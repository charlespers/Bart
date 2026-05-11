"""LLM client — thin re-export over `bart.backends`.

Kept as a stable import surface (`from .llm import LLMClient`) for the
orchestrator. The actual implementations live in `backends.py`.
"""
from __future__ import annotations

from .backends import (
    AnthropicAPIBackend,
    ClaudeCodeBackend,
    LLMError,
    LocalBackend,
)

# Back-compat alias — the orchestrator type-hints against this name.
LLMClient = AnthropicAPIBackend

__all__ = [
    "LLMClient", "LLMError",
    "AnthropicAPIBackend", "ClaudeCodeBackend", "LocalBackend",
]
