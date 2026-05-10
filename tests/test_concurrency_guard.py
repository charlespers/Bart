"""Tests for ConcurrencyGuard — per-model concurrency cap that keeps
parallel fan-out under Anthropic's per-model rate limits."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from bart.backends import (
    ConcurrencyGuard,
    _model_tier,
    _retry_after_seconds,
)


def test_model_tier_classification():
    assert _model_tier("claude-opus-4-7") == "opus"
    assert _model_tier("claude-sonnet-4-6") == "sonnet"
    assert _model_tier("claude-haiku-4-5-20251001") == "haiku"
    # Unknown / local models return None — they get no cap.
    assert _model_tier("gemma3:4b") is None
    assert _model_tier("ollama:something") is None


def test_default_caps():
    g = ConcurrencyGuard()
    assert g.cap_for("claude-opus-4-7") == 2
    assert g.cap_for("claude-sonnet-4-6") == 4
    assert g.cap_for("claude-haiku-4-5-20251001") == 8
    assert g.cap_for("gemma3:4b") is None  # uncapped


def test_env_override(monkeypatch):
    monkeypatch.setenv("BART_MAX_CONCURRENT_OPUS", "5")
    monkeypatch.setenv("BART_MAX_CONCURRENT_SONNET", "10")
    monkeypatch.setenv("BART_MAX_CONCURRENT_HAIKU", "20")
    g = ConcurrencyGuard()
    assert g.cap_for("claude-opus-4-7") == 5
    assert g.cap_for("claude-sonnet-4-6") == 10
    assert g.cap_for("claude-haiku-4-5-20251001") == 20


def test_uncapped_model_acquires_immediately():
    g = ConcurrencyGuard()
    # No cap → acquire is a no-op.
    with g.acquire("gemma3:4b"):
        # Should be able to "acquire" the same uncapped model many times
        # in nested context managers without blocking.
        with g.acquire("gemma3:4b"):
            pass


def test_semaphore_caps_concurrent_holders():
    """N+1 threads competing for an N-cap semaphore: only N hold at once."""
    g = ConcurrencyGuard()
    cap = g.cap_for("claude-opus-4-7")  # default 2
    assert cap == 2

    held = threading.Semaphore(0)  # signals each acquire
    release = threading.Event()    # holders block here until released
    concurrent_count = []
    counter_lock = threading.Lock()
    counter = {"n": 0, "max": 0}

    def worker():
        with g.acquire("claude-opus-4-7"):
            with counter_lock:
                counter["n"] += 1
                counter["max"] = max(counter["max"], counter["n"])
            held.release()
            release.wait(timeout=2.0)
            with counter_lock:
                counter["n"] -= 1

    n_workers = cap + 2
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(worker) for _ in range(n_workers)]
        # Wait until at least cap workers have acquired.
        for _ in range(cap):
            assert held.acquire(timeout=2.0), "first cap workers should acquire fast"
        # Give other workers a chance to (incorrectly) get past the cap.
        time.sleep(0.1)
        with counter_lock:
            assert counter["n"] == cap, (
                f"expected exactly {cap} concurrent holders, got {counter['n']}"
            )
        release.set()
        for f in futures:
            f.result(timeout=5.0)
    assert counter["max"] == cap, f"max concurrent should be {cap}, was {counter['max']}"


def test_retry_after_ms_takes_precedence():
    class _Headers(dict):
        def get(self, k, default=None):
            return super().get(k.lower(), default)

    class _Resp:
        def __init__(self, h):
            self.headers = _Headers({k.lower(): v for k, v in h.items()})

    class _Err(Exception):
        def __init__(self, h):
            self.response = _Resp(h)

    # ms takes precedence over s when both present
    assert _retry_after_seconds(_Err({"retry-after-ms": "2500", "retry-after": "10"})) == 2.5
    # ms-only
    assert _retry_after_seconds(_Err({"retry-after-ms": "1000"})) == 1.0
    # seconds-only
    assert _retry_after_seconds(_Err({"retry-after": "7"})) == 7.0
    # no headers
    assert _retry_after_seconds(_Err({})) is None
    # unparseable values fall through to None
    assert _retry_after_seconds(_Err({"retry-after": "soon"})) is None


def test_retry_after_handles_missing_response():
    class _Err(Exception):
        pass
    assert _retry_after_seconds(_Err()) is None
