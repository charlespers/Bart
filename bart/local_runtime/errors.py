"""User-facing error type for the local runtime.

Every public function in `bart.local_runtime` raises `LocalRuntimeError`
on user-recoverable failure. The message is the *only* thing the user
sees — it must be plain English and end with one concrete next step.

The orchestrator catches these and prints them without the stack trace.
The full traceback always goes to the run log so a developer can dig in
later, but a non-technical user never has to look at it.

The `code` field is short, stable, and grep-able: tests pin behavior to
codes (e.g. `assert e.code == "DISK_FULL"`), and future translations or
GUI surfaces can route by code.
"""
from __future__ import annotations


class LocalRuntimeError(RuntimeError):
    """A user-facing error. `args[0]` is the displayed message."""

    def __init__(self, code: str, message: str, *, hint: str = ""):
        self.code = code
        self.hint = hint
        body = message
        if hint:
            body = f"{message}\n\n  → {hint}"
        super().__init__(body)


# Stable error codes. Add new ones; do not rename existing ones.
CODE_DISK_FULL = "DISK_FULL"
CODE_NETWORK = "NETWORK"
CODE_HF_REPO_MISSING = "HF_REPO_MISSING"
CODE_HF_GATED = "HF_GATED"
CODE_PIP_INSTALL = "PIP_INSTALL"
CODE_ENGINE_IMPORT = "ENGINE_IMPORT"
CODE_SERVER_START = "SERVER_START"
CODE_SERVER_HEALTH = "SERVER_HEALTH"
CODE_WEIGHTS_INCOMPLETE = "WEIGHTS_INCOMPLETE"
CODE_CACHE_PERMISSION = "CACHE_PERMISSION"
CODE_HARDWARE_INSUFFICIENT = "HARDWARE_INSUFFICIENT"
