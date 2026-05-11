"""Token-usage tracking and cost estimation."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Approximate $/1M-token rates (USD). Update if pricing changes.
PRICING = {
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
    # Local stack: Qwen3 family via mlx-lm or llama-cpp-python. $0/token.
    "qwen3-32b-mlx-4bit":     {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-32b-gguf-q4km":    {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-30b-a3b-mlx-4bit": {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-30b-a3b-gguf-q4km": {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-14b-mlx-4bit":     {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-14b-gguf-q4km":    {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-8b-mlx-4bit":      {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-8b-gguf-q4km":     {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-4b-mlx-4bit":      {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
    "qwen3-4b-gguf-q4km":     {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
}


@dataclass
class CallRecord:
    label: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    duration_s: float = 0.0
    cost_usd: float = 0.0


@dataclass
class Telemetry:
    calls: list[CallRecord] = field(default_factory=list)
    stages: dict = field(default_factory=dict)  # {stage_name: duration_s}
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, rec: CallRecord) -> None:
        # Fill in cost if missing
        rec.cost_usd = self._cost(rec)
        with self._lock:
            self.calls.append(rec)

    def record_stage(self, name: str, duration_s: float) -> None:
        with self._lock:
            self.stages[name] = round(duration_s, 2)

    @staticmethod
    def _cost(rec: CallRecord) -> float:
        p = PRICING.get(rec.model)
        if not p:
            return 0.0
        return (
            rec.input_tokens * p["input"]
            + rec.output_tokens * p["output"]
            + rec.cache_creation_input_tokens * p["cache_write"]
            + rec.cache_read_input_tokens * p["cache_read"]
        ) / 1_000_000.0

    def summary(self) -> dict:
        total = {
            "calls": len(self.calls),
            "input_tokens": sum(c.input_tokens for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "cache_creation_input_tokens": sum(c.cache_creation_input_tokens for c in self.calls),
            "cache_read_input_tokens": sum(c.cache_read_input_tokens for c in self.calls),
            "duration_s": sum(c.duration_s for c in self.calls),
            "cost_usd": sum(c.cost_usd for c in self.calls),
        }
        return total

    def write(self, path: Path) -> None:
        data = {
            "summary": self.summary(),
            "stages": dict(self.stages),
            "calls": [asdict(c) for c in self.calls if not c.label.startswith("_")],
        }
        # Strip the lock from any nested dicts
        for c in data["calls"]:
            c.pop("_lock", None)
        path.write_text(json.dumps(data, indent=2))
