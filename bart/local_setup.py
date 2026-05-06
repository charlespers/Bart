"""Local-model lifecycle: Ollama detection, install, hardware probe, model
tier picker, pull/unload/delete with a 24-hour disk cache.

Design notes:
- Cache: `~/.bart_local_cache.json` maps model name → ISO timestamp of last
  use. On every run start we evict any entry older than 24 hours (and run
  `ollama rm` to free disk). On run end we update the timestamp instead of
  deleting, so back-to-back runs reuse the model and don't re-download.
- Ollama keeps loaded weights in VRAM for `OLLAMA_KEEP_ALIVE` (default 5
  min) after the last call. We force an immediate VRAM unload at run end
  via `POST /api/generate {keep_alive: 0}` so the next thing on the user's
  machine has full memory available.
- Hardware probe: macOS reports unified memory via psutil; Linux with an
  NVIDIA GPU prefers VRAM via `nvidia-smi`; everything else falls back to
  system RAM with a CPU-only warning.
- Auto-install: macOS uses `brew install ollama` when Homebrew is present;
  Linux uses the official `curl | sh` script. Either path requires explicit
  user consent. On decline or failure, prints manual instructions and
  aborts local-mode setup.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# --- Cache file ----------------------------------------------------------

CACHE_PATH = Path.home() / ".bart_local_cache.json"
CACHE_TTL = timedelta(hours=24)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


# --- Tier table ----------------------------------------------------------

# (min_memory_gb, primary_model, fast_model, label).
# Probed VRAM (or unified memory on Apple Silicon) is the ceiling — we pick
# the largest tier the machine can run. fast_model is intentionally tiny
# so the per-day Researcher / brief / sidecar calls stay fast.
TIERS: list[tuple[float, str, str, str]] = [
    (40.0, "gemma4:27b", "gemma4:1b", "27B (high-end, ~40GB)"),
    (18.0, "gemma4:12b", "gemma4:1b", "12B (mid-range, ~18GB)"),
    (6.0,  "gemma4:4b",  "gemma4:1b", "4B  (laptop, ~6GB)"),
    (0.0,  "gemma4:1b",  "gemma4:1b", "1B  (CPU / minimal RAM)"),
]


def pick_tier(memory_gb: float) -> tuple[str, str, str]:
    """Returns (primary_model, fast_model, label) for the given memory size."""
    for floor, primary, fast, label in TIERS:
        if memory_gb >= floor:
            return primary, fast, label
    return TIERS[-1][1], TIERS[-1][2], TIERS[-1][3]


# --- Hardware probe ------------------------------------------------------


def probe_memory_gb() -> tuple[float, str]:
    """Returns (memory_gb, source) where source is 'vram' / 'unified' / 'system'.

    Mac unified memory shows up as system RAM via psutil — we mark it
    'unified' so the caller can label tiers correctly. NVIDIA VRAM is
    preferred on Linux when present.
    """
    sysname = platform.system()
    if sysname == "Linux":
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if out.returncode == 0 and out.stdout.strip():
                first_line = out.stdout.strip().splitlines()[0].strip()
                mb = float(first_line)
                if mb > 0:
                    return mb / 1024.0, "vram"
        except (FileNotFoundError, subprocess.SubprocessError, ValueError):
            pass

    try:
        import psutil
        avail = psutil.virtual_memory().available
        gb = avail / (1024 ** 3)
        return gb, ("unified" if sysname == "Darwin" else "system")
    except ImportError:
        # psutil not installed — fall back to /proc/meminfo on Linux,
        # otherwise return a conservative default.
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2), "system"
        except (FileNotFoundError, OSError):
            pass
        return 4.0, "system"


def has_gpu_or_unified() -> bool:
    sysname = platform.system()
    if sysname == "Darwin":
        return True  # Apple Silicon GPUs share unified memory; fine for ollama
    try:
        out = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=2,
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


# --- Ollama HTTP -------------------------------------------------------


def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2).read()
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def ollama_list_models() -> list[str]:
    """Returns list of model names currently installed on the local Ollama."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=4) as r:
            data = json.loads(r.read().decode())
        return [m["name"] for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return []


def ollama_pull(model: str, on_progress=None) -> None:
    """Pull a model. Streams progress lines if `on_progress(text)` is provided."""
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/pull",
        data=json.dumps({"name": model, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_status = ""
    with urllib.request.urlopen(req, timeout=None) as r:
        for line in r:
            try:
                evt = json.loads(line.decode())
            except json.JSONDecodeError:
                continue
            status = evt.get("status", "")
            if status and status != last_status:
                last_status = status
                if on_progress:
                    on_progress(status)
            if evt.get("error"):
                raise RuntimeError(f"ollama pull failed: {evt['error']}")


def ollama_delete(model: str) -> None:
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/delete",
        data=json.dumps({"name": model}).encode(),
        headers={"Content-Type": "application/json"},
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return  # already gone
        raise


def ollama_unload(model: str) -> None:
    """Force-unload a model from VRAM via `keep_alive: 0`."""
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=json.dumps({"model": model, "prompt": "", "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except (urllib.error.URLError, OSError):
        pass  # Best effort — VRAM frees on its own after keep-alive timeout.


# --- Cache management ---------------------------------------------------


def _read_cache() -> dict[str, str]:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_cache(cache: dict[str, str]) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_is_fresh(model: str) -> bool:
    """True if the cache says this model was used within the last 24 h."""
    cache = _read_cache()
    ts = cache.get(model)
    if not ts:
        return False
    try:
        last_used = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_used < CACHE_TTL


def cache_touch(model: str) -> None:
    cache = _read_cache()
    cache[model] = _now_iso()
    _write_cache(cache)


def cache_evict_stale(on_evict=None) -> list[str]:
    """Remove cache entries older than 24 h and `ollama rm` the corresponding
    models. Returns the list of evicted model names."""
    cache = _read_cache()
    if not cache:
        return []
    now = datetime.now(timezone.utc)
    keep: dict[str, str] = {}
    evicted: list[str] = []
    for model, ts in cache.items():
        try:
            last_used = datetime.fromisoformat(ts)
        except ValueError:
            evicted.append(model)
            continue
        if now - last_used >= CACHE_TTL:
            evicted.append(model)
        else:
            keep[model] = ts
    for model in evicted:
        try:
            ollama_delete(model)
        except Exception:
            pass
        if on_evict:
            on_evict(model)
    _write_cache(keep)
    return evicted


def purge_all_cached() -> list[str]:
    """Force-delete every model the cache file knows about. Used by
    `./run cleanup`. Returns the list of models removed."""
    cache = _read_cache()
    removed: list[str] = []
    for model in list(cache.keys()):
        try:
            ollama_unload(model)
        except Exception:
            pass
        try:
            ollama_delete(model)
            removed.append(model)
        except Exception:
            pass
    _write_cache({})
    return removed


# --- Auto-install -------------------------------------------------------


_INSTALL_INSTRUCTIONS = (
    "  manual install:\n"
    "    macOS:   brew install ollama   OR   download from https://ollama.com\n"
    "    Linux:   curl -fsSL https://ollama.com/install.sh | sh\n"
    "  then start the daemon:\n"
    "    ollama serve   (or open the macOS app once)\n"
)


def install_with_consent(prompt_yes_no) -> bool:
    """Try to install ollama. `prompt_yes_no(question) -> bool` is the
    consent callback (so callers can plug in Rich's Confirm.ask).
    Returns True on success, False otherwise."""
    sysname = platform.system()
    if sysname == "Darwin":
        if shutil.which("brew") is None:
            return False
        if not prompt_yes_no("install ollama via `brew install ollama`?"):
            return False
        try:
            subprocess.run(["brew", "install", "ollama"], check=True, timeout=900)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        return shutil.which("ollama") is not None
    if sysname == "Linux":
        if not prompt_yes_no(
            "install ollama via `curl -fsSL https://ollama.com/install.sh | sh`?"
        ):
            return False
        try:
            subprocess.run(
                "curl -fsSL https://ollama.com/install.sh | sh",
                shell=True, check=True, timeout=900,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        return shutil.which("ollama") is not None
    return False


def manual_install_message() -> str:
    return _INSTALL_INSTRUCTIONS


# --- High-level flow ----------------------------------------------------


def ensure_ready(primary_model: str, fast_model: str, console) -> None:
    """Pulls the primary + fast models if not cached. Updates cache
    timestamps. Raises RuntimeError on unrecoverable failure.

    Call this at the start of an Orchestrator run."""
    if not ollama_running():
        # Try `ollama serve` in the background — best-effort. The daemon
        # might be installed but not running.
        try:
            subprocess.Popen(
                ["ollama", "serve"], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            for _ in range(10):
                time.sleep(0.5)
                if ollama_running():
                    break
        except (FileNotFoundError, OSError):
            pass
        if not ollama_running():
            raise RuntimeError(
                f"ollama daemon is not reachable at {OLLAMA_HOST}. "
                "Start it with `ollama serve` and re-run."
            )

    cache_evict_stale(on_evict=lambda m: console.print(
        f"  [dim]✓ evicted stale cached model {m} (>24h since last use)[/dim]"
    ))
    installed = set(ollama_list_models())

    for model in {primary_model, fast_model}:
        if cache_is_fresh(model) and any(model == n or n.startswith(model + ":")
                                          for n in installed):
            console.print(f"  [dim]✓ reused cached model[/dim] [cyan]{model}[/cyan] "
                          f"[dim](<24h since last use)[/dim]")
            cache_touch(model)
            continue
        console.print(f"  → pulling [cyan]{model}[/cyan] [dim](first run / cache "
                      f"expired — may take several minutes)[/dim]")
        try:
            last_status = [""]
            def _on_progress(s: str) -> None:
                if s != last_status[0]:
                    last_status[0] = s
                    console.print(f"    [dim]{s}[/dim]")
            ollama_pull(model, on_progress=_on_progress)
        except Exception as e:
            raise RuntimeError(f"failed to pull {model}: {e}") from e
        cache_touch(model)
        console.print(f"  [green]✓[/green] pulled [cyan]{model}[/cyan]")


def end_of_run(primary_model: str, fast_model: str, console) -> None:
    """Refresh cache timestamps and unload from VRAM. Called from the
    Orchestrator's `finally` so Ctrl-C still cleans up."""
    for model in {primary_model, fast_model}:
        cache_touch(model)
        try:
            ollama_unload(model)
        except Exception:
            pass
    console.print("  [dim]✓ unloaded local model(s) from VRAM (24h cache "
                  "kept for fast next run; `./run cleanup` to purge)[/dim]")
