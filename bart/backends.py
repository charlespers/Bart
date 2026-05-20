"""LLM backends.

Three paths to run bart:

  1. AnthropicAPIBackend  - API key from console.anthropic.com. Pay-per-token,
     supports prompt caching, exposes detailed usage telemetry.

  2. ClaudeCodeBackend    - shells out to the `claude` CLI for users who have
     a claude.ai Pro/Max/Team subscription but no API key. No per-token cost
     (covered by the subscription), no prompt caching at the API layer (we
     fall back to disk cache), no usage telemetry.

  3. LocalBackend         - runs an open-weight model (Qwen3 family) on the
     local machine via mlx-lm (Apple Silicon) or llama-cpp-python (CUDA /
     CPU). Zero per-token cost, no API dependency. See
     `bart/local_runtime/SPEC.md` for the lifecycle contract.

All three classes expose the same `complete(...)` method so the orchestrator
does not care which one is in use.

Hanging vs slow: the Claude Code CLI is invoked with `--print --output-format
stream-json --include-partial-messages`, so it emits one JSON event per line —
including text-delta `stream_event`s as the response is produced — instead of
buffering the whole reply into a single trailing `assistant` event. We parse
those events for live progress (a real, climbing char count); a read-loop
watchdog kills the subprocess and raises if it goes silent for
`_CLI_IDLE_TIMEOUT_S` (so a wedged connection doesn't hang the run). A
heartbeat thread also fires `heartbeat` events every 15s while the subprocess
is alive — long lessons can still take 4-6 min on Opus, and the orchestrator
surfaces these so the user knows it's progressing rather than hung.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import anthropic

from .telemetry import CallRecord, Telemetry


class LLMError(RuntimeError):
    pass


class LLMContextTooLongError(LLMError):
    """The request exceeded the model's input-context window.

    Raised by every backend when the underlying provider reports a
    prompt-too-long / context-length-exceeded error. Callers can catch this
    specifically and reach for map-reduce or a longer-context model rather
    than retrying blindly.
    """


# ─────────────────────────────────────────────────────────────────────
# ConcurrencyGuard — per-model semaphores to keep parallel fan-out under
# the provider's per-model rate limits. Anthropic enforces RPM and ITPM
# *per model*, not per account, so a global max_parallel is the wrong
# unit. Cap concurrency by model tier instead. Tier 2 (most users):
# Opus 50 RPM, Sonnet 1000 RPM, Haiku 4000 RPM. Concurrency caps below
# leave plenty of headroom for retries even at sustained throughput.
# ─────────────────────────────────────────────────────────────────────

# Default per-model concurrency caps. Conservative defaults that fit
# inside Anthropic Tier-2 rate limits even with truncation/block-fix
# retries firing. Power users can lift via env.
_DEFAULT_CONCURRENCY = {
    "opus":   2,
    "sonnet": 4,
    "haiku":  8,
}


def _model_tier(model: str) -> str | None:
    """Map a model id to its tier key. Returns None for non-tiered models
    (local Gemma, anything we don't recognise) — those get no cap."""
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return None


class ConcurrencyGuard:
    """Per-model concurrency limiter shared across all threads.

    The orchestrator runs four overlapping ThreadPoolExecutors (sidecars,
    top-level artifacts, daily lessons, optional researcher prefetch)
    that each issue API calls without coordination. Without a single
    chokepoint, parallel fan-out can launch 10+ concurrent calls to the
    same model and trigger 429 storms. This class is that chokepoint:
    `with guard.acquire(model):` blocks until a slot is free.

    The cap is per *tier* (opus/sonnet/haiku), since Anthropic's rate
    limits are per-model — Sonnet calls don't slow Opus calls down.
    Models we don't recognise (e.g. local Gemma) acquire instantly.
    """

    def __init__(self, on_event: Callable[[str, dict[str, Any]], None] | None = None):
        self._on_event = on_event or (lambda evt, payload: None)
        self._sems: dict[str, threading.BoundedSemaphore] = {}
        self._caps: dict[str, int] = {}
        for tier, default in _DEFAULT_CONCURRENCY.items():
            cap = int(os.environ.get(f"BART_MAX_CONCURRENT_{tier.upper()}", default))
            cap = max(1, cap)
            self._caps[tier] = cap
            self._sems[tier] = threading.BoundedSemaphore(cap)

    def cap_for(self, model: str) -> int | None:
        tier = _model_tier(model)
        return self._caps.get(tier) if tier else None

    @contextmanager
    def acquire(self, model: str, *, label: str = ""):
        tier = _model_tier(model)
        sem = self._sems.get(tier) if tier else None
        if sem is None:
            yield
            return
        # Try non-blocking first so we can emit a wait event only when we
        # actually queue. Blocking wait is otherwise invisible.
        if not sem.acquire(blocking=False):
            t0 = time.time()
            self._on_event("rate_limit_wait", {
                "label": label, "model": model, "tier": tier, "cap": self._caps[tier],
            })
            sem.acquire()
            self._on_event("rate_limit_resumed", {
                "label": label, "model": model, "tier": tier,
                "waited_s": round(time.time() - t0, 2),
            })
        try:
            yield
        finally:
            sem.release()


# Process-wide singleton. All backends share this so concurrent callers
# from different code paths (sidecars, daily Authors, etc.) actually
# share the cap.
_GLOBAL_GUARD: ConcurrencyGuard | None = None
_GLOBAL_GUARD_LOCK = threading.Lock()


def get_concurrency_guard(
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> ConcurrencyGuard:
    global _GLOBAL_GUARD
    with _GLOBAL_GUARD_LOCK:
        if _GLOBAL_GUARD is None:
            _GLOBAL_GUARD = ConcurrencyGuard(on_event=on_event)
        return _GLOBAL_GUARD


# Per-model input context window (tokens). Used for preventative size checks
# before sending — if estimated input > 0.85 * window, callers should
# map-reduce or promote to a longer-context model rather than fail.
# Source: https://docs.claude.com/en/docs/about-claude/models — and Gemma's
# own published 32K/128K limits.
MODEL_CTX_WINDOW: dict[str, int] = {
    # Claude — 200K standard, 1M for the Opus-4-7-1m variant
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-7": 200_000,
    "claude-opus-4-7-1m": 1_000_000,
    # Local Qwen3 family — canonical limits. Mirrors
    # bart/local_runtime/models.py's per-model `context_window`.
    "qwen3-4b-mlx-4bit":      131_072,
    "qwen3-4b-gguf-q4km":     131_072,
    "qwen3-8b-mlx-4bit":      131_072,
    "qwen3-8b-gguf-q4km":     131_072,
    "qwen3-14b-mlx-4bit":     131_072,
    "qwen3-14b-gguf-q4km":    131_072,
    "qwen3-30b-a3b-mlx-4bit": 262_144,
    "qwen3-30b-a3b-gguf-q4km": 262_144,
    "qwen3-32b-mlx-4bit":     131_072,
    "qwen3-32b-gguf-q4km":    131_072,
    # Local Gemma 4 family — 128K context (32K on the 1B variant). Mirrors
    # bart/local_runtime/models.py's per-model `context_window`.
    "gemma4-1b-mlx-4bit":     32_768,
    "gemma4-1b-gguf-q4km":    32_768,
    "gemma4-4b-mlx-4bit":     131_072,
    "gemma4-4b-gguf-q4km":    131_072,
    "gemma4-12b-mlx-4bit":    131_072,
    "gemma4-12b-gguf-q4km":   131_072,
    "gemma4-27b-mlx-4bit":    131_072,
    "gemma4-27b-gguf-q4km":   131_072,
}


def model_ctx_window(model: str) -> int:
    """Return the input-context limit for `model`, defaulting to 200K when
    unknown. Conservative default keeps callers from mis-estimating headroom
    on a model we haven't catalogued."""
    return MODEL_CTX_WINDOW.get(model, 200_000)


# Substrings the providers use to indicate input-too-long. Lowercased; we
# match against the lowercased error/stderr text.
_CONTEXT_TOO_LONG_SIGNALS = (
    "prompt is too long",
    "prompt too long",
    "context length",
    "context_length_exceeded",
    "maximum context length",
    "input is too long",
    "exceeds the context window",
    "exceeds context window",
    "exceeds maximum",
    "too many tokens",
)


def _looks_like_context_too_long(text: str) -> bool:
    if not text:
        return False
    s = text.lower()
    return any(sig in s for sig in _CONTEXT_TOO_LONG_SIGNALS)


def _retry_after_seconds(err: Exception) -> float | None:
    """Extract the server-supplied retry-after hint from an Anthropic error.

    Anthropic returns retry-after-ms (preferred, more precise) and/or
    retry-after (seconds, integer) on 429 responses. Returns the wait
    in seconds, or None if neither header is present or parseable.
    """
    response = getattr(err, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    try:
        get = headers.get
    except AttributeError:
        return None
    raw_ms = get("retry-after-ms") or get("Retry-After-Ms")
    if raw_ms:
        try:
            return float(raw_ms) / 1000.0
        except (TypeError, ValueError):
            pass
    raw_s = get("retry-after") or get("Retry-After")
    if raw_s:
        try:
            return float(raw_s)
        except (TypeError, ValueError):
            pass
    return None


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _jittered(seconds: float) -> float:
    """Apply ±25% random jitter to a backoff/Retry-After wait.

    When several threads hit a 429 at the same moment they otherwise back off
    in lockstep and collide again on the retry; spreading the waits avoids
    that synchronized thundering herd.
    """
    return seconds * random.uniform(0.75, 1.25)


# ─────────────────────────────────────────────────────────────────────
# Output sanitizer for local models
# ─────────────────────────────────────────────────────────────────────

import re as _re_san


_GEMMA_PREAMBLES = [
    _re_san.compile(p, _re_san.IGNORECASE) for p in (
        r"^\s*(?:sure|certainly|absolutely|of course|here you go)[^\n]*\n+",
        r"^\s*here(?:'s| is)\s+(?:the|your|a)\b[^\n]*\n+",
        r"^\s*(?:i(?:'ll| will)|let me)\s+(?:write|create|generate|provide|produce)[^\n]*\n+",
        r"^\s*okay\s*[,!.]?\s*\n+",
        r"^\s*<think>[\s\S]*?</think>\s*",   # gemma3 think-mode tags, when on
        r"^\s*<thinking>[\s\S]*?</thinking>\s*",
        # Gemma 4 thinking-mode artifacts. The new arch emits
        # `<|channel>thought\n...<channel|>` blocks when thinking is
        # active; strip the entire block so only the final answer remains.
        # We don't enable thinking by default, but the model occasionally
        # emits these tokens spontaneously on small variants.
        r"^\s*<\|channel>\s*thought[\s\S]*?<channel\|>\s*",
        r"^\s*<\|think\|>[\s\S]*?<\|/think\|>\s*",
    )
]
_GEMMA_POSTAMBLES = [
    _re_san.compile(p, _re_san.IGNORECASE | _re_san.DOTALL) for p in (
        # "I hope this helps" / "hope that helps" / "hope this is helpful"
        r"\n+\s*(?:i\s+)?hope\s+(?:this|that)\s+(?:helps|is helpful)[^\n]*$",
        r"\n+\s*(?:let me know|feel free|please reach out)[^\n]*$",
        r"\n+\s*(?:happy|good)\s+(?:studying|learning|exam)[^\n]*$",
    )
]
# Outer code-fence wrappers like ```markdown ... ``` that the model adds
# around the entire artifact. We strip them only when they wrap the WHOLE
# output (not when they're a legitimate code block within the artifact).
_OUTER_FENCE_RE = _re_san.compile(
    r"\A\s*```(?:markdown|md|html)?\s*\n([\s\S]*?)\n```\s*\Z",
    _re_san.MULTILINE,
)


def _input_overlap_ratio(output: str, input_text: str, *, chunk: int = 60) -> float:
    """Estimate how much of `output` is literally copied from `input_text`.

    Slides a `chunk`-character window across the output and counts how many
    starting positions match a contiguous substring of the input. Returns a
    value in [0.0, 1.0]. 0.0 means no overlap; 0.4 means roughly 40% of
    the output is contiguously borrowed from the input — a reliable signal
    that a small model is regurgitating prompt rather than reasoning.

    Much more accurate than hardcoded anchor strings, which produce false
    positives on legitimate outputs that quote single header words.
    """
    if not output or not input_text:
        return 0.0
    out = output.strip()
    if len(out) < chunk:
        chunk = max(20, len(out) // 2)
    # Sample up to 200 evenly-spaced windows to keep this O(1) regardless
    # of artifact length.
    n_windows = min(200, max(1, len(out) - chunk + 1))
    step = max(1, (len(out) - chunk) // n_windows) if len(out) > chunk else 1
    hits = 0
    total = 0
    for i in range(0, max(1, len(out) - chunk + 1), step):
        window = out[i:i + chunk]
        total += 1
        if window in input_text:
            hits += 1
    return hits / max(total, 1)


def _sanitize_local_output(text: str, response_format: str | None = None) -> str:
    """Strip chatter that small local models (Gemma 3, especially) wrap
    around their actual output. Idempotent: safe to call repeatedly.

    For `response_format="json"` callers, also extracts JSON from inside
    leading/trailing prose if Ollama's json-mode hiccups."""
    original = text

    # 1. Outer fence (```markdown\n...\n```) wrapping the entire artifact.
    m = _OUTER_FENCE_RE.match(text)
    if m:
        text = m.group(1)

    # 2. Preamble strip — repeat once to handle "Sure!\n\nHere is the lesson:"
    for _ in range(2):
        for pat in _GEMMA_PREAMBLES:
            text = pat.sub("", text)

    # 3. Postamble strip.
    for pat in _GEMMA_POSTAMBLES:
        text = pat.sub("", text)

    # 4. JSON-mode: try to surface a JSON object/array even when wrapped.
    if response_format == "json":
        s = text.strip()
        # If it doesn't start with `{` or `[`, look for the first one.
        if s and s[0] not in "{[":
            for opener, closer in (("{", "}"), ("[", "]")):
                start = s.find(opener)
                if start < 0:
                    continue
                # Walk braces honoring strings & escapes to find balanced close.
                depth, in_str, esc, end = 0, False, False, -1
                for i, ch in enumerate(s[start:], start):
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if ch == '"':
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if ch == opener:
                        depth += 1
                    elif ch == closer:
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    text = s[start:end]
                    break

    text = text.strip()
    return text or original  # never return blank — fall back to original


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
        # Per-model concurrency cap. Shared across all backend instances and
        # threads so concurrent code paths (sidecars, daily Authors, etc.)
        # share the same Opus/Sonnet/Haiku slots.
        self._guard = get_concurrency_guard(on_event=self._on_event)

    # Beta header that unlocks the 1-hour cache TTL. Including it on every
    # request is free when no block uses ttl=1h. Required so long-lived
    # blocks (system prompts, corpus, brief) survive across multi-step runs
    # that take longer than the default 5-minute cache window.
    _BETA_HEADERS = {"anthropic-beta": "extended-cache-ttl-2025-04-11"}

    @staticmethod
    def _cacheable_system(s: str) -> list[dict[str, Any]]:
        """Wrap a system prompt as a single cacheable block with 1h TTL.

        System prompts are stable across all calls in a run (one per agent
        role). Anthropic supports up to 4 cache breakpoints per request —
        this is one of them. Brief + corpus (in user message) are the others.
        We use the 1h TTL so a 30-minute run doesn't pay cache-write twice.
        """
        return [{
            "type": "text",
            "text": s,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }]

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
        response_format: str | None = None,  # "json" honored by LocalBackend; ignored here
        effort: str | None = None,  # honored only by ClaudeCodeBackend; ignored here
    ) -> str:
        sys_blocks = self._cacheable_system(system) if isinstance(system, str) else list(system)
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
                with self._guard.acquire(model, label=label):
                    resp = self._client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=sys_blocks,
                        messages=[{"role": "user", "content": usr_blocks}],
                        extra_headers=self._BETA_HEADERS,
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
            except anthropic.RateLimitError as e:
                # 429 — honor server-supplied retry hint when present.
                # Anthropic returns retry-after (seconds) and/or
                # retry-after-ms; either header lets us back off precisely
                # instead of guessing with 2^attempt. ±25% jitter so
                # concurrent threads don't retry in lockstep; cap at 60s so a
                # misconfigured upstream can't deadlock the run.
                last_err = e
                wait = min(_jittered(_retry_after_seconds(e) or (2 ** attempt)), 60.0)
                self._on_event("retry", {
                    "label": label, "attempt": attempt, "wait_s": wait,
                    "error": str(e), "reason": "rate_limit",
                })
                time.sleep(wait)
            except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
                last_err = e
                wait = _jittered(2 ** attempt)
                self._on_event("retry", {"label": label, "attempt": attempt, "wait_s": wait, "error": str(e)})
                time.sleep(wait)
            except anthropic.BadRequestError as e:
                # 400-class. Detect prompt-too-long specifically so callers
                # can chunk or promote to a larger-context model. Anything
                # else (malformed request, unsupported field) is fatal.
                msg = str(e)
                if _looks_like_context_too_long(msg):
                    raise LLMContextTooLongError(
                        f"Input exceeds context window on '{label}' "
                        f"(model={model}): {msg}"
                    ) from e
                raise LLMError(f"API error on '{label}': {e}") from e
            except anthropic.APIStatusError as e:
                if e.status_code in (500, 502, 503, 529):
                    last_err = e
                    wait = _jittered(2 ** attempt)
                    self._on_event("retry", {"label": label, "attempt": attempt, "wait_s": wait, "error": str(e)})
                    time.sleep(wait)
                    continue
                if e.status_code == 400 and _looks_like_context_too_long(str(e)):
                    raise LLMContextTooLongError(
                        f"Input exceeds context window on '{label}' "
                        f"(model={model}): {e}"
                    ) from e
                raise LLMError(f"API error on '{label}': {e}") from e
        raise LLMError(f"Exhausted retries on '{label}': {last_err}")


# ─────────────────────────────────────────────────────────────────────
# Claude Code subscription backend (no API key required)
# ─────────────────────────────────────────────────────────────────────

# The `claude --print` subprocess must NOT inherit the user's global
# ~/.claude/settings.json. That file commonly carries `effortLevel` (a value
# like "xhigh"/"max" makes the model spend *minutes* on hidden "thinking"
# before emitting a single character of each artifact — the classic "every
# artifact takes 15-20 min then times out" bug), plus `enabledPlugins` whose
# SessionStart hooks / skills push the subprocess into an agentic loop instead
# of one-shotting the artifact. We pin every relevant knob explicitly on the
# command line. Regression test: tests/test_claude_cli_isolation.py.
_VALID_CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_CLAUDE_EFFORT = os.environ.get("BART_CLAUDE_EFFORT", "medium").strip().lower()
if _CLAUDE_EFFORT not in _VALID_CLAUDE_EFFORTS:
    _CLAUDE_EFFORT = "medium"

# CLI timeouts (seconds). `_CLI_TIMEOUT_S` is the absolute deadline for one
# `claude` call; `_CLI_IDLE_TIMEOUT_S` is the longest the stream-json read
# loop will wait for the *next* line before deciding the subprocess is wedged
# and killing it. The idle watchdog is the one that matters in practice — a
# subprocess whose connection dies (laptop sleep, network blip) goes silent
# but doesn't exit, so without it bart blocks for the full overall timeout.
#
# Why 300s and not less: the `claude` CLI does its *own* rate-limit backoff
# on a 429 (subscription mode hits these readily under parallel fan-out), and
# during that backoff it emits nothing on stdout — a perfectly healthy call
# that will recover. At 150s the watchdog killed those mid-backoff and turned
# a slow success into a hard failure (every daily lesson, observed). Any JSON
# event (ping, thinking delta, text delta) resets the timer, so 300s of *true*
# silence is still a strong "this connection is dead" signal — we just stopped
# being trigger-happy about transient backoffs. Detecting a genuinely-dead
# subprocess now takes up to 5 min instead of 2.5 — an acceptable trade for
# not nuking working calls.
_CLI_TIMEOUT_S = int(os.environ.get("BART_CLI_TIMEOUT_S", "600"))
_CLI_IDLE_TIMEOUT_S = int(os.environ.get("BART_CLI_IDLE_TIMEOUT_S", "300"))


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
        # Subscription mode shares the same per-model concurrency caps —
        # Claude Code subscriptions enforce per-model rate limits too, and
        # they're typically tighter than the API tier limits.
        self._guard = get_concurrency_guard(on_event=self._on_event)

    @staticmethod
    def is_available() -> bool:
        return shutil.which("claude") is not None

    def _run_streaming(
        self, cmd: list[str], combined: str, label: str, env: dict[str, str]
    ) -> tuple[str, str, int]:
        """Run the CLI in stream-json mode, parsing events for live progress.

        Returns (stdout_text, stderr, returncode) where stdout_text is the
        concatenated assistant text from the stream events.

        A reader thread iterates `proc.stdout` line-by-line and pushes each
        line onto a queue (a `None` sentinel on EOF); a second thread drains
        `proc.stderr` into a list so it's available even if the subprocess
        hangs. The main loop pulls from the queue with `_CLI_IDLE_TIMEOUT_S`
        timeout — if the subprocess goes silent (its connection died, etc.)
        we kill it and raise `LLMError` rather than blocking for the full
        overall timeout. An absolute `_CLI_TIMEOUT_S` deadline is enforced
        on top.
        """
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, bufsize=1,
        )
        # Send the input and close stdin so the CLI starts streaming.
        try:
            assert proc.stdin is not None
            proc.stdin.write(combined)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

        idle_timeout = _CLI_IDLE_TIMEOUT_S
        deadline = time.monotonic() + _CLI_TIMEOUT_S

        # ── Reader threads ───────────────────────────────────────────
        # stdout: one line per queue item; a None sentinel marks EOF.
        # stderr: collected into a list so it's readable on a hang
        # (the old code only read stderr after `proc.wait()`, so a wedged
        # subprocess's diagnostics were invisible).
        line_q: "queue.Queue[str | None]" = queue.Queue()
        stderr_chunks: list[str] = []

        def _pump_stdout() -> None:
            try:
                if proc.stdout is not None:
                    for raw in proc.stdout:
                        line_q.put(raw)
            except Exception:  # noqa: BLE001 — best-effort; EOF/closed pipe
                pass
            finally:
                line_q.put(None)

        def _pump_stderr() -> None:
            try:
                if proc.stderr is not None:
                    data = proc.stderr.read()
                    if data:
                        stderr_chunks.append(data)
            except Exception:  # noqa: BLE001
                pass

        t_out = threading.Thread(target=_pump_stdout, daemon=True)
        t_err = threading.Thread(target=_pump_stderr, daemon=True)
        t_out.start()
        t_err.start()

        accumulated_text: list[str] = []
        saw_text_delta = False  # did this stream use --include-partial-messages?
        last_emit_chars = 0
        last_emit_time = time.time()

        def _stderr_text() -> str:
            return "".join(stderr_chunks).strip()

        while True:
            # Absolute deadline guard — independent of per-line idleness.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                t_out.join(timeout=2)
                raise LLMError(
                    f"`claude` exceeded its {_CLI_TIMEOUT_S}s overall timeout "
                    f"on '{label}' (stderr: {_stderr_text()[:400] or 'empty'})"
                )
            try:
                line = line_q.get(timeout=min(idle_timeout, max(0.1, remaining)))
            except queue.Empty:
                # The subprocess produced no output for `idle_timeout`
                # seconds. It's wedged (dead connection, hung mid-think) —
                # kill it and surface stderr.
                proc.kill()
                t_out.join(timeout=2)
                raise LLMError(
                    f"`claude` produced no output for {idle_timeout}s on "
                    f"'{label}' (stderr: {_stderr_text()[:400] or 'empty'})"
                )
            if line is None:
                # EOF — stdout closed.
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Some CLI versions print preamble lines that aren't JSON;
                # treat as plain text and append.
                accumulated_text.append(line)
                continue
            # stream-json event shapes (Anthropic Messages API style):
            #   {"type": "stream_event", "event": {"type": "content_block_delta",
            #       "index": 0, "delta": {"type": "text_delta", "text": "foo"}}}
            #     ↑ emitted with --include-partial-messages, as text is produced
            #   {"type": "assistant", "message": {"content": [{"type":"text","text":"..."}]}}
            #     ↑ a full-message snapshot — older CLI / non-partial fallback
            #   {"type": "result", "subtype": "success", "result": "<full text>", ...}
            #     ↑ final, authoritative — overrides whatever we accumulated
            etype = event.get("type", "")
            if etype == "stream_event":
                # Text deltas from --include-partial-messages. Be defensive
                # about the nested shape — ping/message_start/content_block_*
                # events and non-text deltas (input_json_delta, thinking) all
                # arrive here too and must be ignored, not crash the loop.
                inner = event.get("event") or {}
                if isinstance(inner, dict) and inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        txt = delta.get("text", "")
                        if txt:
                            accumulated_text.append(txt)
                            saw_text_delta = True
            elif etype == "assistant":
                # Full-message snapshot. With --include-partial-messages the CLI
                # still emits this at end-of-turn carrying the *complete* text —
                # which we've already built from deltas — so treat it as a
                # replacement, not an addition (otherwise the text doubles).
                # Without deltas (older CLI / non-partial path) it's the only
                # source of text, so append as before.
                msg = event.get("message", {}) or {}
                snapshot = [
                    block.get("text", "")
                    for block in (msg.get("content") or [])
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text", "")
                ]
                if snapshot:
                    if saw_text_delta:
                        accumulated_text = list(snapshot)
                    else:
                        accumulated_text.extend(snapshot)
            elif etype == "result":
                # Final event — `result` field contains the complete text
                # (or the error message, when subtype indicates failure
                # like "error_max_tokens"). Capture it unconditionally so
                # downstream detection can match on the message; the
                # subprocess returncode tells us success vs. failure.
                full = event.get("result", "")
                if full:
                    accumulated_text = [full]
            # Periodic progress event so the orchestrator can show
            # live char counts.
            now = time.time()
            cur_chars = sum(len(t) for t in accumulated_text)
            if (cur_chars - last_emit_chars) >= 500 or (now - last_emit_time) >= 5:
                self._on_event("stream_progress", {
                    "label": label, "chars": cur_chars,
                })
                last_emit_chars = cur_chars
                last_emit_time = now

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise

        # stderr reader thread should be done now that the process exited.
        t_err.join(timeout=2)
        stderr = _stderr_text()

        return "".join(accumulated_text), stderr, proc.returncode

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
        response_format: str | None = None,  # ignored; CLI doesn't expose
        effort: str | None = None,  # per-call override of the pinned --effort
    ) -> str:
        # The CLI doesn't expose cache_control, but flatten() reads either form.
        sys_text = self._flatten(system)
        usr_text = self._flatten(user)

        sys_blocks = [{"type": "text", "text": sys_text}]
        usr_blocks = [{"type": "text", "text": usr_text}]
        key = _cache_key(model, sys_blocks, [{"role": "user", "content": usr_blocks}], max_tokens)
        if use_disk_cache:
            cached = _read_disk_cache(self._cache_dir, key)
            if cached is not None:
                self._on_event("cache_hit", {"label": label, "key": key})
                return cached

        # bart's prompt rides on --system-prompt (verbatim — no agentic default
        # framing from the CLI). A batch-mode preamble keeps the model one-shot.
        batch_preamble = (
            "NON-INTERACTIVE BATCH MODE. There is no human available to answer "
            "questions. Output ONLY the requested artifact, complete and "
            "self-contained, in this single response. Do NOT ask clarifying "
            "questions. Do NOT use tools. Do NOT describe what you are about to "
            "do, or summarize what you did. Do NOT add any preamble, sign-off, "
            "or meta-commentary. If something is ambiguous, make a sensible "
            "assumption and proceed. Begin with the artifact's very first line "
            "and end with its last."
        )
        system_prompt_arg = f"{batch_preamble}\n\n{sys_text}"
        stdin_payload = usr_text  # user message only; system goes via --system-prompt

        # Build the command. Try stream-json mode for live progress.
        use_streaming = os.environ.get("BART_DISABLE_STREAMING", "") != "1"
        cmd = [self._cli, "--print"]
        if model:
            cmd.extend(["--model", model])
        # Isolation (see _CLAUDE_EFFORT note above): don't load the user's
        # global settings (effortLevel / plugins / hooks / skills), pin the
        # effort level, and turn off MCP, slash commands, and built-in tools —
        # an artifact author needs none of them, and inheriting them is what
        # made every artifact "hang" for 10+ minutes.
        #
        # `effort` lets a caller drop below the global default for one call —
        # the long daily-lesson artifact is unusably slow at "medium" on the
        # subscription backend (minutes of hidden thinking before any text),
        # so the AuthorAgent passes "low" for it. Anything not in the valid
        # set falls back to the pinned default.
        eff = (effort or "").strip().lower()
        effort_arg = eff if eff in _VALID_CLAUDE_EFFORTS else _CLAUDE_EFFORT
        cmd.extend([
            "--setting-sources", "project,local",
            "--effort", effort_arg,
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--tools", "",
            "--system-prompt", system_prompt_arg,
        ])
        if use_streaming:
            # `--include-partial-messages` makes the CLI emit `stream_event`
            # content-block deltas as text is produced, instead of buffering
            # the whole reply into one trailing `assistant` snapshot — so the
            # run shows a real, climbing char count and stops looking hung.
            cmd.extend([
                "--output-format", "stream-json", "--verbose",
                "--include-partial-messages",
            ])

        # CRITICAL: strip auth env vars that would force the CLI into API-key mode
        # instead of using the user's claude.ai subscription. If ANTHROPIC_API_KEY
        # is set in the parent env (e.g. from a shell rc file), the CLI silently
        # uses it — and if the key has no credits, you get "Credit balance is too
        # low" even with a perfectly healthy Max subscription.
        scrubbed_env = {
            k: v for k, v in os.environ.items()
            if k not in {
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "ANTHROPIC_BEDROCK_BASE_URL",
                "ANTHROPIC_VERTEX_PROJECT_ID",
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX",
            }
        }

        t0 = time.time()
        self._on_event("call_start", {"label": label, "model": model, "backend": "claude-code"})

        # Heartbeat thread: fires every 15s while the subprocess is alive so the
        # orchestrator can show the user that the call is still progressing.
        # CLAUDE CLI doesn't stream; without this, the user sees nothing for 4-6 min
        # and assumes a hang.
        done = threading.Event()
        HEARTBEAT_INTERVAL = 15

        def _heartbeat():
            elapsed = 0
            while not done.wait(HEARTBEAT_INTERVAL):
                elapsed += HEARTBEAT_INTERVAL
                self._on_event("heartbeat", {"label": label, "elapsed_s": elapsed})
                # After 60s, suggest knobs
                if elapsed == 60:
                    self._on_event("slow_warning", {"label": label, "elapsed_s": elapsed})

        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        # Streaming mode (stream-json): we read one JSON event per line from
        # stdout, surface a per-call progress event, and accumulate text.
        # Plain mode falls back to subprocess.run.
        # ConcurrencyGuard: hold a per-model slot for the lifetime of the
        # CLI call so subscription-mode rate limits don't get pummeled.
        try:
            with self._guard.acquire(model, label=label):
                if use_streaming:
                    stdout, stderr, returncode = self._run_streaming(cmd, stdin_payload, label, scrubbed_env)
                else:
                    proc = subprocess.run(
                        cmd, input=stdin_payload, capture_output=True, text=True,
                        timeout=self._timeout_s, check=False, env=scrubbed_env,
                    )
                    stdout, stderr, returncode = proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired as e:
            done.set()
            raise LLMError(f"`claude` CLI timed out after {self._timeout_s}s on '{label}'.") from e
        finally:
            done.set()

        dt = time.time() - t0
        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()

        if returncode != 0:
            cmd_str = " ".join(cmd)
            combined_err = (stderr + " " + stdout).lower()

            # Detect well-known failure modes and give a precise message.
            if "credit balance" in combined_err or ("insufficient" in combined_err and "credit" in combined_err):
                raise LLMError(
                    "The Anthropic API returned 'Credit balance is too low'.\n\n"
                    "  This is almost always *not* about your Claude.ai subscription —\n"
                    "  it means the `claude` CLI is using an API key (which has no balance)\n"
                    "  instead of your subscription auth.\n\n"
                    "  Most common fixes:\n"
                    "    1. An ANTHROPIC_API_KEY is set in your shell rc file (e.g. ~/.zshrc or\n"
                    "       ~/.bashrc) and is overriding subscription auth. bart already strips\n"
                    "       it from the subprocess env, but if your CLI was already configured\n"
                    "       to API mode (e.g. `claude /login` while a key was set), you may need\n"
                    "       to log in fresh:\n"
                    "         claude /logout\n"
                    "         claude /login         # pick the 'subscription' option\n"
                    "    2. Or, if you'd rather use API-key billing, run `./run setup` and\n"
                    "       pick option 2, then paste a key with credits.\n\n"
                    f"  raw CLI stderr: {stderr[:200] or '(empty)'}"
                )
            if "not logged in" in combined_err or "authentication" in combined_err or "auth" in combined_err and "fail" in combined_err:
                raise LLMError(
                    "Your Claude Code CLI isn't logged in. Run `claude` once in a terminal, "
                    "complete the login flow, then re-run bart.\n\n"
                    f"  raw CLI stderr: {stderr[:200] or '(empty)'}"
                )
            if _looks_like_context_too_long(combined_err):
                raise LLMContextTooLongError(
                    f"Input exceeds context window on '{label}' "
                    f"(model={model}, backend=claude-code).\n"
                    f"  raw CLI stderr: {stderr[:300] or '(empty)'}"
                )
            if "model" in combined_err and ("not found" in combined_err or "unavailable" in combined_err or "not supported" in combined_err):
                raise LLMError(
                    f"Your Claude Code subscription doesn't have access to model `{model}`.\n\n"
                    "  Edit .bart_config.json and switch `primary_model` and/or `fast_model`\n"
                    "  to a model your subscription supports (e.g. claude-sonnet-4-6).\n\n"
                    f"  raw CLI stderr: {stderr[:200] or '(empty)'}"
                )

            details = [
                f"  command:   {cmd_str}",
                f"  exit code: {returncode}",
                f"  model:     {model or '(default)'}",
            ]
            if stderr:
                details.append(f"  stderr:\n{_indent(stderr[:1500], '    ')}")
            else:
                details.append("  stderr:    (empty)")
            if stdout:
                details.append(f"  stdout:\n{_indent(stdout[:600], '    ')}")
            details.extend([
                "",
                "  hints:",
                "    • run `claude` once interactively to confirm you're logged in.",
                "    • try `./run doctor` to test the CLI directly.",
                f"    • if the model `{model}` is unavailable on your subscription, edit "
                ".bart_config.json and switch primary_model / fast_model.",
                "    • or switch to API-key auth: `./run setup` and pick option 2.",
            ])
            raise LLMError(
                f"`claude` CLI failed on '{label}'.\n" + "\n".join(details)
            )

        if not stdout:
            raise LLMError(
                f"`claude` CLI returned empty output on '{label}' "
                f"(stderr: {stderr[:400] or 'empty'}). "
                "Try running `./run doctor` to diagnose."
            )
        text = stdout

        # Approximate telemetry: chars-as-tokens proxy, no cost.
        approx_in = (len(stdin_payload) + len(system_prompt_arg)) // 4
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


# ─────────────────────────────────────────────────────────────────────
# Local backend — runs Qwen3 (or other open-weight model) via mlx-lm
# (Apple Silicon) or llama-cpp-python (CUDA / CPU). Talks the
# OpenAI-compatible HTTP API exposed by those servers, with sampler-level
# JSON via GBNF grammars on llama.cpp.
#
# Architectural wins:
#   - Streaming + heartbeat (fixes "looks hung" + "partial loss on timeout")
#   - Retry+backoff with Retry-After honoring (fixes "blip kills the run")
#   - num_predict capped against n_ctx (fixes "truncated mid-sentence")
#   - System role passed as system message (fixes Gemma 3 system-prompt drop)
#   - Grammar-constrained JSON when schema is in scope (fixes "garbage JSON")
# ─────────────────────────────────────────────────────────────────────


class LocalBackend:
    """OpenAI-compatible client for a locally-hosted open-weight model.

    Construct via `LocalBackend(runtime=..., telemetry=...)` where `runtime`
    is a `bart.local_runtime.RuntimeHandle` (the orchestrator builds and
    owns the handle; this class is a thin protocol-adapter on top).

    Implements the same `complete(...)` shape as `AnthropicAPIBackend` so
    the orchestrator and agents do not need to know which backend is in
    use. The `model` argument is accepted for protocol compatibility but
    ignored — the runtime is bound to a single served model.
    """

    name = "local"

    def __init__(
        self,
        runtime,
        telemetry: Telemetry,
        cache_dir: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._runtime = runtime
        self._tel = telemetry
        self._cache_dir = cache_dir
        self._on_event = on_event or (lambda evt, payload: None)
        # Re-bind the client's event handler so heartbeats route through us.
        self._runtime.client.on_event = self._on_event

    @staticmethod
    def _flatten_blocks(content) -> str:
        """Anthropic-style content is `str | list[{"type":"text","text":...}]`.
        Flatten to plain text — local servers don't understand block content."""
        if isinstance(content, str):
            return content
        out: list[str] = []
        for b in content or ():
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    out.append(t)
            elif isinstance(b, str):
                out.append(b)
        return "\n".join(out)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str | list[dict[str, Any]],
        max_tokens: int = 8000,
        label: str = "",
        use_disk_cache: bool = True,
        temperature: float = 0.7,
        response_format: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,  # honored only by ClaudeCodeBackend; ignored here
    ) -> str:
        sys_text = self._flatten_blocks(system)
        usr_text = self._flatten_blocks(user)

        # Disk-cache key uses the runtime's model key (not the caller's
        # `model` arg) so cache hits are correct across role labels.
        sys_blocks = [{"type": "text", "text": sys_text}]
        usr_blocks = [{"type": "text", "text": usr_text}]
        cache_model = self._runtime.model.key
        key = _cache_key(cache_model, sys_blocks,
                         [{"role": "user", "content": usr_blocks}],
                         max_tokens)
        if use_disk_cache:
            cached = _read_disk_cache(self._cache_dir, key)
            if cached is not None:
                self._on_event("cache_hit", {"label": label, "key": key})
                return cached

        messages = [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": usr_text},
        ]
        t0 = time.time()
        try:
            text, usage = self._runtime.client.complete(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                json_schema=json_schema,
                is_llama_cpp=self._runtime.is_llama_cpp,
                label=label,
            )
        except Exception as e:  # noqa: BLE001
            # Translate context-too-long from the local client into our
            # shared LLMContextTooLongError so existing catch sites work.
            from .local_runtime.client import LocalClientContextTooLong
            if isinstance(e, LocalClientContextTooLong):
                raise LLMContextTooLongError(str(e)) from e
            raise LLMError(f"local backend error on '{label}': {e}") from e

        dt = time.time() - t0
        text = _sanitize_local_output(text, response_format=response_format)
        rec = CallRecord(
            label=label,
            model=cache_model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            duration_s=dt,
        )
        self._tel.record(rec)
        self._on_event("call_done", {
            "label": label, "duration_s": round(dt, 2),
            "output_tokens": rec.output_tokens, "chars": len(text),
        })
        if use_disk_cache:
            _write_disk_cache(self._cache_dir, key, text)
        return text


# ─── Browser backend ────────────────────────────────────────────────────────
#
# Runs inference in the user's browser tab via WebLLM (WebGPU). The orchestrator
# subprocess can't talk to the browser directly, so it POSTs prompts to the
# website's FastAPI server, which forwards them to the user's WebSocket and
# blocks until the browser sends back a completion.
#
# The orchestrator picks this backend when auth_mode == "browser". The proxy
# URL comes from env var BART_LLM_PROXY_URL, set by the website's run launcher.
class BrowserBackend:
    """LLM backend that proxies completions to the user's browser via the
    website's FastAPI server. Same `complete(...)` shape as the other
    backends so the orchestrator is unaware of the transport."""

    name = "browser"

    def __init__(
        self,
        proxy_url: str,
        telemetry: Telemetry,
        cache_dir: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        timeout_s: float = 240.0,
        model_label: str = "browser",
    ):
        self._proxy_url = proxy_url.rstrip("/")
        self._tel = telemetry
        self._cache_dir = cache_dir
        self._on_event = on_event or (lambda evt, payload: None)
        self._timeout_s = timeout_s
        self._model_label = model_label

    @staticmethod
    def _flatten(content) -> str:
        if isinstance(content, str):
            return content
        out: list[str] = []
        for b in content or ():
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    out.append(t)
            elif isinstance(b, str):
                out.append(b)
        return "\n".join(out)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str | list[dict[str, Any]],
        max_tokens: int = 8000,
        label: str = "",
        use_disk_cache: bool = True,
        temperature: float = 0.7,
        response_format: str | None = None,
        json_schema: dict[str, Any] | None = None,
        effort: str | None = None,
    ) -> str:
        sys_text = self._flatten(system)
        usr_text = self._flatten(user)

        # Hard cap on combined system+user prompt size before it goes over
        # the wire to WebLLM. Real corpora tokenize denser than ASCII (LaTeX,
        # code, foreign chars often hit ~1.5 chars/token instead of 3.5), so
        # we have to be conservative. 12K chars ≈ 8K tokens at the dense
        # extreme — fits inside Gemma 2's 8K context window with a small
        # margin for the chat template + ~2K tokens of output. Lossy for
        # giant uploads but lets runs actually finish.
        BROWSER_PROMPT_CHAR_CAP = 12_000
        if len(sys_text) + len(usr_text) > BROWSER_PROMPT_CHAR_CAP:
            allowed = max(1000, BROWSER_PROMPT_CHAR_CAP - len(sys_text))
            if len(usr_text) > allowed:
                marker = "\n\n[…corpus truncated to fit the local model's context…]"
                usr_text = usr_text[: max(0, allowed - len(marker))] + marker

        sys_blocks = [{"type": "text", "text": sys_text}]
        usr_blocks = [{"type": "text", "text": usr_text}]
        cache_model = self._model_label
        key = _cache_key(cache_model, sys_blocks,
                         [{"role": "user", "content": usr_blocks}],
                         max_tokens)
        if use_disk_cache:
            cached = _read_disk_cache(self._cache_dir, key)
            if cached is not None:
                self._on_event("cache_hit", {"label": label, "key": key})
                return cached

        # Cap max_tokens to fit Gemma's 8K context (we've used most of it for
        # the prompt). 2500 leaves enough headroom for typical agent outputs.
        capped_max_tokens = min(max_tokens, 2500)

        body = json.dumps({
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user",   "content": usr_text},
            ],
            "max_tokens": capped_max_tokens,
            "temperature": temperature,
            "response_format": response_format,
            "label": label,
        }).encode("utf-8")

        import urllib.request as _ur
        import urllib.error as _ue
        req = _ur.Request(
            self._proxy_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        try:
            with _ur.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read()
        except _ue.HTTPError as e:
            payload_text = ""
            try:
                payload_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 413:
                raise LLMContextTooLongError(
                    f"browser model can't fit this prompt: {payload_text or e.reason}"
                ) from e
            raise LLMError(
                f"browser backend HTTP {e.code} on '{label}': {payload_text or e.reason}"
            ) from e
        except _ue.URLError as e:
            raise LLMError(
                f"browser backend unreachable on '{label}': {e.reason} — "
                "is the user's tab still open?"
            ) from e

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise LLMError(
                f"browser backend returned malformed JSON on '{label}': {e}"
            ) from e

        if not isinstance(payload, dict) or "text" not in payload:
            err = (payload or {}).get("error") if isinstance(payload, dict) else None
            if err == "context_too_long":
                raise LLMContextTooLongError(
                    f"browser model can't fit this prompt: {payload.get('detail', '')}"
                )
            raise LLMError(
                f"browser backend returned unexpected payload on '{label}': {payload}"
            )

        text = _sanitize_local_output(payload["text"] or "", response_format=response_format)
        usage = payload.get("usage") or {}
        dt = time.time() - t0
        rec = CallRecord(
            label=label,
            model=cache_model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            duration_s=dt,
        )
        self._tel.record(rec)
        self._on_event("call_done", {
            "label": label, "duration_s": round(dt, 2),
            "output_tokens": rec.output_tokens, "chars": len(text),
        })
        if use_disk_cache:
            _write_disk_cache(self._cache_dir, key, text)
        return text

