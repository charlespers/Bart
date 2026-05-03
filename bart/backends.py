"""LLM backends.

Two paths to run bart:

  1. AnthropicAPIBackend  — API key from console.anthropic.com. Pay-per-token,
     supports prompt caching, exposes detailed usage telemetry.

  2. ClaudeCodeBackend    — shells out to the `claude` CLI for users who have
     a claude.ai Pro/Max/Team subscription but no API key. No per-token cost
     (covered by the subscription), no prompt caching at the API layer (we
     fall back to disk cache), no usage telemetry.

Both classes expose the same `complete(...)` method so the orchestrator does
not care which one is in use.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import anthropic

from .telemetry import CallRecord, Telemetry


class LLMError(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────────
# Disk-cache helpers shared by both backends
# ─────────────────────────────────────────────────────────────────────

def _cache_key(model: str, system_blocks: list, messages: list, max_tokens: int) -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(str(max_tokens).encode())
    h.update(json.dumps(system_blocks, sort_keys=True, default=str).encode())
    h.update(json.dumps(messages, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def _read_disk_cache(cache_dir: Path | None, key: str) -> str | None:
    if not cache_dir:
        return None
    path = cache_dir / f"{key}.txt"
    if path.exists():
        return path.read_text()
    return None


def _write_disk_cache(cache_dir: Path | None, key: str, text: str) -> None:
    if not cache_dir:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.txt").write_text(text)


# ─────────────────────────────────────────────────────────────────────
# Anthropic API backend (existing path, unchanged behavior)
# ─────────────────────────────────────────────────────────────────────

class AnthropicAPIBackend:
    name = "anthropic-api"

    def __init__(
        self,
        api_key: str,
        telemetry: Telemetry,
        cache_dir: Path | None = None,
        max_retries: int = 4,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._tel = telemetry
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        self._on_event = on_event or (lambda evt, payload: None)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str | list[dict[str, Any]],
        max_tokens: int = 8000,
        label: str = "",
        use_disk_cache: bool = True,
        temperature: float = 1.0,
    ) -> str:
        sys_blocks = [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
        usr_blocks = [{"type": "text", "text": user}] if isinstance(user, str) else list(user)

        key = _cache_key(model, sys_blocks, [{"role": "user", "content": usr_blocks}], max_tokens)
        if use_disk_cache:
            cached = _read_disk_cache(self._cache_dir, key)
            if cached is not None:
                self._on_event("cache_hit", {"label": label, "key": key})
                return cached

        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            t0 = time.time()
            try:
                self._on_event("call_start", {"label": label, "model": model, "attempt": attempt})
                resp = self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=sys_blocks,
                    messages=[{"role": "user", "content": usr_blocks}],
                )
                dt = time.time() - t0
                text = "".join(b.text for b in resp.content if b.type == "text")
                usage = resp.usage
                rec = CallRecord(
                    label=label,
                    model=model,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    duration_s=dt,
                )
                self._tel.record(rec)
                self._on_event(
                    "call_done",
                    {"label": label, "duration_s": dt, "output_tokens": rec.output_tokens, "chars": len(text)},
                )
                if use_disk_cache:
                    _write_disk_cache(self._cache_dir, key, text)
                return text
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError) as e:
                last_err = e
                wait = 2 ** attempt
                self._on_event("retry", {"label": label, "attempt": attempt, "wait_s": wait, "error": str(e)})
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code in (500, 502, 503, 529):
                    last_err = e
                    wait = 2 ** attempt
                    self._on_event("retry", {"label": label, "attempt": attempt, "wait_s": wait, "error": str(e)})
                    time.sleep(wait)
                    continue
                raise LLMError(f"API error on '{label}': {e}") from e
        raise LLMError(f"Exhausted retries on '{label}': {last_err}")


# ─────────────────────────────────────────────────────────────────────
# Claude Code subscription backend (no API key required)
# ─────────────────────────────────────────────────────────────────────

class ClaudeCodeBackend:
    """Shells out to the `claude` CLI installed by Claude Code subscribers.

    Authentication piggybacks on the user's existing `claude` login (handled
    by the CLI itself — no token plumbing in bart). The model selector still
    applies. We flatten content blocks into a single stdin message because the
    CLI doesn't expose `cache_control`.
    """

    name = "claude-code"

    def __init__(
        self,
        telemetry: Telemetry,
        cache_dir: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        cli_path: str | None = None,
        timeout_s: int = 600,
    ):
        self._tel = telemetry
        self._cache_dir = cache_dir
        self._on_event = on_event or (lambda evt, payload: None)
        self._cli = cli_path or shutil.which("claude")
        if not self._cli:
            raise LLMError(
                "The `claude` CLI was not found on PATH. Install Claude Code "
                "from https://claude.ai/code (subscription required) or switch "
                "auth_mode to 'api' and provide an API key."
            )
        self._timeout_s = timeout_s

    @staticmethod
    def is_available() -> bool:
        return shutil.which("claude") is not None

    @staticmethod
    def _flatten(blocks: str | list[dict[str, Any]]) -> str:
        if isinstance(blocks, str):
            return blocks
        out: list[str] = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n\n".join(out)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str | list[dict[str, Any]],
        max_tokens: int = 8000,
        label: str = "",
        use_disk_cache: bool = True,
        temperature: float = 1.0,  # unused — CLI doesn't expose
    ) -> str:
        sys_text = self._flatten(system)
        usr_text = self._flatten(user)

        # Cache key uses the same shape so caches transfer between backends
        # if a user moves from CLI to API or vice versa.
        sys_blocks = [{"type": "text", "text": sys_text}]
        usr_blocks = [{"type": "text", "text": usr_text}]
        key = _cache_key(model, sys_blocks, [{"role": "user", "content": usr_blocks}], max_tokens)
        if use_disk_cache:
            cached = _read_disk_cache(self._cache_dir, key)
            if cached is not None:
                self._on_event("cache_hit", {"label": label, "key": key})
                return cached

        # Combined message — system goes first inside <system> tags, then the user request.
        combined = f"<system_instructions>\n{sys_text}\n</system_instructions>\n\n{usr_text}"

        cmd = [self._cli, "--print", "--model", model, "--output-format", "text"]
        t0 = time.time()
        self._on_event("call_start", {"label": label, "model": model, "backend": "claude-code"})
        try:
            proc = subprocess.run(
                cmd,
                input=combined,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                # Inherit environment so the CLI finds its credentials.
                env={**os.environ},
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"`claude` CLI timed out after {self._timeout_s}s on '{label}'.") from e

        dt = time.time() - t0
        if proc.returncode != 0:
            raise LLMError(
                f"`claude` CLI failed on '{label}' (exit {proc.returncode}). "
                f"stderr: {proc.stderr.strip()[:400]}"
            )
        text = proc.stdout.strip()
        if not text:
            raise LLMError(f"`claude` CLI returned empty output on '{label}'.")

        # Approximate telemetry: chars-as-tokens proxy, no cost.
        approx_in = len(combined) // 4
        approx_out = len(text) // 4
        self._tel.record(CallRecord(
            label=label,
            model=model,
            input_tokens=approx_in,
            output_tokens=approx_out,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            duration_s=dt,
            cost_usd=0.0,  # subscription-covered
        ))
        self._on_event("call_done", {
            "label": label, "duration_s": dt, "chars": len(text), "backend": "claude-code",
        })
        if use_disk_cache:
            _write_disk_cache(self._cache_dir, key, text)
        return text
