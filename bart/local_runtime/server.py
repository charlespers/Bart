"""Subprocess-managed local inference server.

Two server flavors, both expose an OpenAI-compatible HTTP API at
`http://127.0.0.1:<port>/v1/chat/completions`:

  - mlx_lm.server   (Apple Silicon)
  - llama_cpp.server (everywhere else)

We pick a free port, launch the subprocess, poll `/v1/models` until ready
(max 90s — model load can be slow on first launch), and register an
atexit handler so Ctrl-C and unexpected exits don't leave orphan
processes hogging the GPU.
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .cache import model_dir
from .errors import LocalRuntimeError
from .hardware import Platform
from .models import Model


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class Server:
    proc: subprocess.Popen
    base_url: str
    model_id: str          # the model id the server reports (we pass this in /v1/chat)
    log_path: Path
    parallel_slots: int    # how many concurrent requests this server can handle

    def shutdown(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        except OSError:
            pass

    def health_ok(self, timeout_s: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/v1/models", timeout=timeout_s,
            ) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False


def _wait_until_ready(base_url: str, max_wait_s: float, log_path: Path,
                      proc: subprocess.Popen) -> None:
    """Poll /v1/models until 200 or timeout. Reports last log lines on failure."""
    deadline = time.time() + max_wait_s
    last_err: Exception | None = None
    while time.time() < deadline:
        # Detect process death early — no point polling for 90s if the
        # subprocess crashed during model load.
        if proc.poll() is not None:
            tail = _tail_log(log_path)
            raise LocalRuntimeError(
                "SERVER_START",
                f"The local inference server exited unexpectedly "
                f"(exit code {proc.returncode}) before it could serve "
                f"requests.",
                hint=(
                    "This usually means the model is too big for available "
                    "memory, the weight files are corrupted, or the engine "
                    "version doesn't support this model.\n"
                    "  Try `./run cleanup` to clear cached weights and "
                    "let bart pick a smaller model on the next run.\n"
                    f"  Server log tail:\n{tail}"
                ),
            )
        try:
            with urllib.request.urlopen(
                f"{base_url}/v1/models", timeout=2,
            ) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = e
        time.sleep(0.5)
    tail = _tail_log(log_path)
    raise LocalRuntimeError(
        "SERVER_HEALTH",
        f"The local inference server didn't become ready within "
        f"{max_wait_s:.0f}s.",
        hint=(
            "The model may be too large for your machine's RAM. Try a "
            "smaller variant via `./run setup`.\n"
            f"  Server log tail:\n{tail}"
        ),
    )


def _tail_log(log_path: Path, lines: int = 20) -> str:
    try:
        with open(log_path) as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return "(log unavailable)"


def start(model: Model, platform: Platform, *,
          n_ctx: int = 32768,
          parallel_slots: int = 1) -> Server:
    """Start an inference server for `model`. Returns a `Server` handle.

    `n_ctx`: context window the server will allocate (KV cache scales with
        this — bigger = more RAM/VRAM). 32K is the default; the orchestrator
        can request larger up to model.context_window.
    `parallel_slots`: how many simultaneous requests the server can serve.
        mlx-lm currently runs single-slot (≤1); llama-cpp-python supports
        multiple slots if the host has the RAM. Default 1 keeps it safe.
    """
    weights_path = model_dir(model)
    if not weights_path.exists():
        raise RuntimeError(
            f"weights directory missing: {weights_path}. "
            f"Run installer.ensure_weights() first."
        )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = weights_path.parent.parent / f"{model.key}.server.log"
    log_f = open(log_path, "w")

    if model.is_mlx:
        # mlx-lm exposes its server CLI as the `mlx_lm` console script with
        # a `server` subcommand (recent versions deprecate `mlx_lm.server`).
        # The model arg is the path to the snapshot dir.
        cmd = [
            sys.executable, "-m", "mlx_lm", "server",
            "--model", str(weights_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "WARNING",
        ]
        # mlx-lm is single-slot — ignore parallel_slots > 1.
        slots = 1
    else:
        # llama-cpp-python's server CLI lives at `llama_cpp.server`. The
        # `--model` path is the .gguf file.
        gguf_files = list(weights_path.glob("*.gguf"))
        if not gguf_files:
            raise RuntimeError(
                f"no .gguf file found under {weights_path} for {model.key}"
            )
        gguf_path = gguf_files[0]
        cmd = [
            sys.executable, "-m", "llama_cpp.server",
            "--model", str(gguf_path),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--n_ctx", str(n_ctx),
            "--n_gpu_layers", "999",  # ignored on CPU builds
        ]
        if parallel_slots > 1:
            cmd += ["--n_threads", str(max(2, os.cpu_count() or 4))]
        slots = max(1, parallel_slots)

    env = os.environ.copy()
    # Suppress HF tokenizer parallelism warnings spam.
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    proc = subprocess.Popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )

    server = Server(
        proc=proc,
        base_url=base_url,
        # Provisional. Replaced below with the id mlx-lm/llama-cpp actually
        # reports at /v1/models — for mlx-lm that's the absolute path to
        # the snapshot dir, not our model.key, and the chat-completions
        # endpoint rejects requests whose `model` doesn't match.
        model_id=model.key,
        log_path=log_path,
        parallel_slots=slots,
    )

    # Register cleanup BEFORE waiting — if wait_until_ready raises we still
    # want to kill the subprocess.
    atexit.register(server.shutdown)

    try:
        # First-load can be slow: model parsing + Metal/CUDA shader compile.
        # 180s ceiling covers a 32B MLX 4-bit cold-load on M-series Macs
        # plus a small safety margin for slower disks.
        _wait_until_ready(base_url, max_wait_s=180.0, log_path=log_path,
                          proc=proc)
        # Replace the provisional model_id with the server-reported one.
        try:
            with urllib.request.urlopen(
                f"{base_url}/v1/models", timeout=5,
            ) as r:
                models = json.loads(r.read().decode())
                ids = [m.get("id") for m in models.get("data", []) if m.get("id")]
                if ids:
                    server.model_id = ids[0]
        except (urllib.error.URLError, OSError, ValueError):
            pass  # fall back to model.key — not great but not fatal
    except Exception:
        server.shutdown()
        raise

    return server


def stop_all() -> None:
    """No-op safety net — atexit fires per-server."""
    pass
