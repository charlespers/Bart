"""OpenAI-compatible streaming HTTP client for the local server.

Talks `POST /v1/chat/completions` to mlx-lm or llama-cpp-python's server.
Both engines accept the OpenAI shape: messages list, model, max_tokens,
temperature, stop, response_format, stream. Differences:

  - llama-cpp-python accepts `grammar` (GBNF string) for sampler-level
    constraint. mlx-lm doesn't, but it accepts a json-mode fallback.
  - mlx-lm uses `max_tokens`, llama-cpp-python uses `max_tokens` too.

We retry transient failures (5xx, network blips) up to 4 times with
exponential backoff. We honor server-supplied Retry-After. We emit
heartbeat events every 5s while streaming so the orchestrator UI can
show progress instead of looking hung.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .grammar import schema_to_gbnf


class LocalClientError(RuntimeError):
    pass


class LocalClientContextTooLong(LocalClientError):
    pass


@dataclass
class _RetryAfter:
    seconds: float | None


def _parse_retry_after(headers) -> _RetryAfter:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return _RetryAfter(None)
    try:
        return _RetryAfter(float(raw))
    except (TypeError, ValueError):
        return _RetryAfter(None)


def _approx_tokens(text: str) -> int:
    """Cheap tiktoken-free token estimate. 3.5 chars/token is closer to truth
    than 4.0 for English+code+math mixed content (the audit found that 4.0
    underestimates by 20-40% on code-heavy text)."""
    return max(1, int(len(text) / 3.5))


@dataclass
class LocalClient:
    base_url: str
    model_id: str
    n_ctx: int
    timeout_s: float = 600.0       # per-request HTTP timeout
    max_retries: int = 4
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def _emit(self, evt: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(evt, payload)

    # Qwen3 emits a "reasoning" channel by default and reserves "content"
    # for the final answer. For our pipeline we want the final answer,
    # not the chain-of-thought, so we disable thinking unconditionally.
    # Power users can re-enable it later by editing this flag.
    _DISABLE_THINKING = True

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        response_format: str | None,
        json_schema: dict[str, Any] | None,
        stop: list[str] | None,
        is_llama_cpp: bool,
    ) -> dict[str, Any]:
        # Cap max_tokens against context window.
        # Estimate input tokens conservatively; leave a 256-token buffer.
        approx_input = sum(_approx_tokens(m.get("content", "")) for m in messages)
        room = max(64, self.n_ctx - approx_input - 256)
        capped = min(max_tokens, room)
        if capped < max_tokens * 0.5:
            self._emit("max_tokens_capped", {
                "requested": max_tokens, "capped": capped,
                "approx_input": approx_input, "n_ctx": self.n_ctx,
            })

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": capped,
            "temperature": temperature,
            "stream": True,
        }
        if self._DISABLE_THINKING:
            # Qwen3 chat template flag — both mlx-lm and llama-cpp-python
            # forward this through to the tokenizer. With it set False,
            # the model emits content directly instead of routing reasoning
            # into a separate channel.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if stop:
            payload["stop"] = stop

        if response_format == "json":
            if is_llama_cpp and json_schema:
                # GBNF gives us sampler-level guaranteed parseable JSON
                # matching the schema. This architecturally fixes the
                # "bad JSON / silent failure" class of bug.
                payload["grammar"] = schema_to_gbnf(json_schema)
            elif json_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response", "schema": json_schema,
                        "strict": True,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}
        return payload

    def _post_streaming(self, payload: dict[str, Any]) -> tuple[str, dict]:
        """Issue the request, stream SSE, return (text, usage_dict).

        Heartbeats are emitted every 5s while data is flowing.
        """
        url = f"{self.base_url}/v1/chat/completions"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        out: list[str] = []
        usage: dict[str, Any] = {}
        last_hb = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").rstrip("\r\n")
                # mlx-lm sends SSE keepalive comments like ": keepalive 11/15"
                # between content chunks. Skip them.
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data in ("", "[DONE]"):
                    if data == "[DONE]":
                        break
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # OpenAI delta shape: {"choices":[{"delta":{"content":"..."}}]}
                # Qwen3 with thinking enabled emits {"delta":{"reasoning":"..."}}
                # — fall back to that channel if we ever see it (we disable
                # thinking by default, but a future template change shouldn't
                # silently drop output).
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content") or delta.get("reasoning")
                    if chunk:
                        out.append(chunk)
                # Some engines emit usage in the final chunk.
                if "usage" in obj and obj["usage"]:
                    usage = obj["usage"]
                # Heartbeat every 5s of streaming.
                now = time.time()
                if now - last_hb >= 5.0:
                    last_hb = now
                    self._emit("heartbeat", {"chars": sum(len(s) for s in out)})
        return "".join(out), usage

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        temperature: float = 0.7,
        response_format: str | None = None,
        json_schema: dict[str, Any] | None = None,
        stop: list[str] | None = None,
        is_llama_cpp: bool = False,
        label: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """Run a chat completion. Returns (text, usage).

        `is_llama_cpp` selects the GBNF code path for json mode. Pass True
        for llama-cpp-python servers, False for mlx-lm.
        """
        payload = self._build_payload(
            messages, max_tokens, temperature, response_format,
            json_schema, stop, is_llama_cpp,
        )

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            t0 = time.time()
            try:
                self._emit("call_start", {
                    "label": label, "model": self.model_id, "attempt": attempt,
                })
                text, usage = self._post_streaming(payload)
                dt = time.time() - t0
                self._emit("call_done", {
                    "label": label, "duration_s": round(dt, 2),
                    "chars": len(text),
                    "output_tokens": usage.get("completion_tokens", 0),
                })
                if not text.strip():
                    raise LocalClientError(
                        f"empty response from local server on '{label}'"
                    )
                return text, usage
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (400, 422):
                    body = ""
                    try:
                        body = e.read().decode(errors="replace")[:500]
                    except OSError:
                        pass
                    if "context" in body.lower() and "length" in body.lower():
                        raise LocalClientContextTooLong(
                            f"input too long for n_ctx={self.n_ctx}: {body}"
                        ) from e
                    raise LocalClientError(
                        f"bad request to local server: {e.code} {body}"
                    ) from e
                if e.code in (429, 500, 502, 503, 504):
                    retry = _parse_retry_after(e.headers).seconds
                    wait = retry if retry is not None else min(60.0, 2 ** attempt)
                    self._emit("retry", {
                        "label": label, "attempt": attempt,
                        "wait_s": wait, "error": f"http {e.code}",
                    })
                    time.sleep(wait)
                    continue
                raise LocalClientError(
                    f"local server returned {e.code} on '{label}': {e}"
                ) from e
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last_err = e
                wait = min(60.0, 2 ** attempt)
                self._emit("retry", {
                    "label": label, "attempt": attempt,
                    "wait_s": wait, "error": str(e),
                })
                time.sleep(wait)
                continue
        raise LocalClientError(
            f"exhausted {self.max_retries} retries on '{label}': {last_err}"
        )
