"""LLM backends.

Two paths to run bart:

  1. AnthropicAPIBackend  - API key from console.anthropic.com. Pay-per-token,
     supports prompt caching, exposes detailed usage telemetry.

  2. ClaudeCodeBackend    - shells out to the `claude` CLI for users who have
     a claude.ai Pro/Max/Team subscription but no API key. No per-token cost
     (covered by the subscription), no prompt caching at the API layer (we
     fall back to disk cache), no usage telemetry.

Both classes expose the same `complete(...)` method so the orchestrator does
not care which one is in use.

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


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


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
                    # Final event — `result` field contains the complete text.
                    if event.get("subtype") == "success":
                        full = event.get("result", "")
                        if full:
                            # Replace accumulated text with the canonical result.
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
