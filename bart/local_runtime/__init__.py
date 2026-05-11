"""Local-model runtime for Bart.

This package owns the lifecycle of a locally-hosted open-weight LLM:

- hardware.py — detect CPU/GPU/RAM and recommend a model tier
- models.py   — catalog of supported open-weight models (Qwen3 family + fallbacks)
- cache.py    — on-disk weight cache with 24h TTL
- installer.py — auto-install the right inference engine and download weights
- server.py   — manage the inference server subprocess (mlx-lm / llama-cpp-python)
- client.py   — OpenAI-compatible streaming HTTP client with retry + heartbeat
- grammar.py  — JSON Schema → GBNF grammar compiler for sampler-level JSON

Top-level entry point: `prepare(cfg, console)` returns a `RuntimeHandle`
with a configured client, then `handle.shutdown()` at end of run.
"""
from __future__ import annotations

from .handle import RuntimeHandle, prepare

__all__ = ["RuntimeHandle", "prepare"]
