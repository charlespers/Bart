"""Anthropic API wrapper: prompt caching, retries, telemetry, streaming."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

import anthropic

from .telemetry import CallRecord, Telemetry


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Thin wrapper over the Anthropic SDK with caching, retries, and disk-cached responses."""

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

    # ------------------------------------------------------------------
    # Disk-cache helpers (skip API call if the same request was already made)
    # ------------------------------------------------------------------
    def _cache_key(self, model: str, system_blocks: list, messages: list, max_tokens: int) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(str(max_tokens).encode())
        h.update(json.dumps(system_blocks, sort_keys=True, default=str).encode())
        h.update(json.dumps(messages, sort_keys=True, default=str).encode())
        return h.hexdigest()[:16]

    def _read_cache(self, key: str) -> str | None:
        if not self._cache_dir:
            return None
        path = self._cache_dir / f"{key}.txt"
        if path.exists():
            return path.read_text()
        return None

    def _write_cache(self, key: str, text: str) -> None:
        if not self._cache_dir:
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / f"{key}.txt").write_text(text)

    # ------------------------------------------------------------------
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
        # Normalize to content blocks
        sys_blocks = [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
        usr_blocks = [{"type": "text", "text": user}] if isinstance(user, str) else list(user)

        key = self._cache_key(model, sys_blocks, [{"role": "user", "content": usr_blocks}], max_tokens)
        if use_disk_cache:
            cached = self._read_cache(key)
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
                    self._write_cache(key, text)
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
