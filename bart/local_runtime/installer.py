"""Auto-install the inference engine + download model weights.

Two engines, picked by Platform.accelerator:
  - "metal" → mlx-lm       (Apple Silicon native, ~2× faster than llama.cpp)
  - "cuda"  → llama-cpp-python[server]  (with CUDA wheels)
  - "cpu"   → llama-cpp-python[server]  (CPU wheels)

The engine is installed into the *current* Python's pip environment — the
./run script already created and activated a venv in .venv/, so we install
into that venv. Model weights are downloaded via huggingface_hub with
resume support and progress bars.

Both pip-install and HF-download are slow operations. We surface progress
via the `console` callback so the user sees what's happening.

Error policy: every failure raises a `LocalRuntimeError` with a message
that names the problem and tells the user how to fix it. Stack traces
never leak to the user — they go to logs.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .cache import is_downloaded, model_dir, models_dir, touch
from .errors import LocalRuntimeError
from .hardware import Platform
from .models import Model, hf_resolve_url


# --- Engine install ---------------------------------------------------------


def engine_package(platform: Platform) -> str:
    """The pip package the user needs for this platform."""
    if platform.is_apple_silicon:
        return "mlx-lm"
    return "llama-cpp-python[server]"


def engine_module(platform: Platform) -> str:
    """The Python import name to test for engine presence."""
    return "mlx_lm" if platform.is_apple_silicon else "llama_cpp"


def is_engine_installed(platform: Platform) -> bool:
    try:
        importlib.import_module(engine_module(platform))
        return True
    except ImportError:
        return False


def ensure_engine(platform: Platform, console) -> None:
    """Pip-install the engine if missing. No-op when already importable.

    Installs into the current Python's environment (the ./run venv).
    """
    if is_engine_installed(platform):
        return

    pkg = engine_package(platform)
    console.print(
        f"  → installing inference engine [cyan]{pkg}[/cyan] "
        f"[dim](one-time, ~30-60s on a fast network)[/dim]"
    )
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
           "--quiet", "--progress-bar", "off", pkg]
    try:
        subprocess.run(cmd, check=True, timeout=900,
                       capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise LocalRuntimeError(
            "PIP_INSTALL",
            f"Could not install the inference engine "
            f"({pkg}).",
            hint=(
                f"Run this once by hand to see the full error from pip:\n"
                f"     {sys.executable} -m pip install {pkg}\n"
                f"  Common causes: no network, a corporate proxy, or a missing "
                f"compiler toolchain. On macOS, install Xcode command-line "
                f"tools with `xcode-select --install`."
            ),
        ) from e
    except subprocess.TimeoutExpired as e:
        raise LocalRuntimeError(
            "PIP_INSTALL",
            f"Installing {pkg} took longer than 15 minutes.",
            hint=(
                "Your network is probably slow or a wheel build is hung. "
                "Re-run ./run when you have a faster connection, or try "
                f"`{sys.executable} -m pip install {pkg}` directly."
            ),
        ) from e

    if not is_engine_installed(platform):
        raise LocalRuntimeError(
            "ENGINE_IMPORT",
            f"Installed {pkg} but Python still can't import it.",
            hint=(
                "Try removing the .venv directory and re-running ./run from "
                "scratch. If the problem persists, the package may be "
                "incompatible with your Python version "
                f"({sys.version_info.major}.{sys.version_info.minor})."
            ),
        )
    console.print(f"  [green]✓[/green] installed [cyan]{pkg}[/cyan]")


# --- Weights download -------------------------------------------------------


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return usage.free / (1024 ** 3)
    except OSError:
        return 0.0


def validate_repo_reachable(m: Model) -> None:
    """HEAD-check the model's sentinel file on Hugging Face.

    Run this BEFORE the long download so a 404 / 401 / network failure
    surfaces in seconds with an actionable message instead of after the
    user has been waiting 5 minutes for a partial download to fail.

    Uses system curl to side-step macOS Python's missing CA bundle. Falls
    back to permissive (best-effort) when curl is unavailable.
    """
    if shutil.which("curl") is None:
        return
    url = hf_resolve_url(m)
    try:
        out = subprocess.run(
            ["curl", "-sIL", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "10", url],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return  # network probe failed; let the real download surface it
    code = (out.stdout or "").strip()
    if not code.isdigit():
        return
    n = int(code)
    if 200 <= n < 400:
        return
    if n == 404:
        raise LocalRuntimeError(
            "HF_REPO_MISSING",
            f"The model `{m.display_name}` is no longer reachable on "
            f"Hugging Face (got HTTP 404 for {m.hf_repo}).",
            hint=(
                "This is almost certainly because the upstream repo was "
                "renamed or removed. Update bart/local_runtime/models.py "
                "to a new repo, or pick a different model with `./run setup`."
            ),
        )
    if n in (401, 403):
        raise LocalRuntimeError(
            "HF_GATED",
            f"The model `{m.display_name}` requires authentication on "
            f"Hugging Face (got HTTP {n} for {m.hf_repo}).",
            hint=(
                "Either pick a non-gated model with `./run setup`, or set "
                "the HUGGING_FACE_HUB_TOKEN env var with a token that has "
                "access. Free tokens are at huggingface.co/settings/tokens."
            ),
        )
    # Any other status: log warning, let the real download try anyway.


def ensure_weights(m: Model, console) -> Path:
    """Download model weights from Hugging Face into the cache dir.

    Idempotent: returns the local path immediately if files already exist.
    Resumable: huggingface_hub uses Range requests and partial downloads.
    Progress is surfaced via huggingface_hub's tqdm bars (shown directly in
    the user's terminal — no rich.Progress wrapper here because tqdm and
    rich don't share the same line).

    Raises `LocalRuntimeError` with a code on every recoverable failure.
    """
    target = model_dir(m)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise LocalRuntimeError(
            "CACHE_PERMISSION",
            f"Cannot create the model cache directory at {target}.",
            hint=(
                "Set BART_CACHE_DIR to a directory you can write to, or "
                "fix permissions on ~/.cache/."
            ),
        ) from e

    if is_downloaded(m):
        touch(m)
        return target

    # Disk-space precheck. Need bytes_on_disk + 10% safety + 1 GB workspace.
    needed_gb = (m.bytes_on_disk * 1.10 + 1 * 1024**3) / (1024 ** 3)
    free_gb = _disk_free_gb(target)
    if free_gb < needed_gb:
        raise LocalRuntimeError(
            "DISK_FULL",
            f"Not enough free disk space for {m.display_name}: need "
            f"~{needed_gb:.1f} GB, only {free_gb:.1f} GB available at "
            f"{target}.",
            hint=(
                "Free up space (start with ~/Downloads or ~/.cache), or "
                "set BART_CACHE_DIR to a directory on a larger volume "
                "(e.g. an external drive)."
            ),
        )

    # Catch the catalog-drift failure mode early, before the slow download.
    validate_repo_reachable(m)

    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError as e:
        raise LocalRuntimeError(
            "ENGINE_IMPORT",
            "huggingface_hub is not installed.",
            hint=(
                "Re-run ./run to rebuild the venv, or install manually:\n"
                f"     {sys.executable} -m pip install huggingface_hub"
            ),
        ) from e

    console.print(
        f"  → downloading [cyan]{m.display_name}[/cyan] from "
        f"[dim]{m.hf_repo}[/dim] [dim](~{m.bytes_on_disk/1024**3:.1f} GB; "
        f"first run only — cached 24h between runs)[/dim]"
    )

    try:
        # huggingface_hub >=0.23 always resumes whenever possible, so we
        # don't pass resume_download (deprecated in 0.27+).
        if m.format == "mlx" or not m.hf_filenames:
            snapshot_download(
                repo_id=m.hf_repo,
                local_dir=str(target),
            )
        else:
            for fn in m.hf_filenames:
                hf_hub_download(
                    repo_id=m.hf_repo,
                    filename=fn,
                    local_dir=str(target),
                )
    except Exception as e:  # noqa: BLE001 — translate every failure
        # huggingface_hub raises a wide variety: HfHubHTTPError,
        # OSError (disk full mid-download), ConnectionError, etc. We
        # don't want to depend on every internal class — match by name.
        cls = type(e).__name__
        msg = str(e).lower()
        if "no space" in msg or "disk full" in msg or cls == "OSError":
            raise LocalRuntimeError(
                "DISK_FULL",
                f"The disk filled up while downloading {m.display_name}.",
                hint="Free up space and re-run ./run — the download will resume.",
            ) from e
        if "connection" in msg or "timed out" in msg or "ssl" in msg:
            raise LocalRuntimeError(
                "NETWORK",
                "Network failure while downloading model weights.",
                hint=(
                    "Check your internet connection and re-run ./run. "
                    "The download is resumable — bart will pick up where "
                    "it left off."
                ),
            ) from e
        if "401" in msg or "403" in msg or "gated" in msg:
            raise LocalRuntimeError(
                "HF_GATED",
                f"Hugging Face refused the download for {m.hf_repo}.",
                hint=(
                    "Set HUGGING_FACE_HUB_TOKEN with an access token, or "
                    "pick a non-gated model with `./run setup`."
                ),
            ) from e
        raise LocalRuntimeError(
            "NETWORK",
            f"Download of {m.display_name} failed: {e}",
            hint="Re-run ./run. If the problem repeats, try `./run doctor`.",
        ) from e

    if not is_downloaded(m):
        raise LocalRuntimeError(
            "WEIGHTS_INCOMPLETE",
            f"The download of {m.display_name} finished but required files "
            f"are missing under {target}.",
            hint=(
                "Run `./run cleanup` to clear the partial download, then "
                "`./run` again."
            ),
        )

    touch(m)
    console.print(f"  [green]✓[/green] downloaded [cyan]{m.display_name}[/cyan]")
    return target
