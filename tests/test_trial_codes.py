"""Trial-code tests — single-use, admin-minted coupons that grant one free
premium packet generation, in website/auth.py.

Like test_creator_program.py, website/auth.py imports fastapi + bcrypt
(absent from bart's venv); they're stubbed so the pure sqlite logic can be
exercised in isolation.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AUTH_PATH = _REPO / "website" / "auth.py"


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
    spec = importlib.util.spec_from_file_location("bart_web_auth_trial", _AUTH_PATH)
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)
    auth.DB_PATH = tmp_path / "trial_test.db"
    auth.init_db()
    return auth


def _user(auth, email="creator@example.com"):
    return auth.create_user(email, "pw123456", "Creator")


# ── Minting ────────────────────────────────────────────────────────────

def test_mint_returns_a_usable_code(tmp_path):
    auth = _load_auth(tmp_path)
    code = auth.create_trial_code("for @studytips")
    assert code and code.isalnum() and code.upper() == code
    assert len(code) >= 10
    row = auth.get_trial_code(code)
    assert row is not None
    assert row["note"] == "for @studytips"
    assert row["redeemed_by"] is None


def test_minted_codes_are_distinct(tmp_path):
    auth = _load_auth(tmp_path)
    codes = {auth.create_trial_code() for _ in range(25)}
    assert len(codes) == 25


def test_list_trial_codes_newest_first(tmp_path):
    auth = _load_auth(tmp_path)
    auth.create_trial_code("first")
    auth.create_trial_code("second")
    rows = auth.list_trial_codes()
    assert len(rows) == 2
    # Both unredeemed → no redeemed_email.
    assert all(r["redeemed_by"] is None and r["redeemed_email"] is None for r in rows)


# ── Redemption grants a credit ─────────────────────────────────────────

def test_redeem_grants_one_credit(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    code = auth.create_trial_code()
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 0
    result = auth.redeem_trial_code(code, uid)
    assert result["trial_credits"] == 1
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 1
    # The code now records who spent it.
    row = auth.get_trial_code(code)
    assert row["redeemed_by"] == uid
    assert row["redeemed_at"] is not None


def test_redeem_is_case_insensitive(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    code = auth.create_trial_code()
    result = auth.redeem_trial_code(code.lower(), uid)
    assert result["trial_credits"] == 1


# ── A code is single-use, globally ─────────────────────────────────────

def test_code_cannot_be_reused_by_another_account(tmp_path):
    auth = _load_auth(tmp_path)
    code = auth.create_trial_code()
    first = _user(auth, "a@example.com")
    second = _user(auth, "b@example.com")
    auth.redeem_trial_code(code, first)
    with pytest.raises(auth.TrialCodeError):
        auth.redeem_trial_code(code, second)
    # The second account got nothing.
    assert auth.trial_credits(auth.find_user_by_id(second)) == 0


def test_code_cannot_be_reused_by_the_same_account(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    code = auth.create_trial_code()
    auth.redeem_trial_code(code, uid)
    with pytest.raises(auth.TrialCodeError):
        auth.redeem_trial_code(code, uid)
    # Still exactly one credit — the second attempt granted nothing.
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 1


def test_unknown_code_is_rejected(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    with pytest.raises(auth.TrialCodeError):
        auth.redeem_trial_code("NOTAREALCODE", uid)


def test_empty_code_is_rejected(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    with pytest.raises(auth.TrialCodeError):
        auth.redeem_trial_code("   ", uid)


# ── Spending a credit ──────────────────────────────────────────────────

def test_consume_trial_credit_decrements(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    auth.redeem_trial_code(auth.create_trial_code(), uid)
    assert auth.consume_trial_credit(uid) is True
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 0


def test_consume_trial_credit_never_goes_negative(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    # No credits → consume is a no-op, returns False, balance stays 0.
    assert auth.consume_trial_credit(uid) is False
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 0


def test_two_codes_stack_into_two_credits(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth)
    auth.redeem_trial_code(auth.create_trial_code(), uid)
    auth.redeem_trial_code(auth.create_trial_code(), uid)
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 2
    assert auth.consume_trial_credit(uid) is True
    assert auth.consume_trial_credit(uid) is True
    assert auth.consume_trial_credit(uid) is False  # exhausted
    assert auth.trial_credits(auth.find_user_by_id(uid)) == 0


# ── Admin view reflects redemption ─────────────────────────────────────

def test_list_shows_who_redeemed(tmp_path):
    auth = _load_auth(tmp_path)
    uid = _user(auth, "redeemer@example.com")
    code = auth.create_trial_code("minted for someone")
    auth.redeem_trial_code(code, uid)
    rows = auth.list_trial_codes()
    redeemed = next(r for r in rows if r["code"] == code)
    assert redeemed["redeemed_by"] == uid
    assert redeemed["redeemed_email"] == "redeemer@example.com"
