"""Monthly premium-run metering tests for website/auth.py.

The $10/month subscription includes a fixed monthly allowance of premium
(Claude) runs; local Gemma runs are unlimited. These tests pin the
allowance accounting: counting, month rollover, and the grandfathered
unlimited path.

website/auth.py imports fastapi + bcrypt, which aren't in bart's venv —
the bart package doesn't need them. We stub those two modules so the
metering logic (pure sqlite + datetime) can be tested in isolation.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AUTH_PATH = _REPO / "website" / "auth.py"


def _load_auth(tmp_path: Path):
    """Import website/auth.py with fastapi/bcrypt stubbed and a temp DB."""
    if "fastapi" not in sys.modules:
        fake_fastapi = types.ModuleType("fastapi")
        fake_fastapi.Cookie = lambda *a, **k: None
        fake_fastapi.HTTPException = type("HTTPException", (Exception,), {})
        fake_fastapi.Response = type("Response", (), {})
        sys.modules["fastapi"] = fake_fastapi
    if "bcrypt" not in sys.modules:
        fake_bcrypt = types.ModuleType("bcrypt")
        fake_bcrypt.hashpw = lambda pw, salt: b"hash"
        fake_bcrypt.gensalt = lambda: b"salt"
        fake_bcrypt.checkpw = lambda pw, h: True
        sys.modules["bcrypt"] = fake_bcrypt

    spec = importlib.util.spec_from_file_location("bart_web_auth", _AUTH_PATH)
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    auth.DB_PATH = tmp_path / "bart_test.db"
    auth.init_db()
    return auth


def _make_user(auth, email="student@example.com"):
    uid = auth.create_user(email, "pw123456", "Test Student")
    return auth.find_user_by_id(uid)


# ── Allowance accounting ───────────────────────────────────────────────

def test_fresh_user_has_full_allowance(tmp_path):
    auth = _load_auth(tmp_path)
    user = _make_user(auth)
    u = auth.claude_run_usage(user)
    assert not u["unlimited"]
    assert u["used"] == 0
    assert u["limit"] == auth.CLAUDE_RUNS_PER_MONTH
    assert u["remaining"] == auth.CLAUDE_RUNS_PER_MONTH
    assert auth.has_claude_run_quota(user)


def test_consume_decrements_remaining(tmp_path):
    auth = _load_auth(tmp_path)
    user = _make_user(auth)
    auth.consume_claude_run(user["id"])
    auth.consume_claude_run(user["id"])
    u = auth.claude_run_usage(auth.find_user_by_id(user["id"]))
    assert u["used"] == 2
    assert u["remaining"] == auth.CLAUDE_RUNS_PER_MONTH - 2


def test_allowance_exhausts_then_blocks(tmp_path):
    auth = _load_auth(tmp_path)
    user = _make_user(auth)
    for _ in range(auth.CLAUDE_RUNS_PER_MONTH):
        assert auth.has_claude_run_quota(auth.find_user_by_id(user["id"]))
        auth.consume_claude_run(user["id"])
    fresh = auth.find_user_by_id(user["id"])
    u = auth.claude_run_usage(fresh)
    assert u["remaining"] == 0
    assert not auth.has_claude_run_quota(fresh)


def test_month_rollover_resets_counter(tmp_path):
    auth = _load_auth(tmp_path)
    user = _make_user(auth)
    auth.consume_claude_run(user["id"])
    auth.consume_claude_run(user["id"])
    # Force the stored period to a stale month.
    with auth._connect() as db:
        db.execute("UPDATE users SET usage_period = ? WHERE id = ?",
                   ("2000-01", user["id"]))
    u = auth.claude_run_usage(auth.find_user_by_id(user["id"]))
    assert u["used"] == 0
    assert u["remaining"] == auth.CLAUDE_RUNS_PER_MONTH
    assert u["period"] == datetime.now(timezone.utc).strftime("%Y-%m")


def test_grandfathered_user_is_unlimited(tmp_path):
    auth = _load_auth(tmp_path)
    user = _make_user(auth, email="owner@example.com")
    with auth._connect() as db:
        db.execute("UPDATE users SET is_grandfathered = 1 WHERE id = ?", (user["id"],))
    gf = auth.find_user_by_id(user["id"])
    u = auth.claude_run_usage(gf)
    assert u["unlimited"]
    assert auth.has_claude_run_quota(gf)
    # Even after many consumptions, a grandfathered user is never blocked.
    for _ in range(auth.CLAUDE_RUNS_PER_MONTH + 5):
        auth.consume_claude_run(gf["id"])
    assert auth.has_claude_run_quota(auth.find_user_by_id(gf["id"]))


def test_usage_limit_is_positive(tmp_path):
    auth = _load_auth(tmp_path)
    assert auth.CLAUDE_RUNS_PER_MONTH >= 1
