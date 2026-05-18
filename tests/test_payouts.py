"""Payouts schema tests — payouts table, commissions.payout_id, and
creator payout columns in website/auth.py.

website/auth.py imports fastapi + bcrypt (not in bart's venv); they're
stubbed so the pure sqlite logic can be tested in isolation.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AUTH_PATH = _REPO / "website" / "auth.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_auth(tmp_path: Path):
    if "fastapi" not in sys.modules:
        fake = types.ModuleType("fastapi")
        fake.Cookie = lambda *a, **k: None
        fake.HTTPException = type("HTTPException", (Exception,), {})
        fake.Response = type("Response", (), {})
        sys.modules["fastapi"] = fake
    if "bcrypt" not in sys.modules:
        fb = types.ModuleType("bcrypt")
        fb.hashpw = lambda pw, salt: b"hash"
        fb.gensalt = lambda: b"salt"
        fb.checkpw = lambda pw, h: True
        sys.modules["bcrypt"] = fb
    auth = _load_module("bart_web_auth_payouts", _AUTH_PATH)
    auth.DB_PATH = tmp_path / "creator_test.db"
    auth.init_db()
    return auth


def _apply(auth, email="creator@example.com", name="Sam Creator"):
    return auth.create_creator_application(
        name=name, email=email, audience="200k on TikTok",
        links="tiktok.com/@sam", pitch="I make study-tips videos for premeds",
    )


def _columns(auth, table):
    with auth._connect() as db:
        return {r["name"] for r in db.execute(
            f"PRAGMA table_info({table})").fetchall()}


def test_migration_adds_payout_columns_and_table(tmp_path):
    auth = _load_auth(tmp_path)
    creator_cols = _columns(auth, "creators")
    assert {"stripe_account_id", "payout_method",
            "payout_details", "payouts_enabled"} <= creator_cols
    assert "payout_id" in _columns(auth, "commissions")
    assert _columns(auth, "payouts") == {
        "id", "creator_id", "amount_cents", "currency", "method",
        "status", "stripe_transfer_id", "note", "period",
        "created_at", "paid_at",
    }


def test_init_db_is_idempotent(tmp_path):
    auth = _load_auth(tmp_path)
    auth.init_db()   # second call must not raise
    auth.init_db()
    assert "payout_id" in _columns(auth, "commissions")


def test_creator_earnings_breakdown(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    # two referred users, one subscribed-active, one not
    u1 = auth.create_user("u1@example.com", "pw123456", referred_by=code)
    u2 = auth.create_user("u2@example.com", "pw123456", referred_by=code)
    auth.update_subscription(u1, "sub_1", "active", None)
    # three $2 commissions for u1 over three months
    for i in range(3):
        auth.record_commission(creator["id"], u1, 200, "usd", f"cs_{i}")
    e = auth.creator_earnings(auth.get_creator_by_code(code))
    assert e["lifetime_earnings_cents"] == 600
    assert e["pending_balance_cents"] == 600     # none paid out yet
    assert e["paid_out_cents"] == 0
    assert e["total_referred"] == 2
    assert e["active_subscribers"] == 1
    assert e["monthly_run_rate_cents"] == 200    # 1 active sub * $2
    assert e["conversion_pct"] == 50.0
    assert e["this_month_cents"] == 600   # all three commissions are this month


def _seed_creator_with_balance(auth, cents, email="c@example.com"):
    creator = auth.approve_creator_application(
        auth.create_creator_application(
            name="C", email=email, audience="x", links="x", pitch="x"))
    uid = auth.create_user(f"sub-{email}", "pw123456",
                           referred_by=creator["referral_code"])
    n, rem = divmod(cents, 200)
    for i in range(n):
        auth.record_commission(creator["id"], uid, 200, "usd", f"cs-{email}-{i}")
    if rem:
        auth.record_commission(creator["id"], uid, rem, "usd", f"cs-{email}-r")
    return auth.get_creator_by_code(creator["referral_code"])


def test_run_payouts_skips_creators_below_minimum(tmp_path):
    auth = _load_auth(tmp_path)
    _seed_creator_with_balance(auth, 2000, "low@example.com")  # $20 < $25
    results = auth.run_payouts(minimum_cents=2500, period="2026-05")
    assert results == []


def test_run_payouts_manual_creates_pending_and_claims_commissions(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 3000, "ok@example.com")  # $30
    results = auth.run_payouts(minimum_cents=2500, period="2026-05")
    assert len(results) == 1
    assert results[0]["amount_cents"] == 3000
    assert results[0]["status"] == "pending"   # no transfer_fn -> manual
    assert auth.creator_earnings(creator)["pending_balance_cents"] == 0
    assert auth.run_payouts(minimum_cents=2500, period="2026-05") == []


def test_run_payouts_stripe_success_marks_paid(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 4000, "s@example.com")
    auth.set_creator_stripe_account(creator["id"], "acct_123")
    auth.set_creator_payouts_enabled(creator["id"], True)
    calls = []
    def transfer(creator_row, amount_cents):
        calls.append((creator_row["id"], amount_cents))
        return "tr_abc"
    results = auth.run_payouts(2500, "2026-05", transfer_fn=transfer)
    assert calls == [(creator["id"], 4000)]
    assert results[0]["status"] == "paid"
    assert results[0]["stripe_transfer_id"] == "tr_abc"


def test_run_payouts_stripe_failure_rolls_back(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 4000, "f@example.com")
    auth.set_creator_stripe_account(creator["id"], "acct_x")
    auth.set_creator_payouts_enabled(creator["id"], True)
    def transfer(creator_row, amount_cents):
        raise RuntimeError("stripe down")
    results = auth.run_payouts(2500, "2026-05", transfer_fn=transfer)
    assert results[0]["status"] == "failed"
    assert auth.creator_earnings(creator)["pending_balance_cents"] == 4000


def test_mark_payout_paid(tmp_path):
    auth = _load_auth(tmp_path)
    _seed_creator_with_balance(auth, 3000, "m@example.com")
    payout = auth.run_payouts(2500, "2026-05")[0]
    assert auth.mark_payout_paid(payout["id"], note="venmo sent") is True
    rows = auth.list_payouts()
    assert rows[0]["status"] == "paid" and rows[0]["paid_at"] is not None
    assert rows[0]["note"] == "venmo sent"
    assert auth.mark_payout_paid(payout["id"]) is False
    assert auth.mark_payout_paid(999999) is False
