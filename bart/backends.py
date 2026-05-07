"""LLM backends.

Three paths to run bart:

  1. AnthropicAPIBackend  - API key from console.anthropic.com. Pay-per-token,
     supports prompt caching, exposes detailed usage telemetry.

  2. ClaudeCodeBackend    - shells out to the `claude` CLI for users who have
     a claude.ai Pro/Max/Team subscription but no API key. No per-token cost
     (covered by the subscription), no prompt caching at the API layer (we
     fall back to disk cache), no usage telemetry.

  3. OllamaBackend        - runs Gemma 4 (or any Ollama-hosted model) on the
     local machine. No per-token cost, no Anthropic dependency. Models are
     pulled at run start and cached for 24h between runs. See `local_setup`
     for lifecycle management.

All three classes expose the same `complete(...)` method so the orchestrator
does not care which one is in use.

Hanging vs slow: the Claude Code CLI with `--print` is non-streaming. We get
NO output until the model finishes generating. Long lessons can take 4-6 min
on Opus. To distinguish 'slow' from 'hung', we run a heartbeat thread that
fires `heartbeat` events every 30s while the subprocess is alive. The
orchestrator surfaces these to the user.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
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
    # Gemma 3 (Ollama) — published limits
    "gemma3:1b": 32_000,
    "gemma3:4b": 128_000,
    "gemma3:12b": 128_000,
    "gemma3:27b": 128_000,
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


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


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

    @staticmethod
    def _cacheable_system(s: str) -> list[dict[str, Any]]:
        """Wrap a system prompt as a single cacheable block.

        System prompts are stable across many calls (one per agent role), so
        caching them gets us cheap reads on every call after the first.
        Anthropic supports up to 4 cache breakpoints per request — this is one
        of them. Corpus block (in user message) is the second.
        """
        return [{"type": "text", "text": s, "cache_control": {"type": "ephemeral"}}]

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
        response_format: str | None = None,  # "json" honored by OllamaBackend; ignored here
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
                    wait = 2 ** attempt
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

    def _run_streaming(
        self, cmd: list[str], combined: str, label: str, env: dict[str, str]
    ) -> tuple[str, str, int]:
        """Run the CLI in stream-json mode, parsing events for live progress.

        Returns (stdout_text, stderr, returncode) where stdout_text is the
        concatenated assistant text from the stream events.
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

        accumulated_text: list[str] = []
        last_emit_chars = 0
        last_emit_time = time.time()

        # Read stdout line-by-line, parse stream-json events, accumulate text.
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
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
                #   {"type": "assistant", "message": {"content": [{"type":"text","text":"..."}]}}
                #   {"type": "result", "subtype": "success", "result": "<full text>", ...}
                etype = event.get("type", "")
                if etype == "assistant":
                    msg = event.get("message", {}) or {}
                    for block in (msg.get("content") or []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            txt = block.get("text", "")
                            if txt:
                                accumulated_text.append(txt)
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
        except Exception as e:  # noqa: BLE001
            self._on_event("stream_error", {"label": label, "error": str(e)})

        try:
            proc.wait(timeout=self._timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise

        stderr = ""
        try:
            if proc.stderr is not None:
                stderr = proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            pass

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

        # Combined message: system goes inside an explicit wrapper so the model
        # respects it, then the user request.
        combined = f"<system_instructions>\n{sys_text}\n</system_instructions>\n\n{usr_text}"

        # Build the command. We pass `--model` only if the caller specified one;
        # otherwise let the CLI use the user's default. Try stream-json mode for
        # live progress; fall back to plain text if the CLI rejects it.
        use_streaming = os.environ.get("BART_DISABLE_STREAMING", "") != "1"
        cmd = [self._cli, "--print"]
        if model:
            cmd.extend(["--model", model])
        if use_streaming:
            cmd.extend(["--output-format", "stream-json", "--verbose"])

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
        try:
            if use_streaming:
                stdout, stderr, returncode = self._run_streaming(cmd, combined, label, scrubbed_env)
            else:
                proc = subprocess.run(
                    cmd, input=combined, capture_output=True, text=True,
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


# ─────────────────────────────────────────────────────────────────────
# Ollama (local-model) backend — Gemma 4 default, free, offline
# ─────────────────────────────────────────────────────────────────────


class OllamaBackend:
    """Talks to a local Ollama daemon over its HTTP API. Suitable for users
    who don't have a Claude subscription or API key.

    Strips Anthropic-only `cache_control` blocks before serializing — Ollama
    doesn't have an equivalent. Falls back to bart's disk cache for repeat-
    call savings. Cost is always $0 (PRICING table has zero entries for
    the Gemma model names below).
    """

    name = "ollama-local"

    def __init__(
        self,
        telemetry: Telemetry,
        cache_dir: Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        host: str | None = None,
        timeout_s: int = 1800,
    ):
        from .local_setup import OLLAMA_HOST
        self._host = (host or OLLAMA_HOST).rstrip("/")
        self._tel = telemetry
        self._cache_dir = cache_dir
        self._on_event = on_event or (lambda evt, payload: None)
        self._timeout_s = timeout_s

    @staticmethod
    def _flatten(blocks: str | list[dict[str, Any]]) -> str:
        """Concatenate `[{type:'text', text:'...'}, ...]` (the Anthropic
        block shape) into a single string. `cache_control` blocks are
        ignored — Ollama has no equivalent."""
        if isinstance(blocks, str):
            return blocks
        out: list[str] = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "text":
                txt = b.get("text", "")
                if txt:
                    out.append(txt)
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
        temperature: float = 1.0,
        response_format: str | None = None,
    ) -> str:
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

        import urllib.error
        import urllib.request

        # CRITICAL: set num_ctx explicitly. Ollama defaults to 2048 tokens —
        # which is far too small for bart's prompts (corpus brief + skeleton
        # + research slice routinely exceed 8K tokens). When the input
        # overflows the context window, the model echoes the prompt back or
        # returns garbage, and the orchestrator silently writes that as the
        # artifact. Estimate input size and round up to a Gemma-supported
        # power of two; 27B / 12B / 4B all support ≥ 32K, 1B supports 32K.
        approx_input_tokens = (len(sys_text) + len(usr_text)) // 4
        ctx_budget = approx_input_tokens + max_tokens + 1024  # headroom
        for ceiling in (4096, 8192, 16384, 32768, 65536, 131072):
            if ctx_budget <= ceiling:
                num_ctx = ceiling
                break
        else:
            num_ctx = 131072
        env_override = os.environ.get("BART_OLLAMA_NUM_CTX")
        if env_override and env_override.isdigit():
            num_ctx = int(env_override)

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_text},
                {"role": "user", "content": usr_text},
            ],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "num_ctx": num_ctx,
                "temperature": temperature,
            },
            # Keep weights in VRAM between calls within a run — much faster
            # than reloading per call. Orchestrator force-unloads at end.
            "keep_alive": "30m",
        }
        # Ollama's structured-output mode. When the caller asks for "json",
        # the model is forced to emit syntactically valid JSON — dramatically
        # improves JSON-emitting agents (problem_indexer, exam_pattern,
        # topic_distiller, whimsy_indexer, reviewer-grade) on small models
        # that otherwise wrap JSON in code fences or chatty preambles.
        if response_format == "json":
            payload["format"] = "json"
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.time()
        self._on_event("call_start", {"label": label, "model": model, "backend": "ollama"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                body = resp.read().decode()
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode()[:400]
            except Exception:
                pass
            raise LLMError(
                f"ollama HTTP {e.code} on '{label}' (model={model}): "
                f"{e.reason}. {err_body}"
            ) from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise LLMError(
                f"ollama not reachable at {self._host} on '{label}': {e}. "
                "Run `ollama serve` to start the daemon."
            ) from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise LLMError(f"ollama returned non-JSON on '{label}': {body[:200]}") from e

        text = (data.get("message") or {}).get("content", "") or ""
        if not text.strip():
            raise LLMError(
                f"ollama returned empty content on '{label}' (model={model}). "
                f"raw response: {body[:300]}"
            )
        # Strip Gemma's common chatter: leading "Here is the lesson:" /
        # "Sure! Here's…" preambles, trailing "Hope this helps!" /
        # "Let me know if…" postambles, and outer markdown code fences
        # that the orchestrator does NOT want written into the .md file.
        text = _sanitize_local_output(text, response_format=response_format)

        # Echo-detection: small models (especially gemma3:1b) sometimes
        # repeat the prompt back when overwhelmed. Catch the most common
        # signatures so the orchestrator doesn't silently write the prompt
        # text as the artifact (the symptom: rendered HTML pages that show
        # raw prompt content + empty bart-block boxes).
        echo_signals = (
            "ARTIFACT:",                 # author.py user-message header
            "TEACHING CONTRACT —",       # _daily_lesson_brief preamble
            "OBJECTIVES",                # daily-lesson brief
            "SKELETON (follow",          # daily-lesson brief
            "BRIEF\n",                   # author.py user-message brief block
            "Subject:",                  # most agent user messages
        )
        # Treat as echo if 2+ prompt-anchor strings appear unchanged in the
        # output AND the output isn't a plausible artifact (e.g., < 600
        # chars but contains prompt scaffolding).
        echo_hits = sum(1 for s in echo_signals if s in text)
        if echo_hits >= 2 and len(text) < max(800, len(usr_text) // 3):
            raise LLMError(
                f"ollama model `{model}` appears to be echoing the prompt "
                f"on '{label}' (output {len(text)} chars, contains "
                f"{echo_hits} prompt anchor(s)).\n\n"
                "  This is the classic 'context window too small' failure "
                "mode for tiny local models. Either:\n"
                "    1. Pick a larger Gemma variant: `./run setup` → option 3 → "
                "      gemma3:4b or gemma3:12b.\n"
                "    2. Override the context window: "
                "      BART_OLLAMA_NUM_CTX=16384 ./run\n"
                "    3. Switch to API or subscription mode for these prompts."
            )

        dt = time.time() - t0
        in_tok = int(data.get("prompt_eval_count", 0) or 0)
        out_tok = int(data.get("eval_count", 0) or 0)
        self._tel.record(CallRecord(
            label=label,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            duration_s=dt,
            cost_usd=0.0,
        ))
        self._on_event("call_done", {
            "label": label, "duration_s": dt, "chars": len(text),
            "backend": "ollama", "input_tokens": in_tok, "output_tokens": out_tok,
        })
        if use_disk_cache:
            _write_disk_cache(self._cache_dir, key, text)
        return text
