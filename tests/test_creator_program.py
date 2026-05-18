"""Creator-program tests — applications, approval, referral codes, and
verified-payment commissions in website/auth.py.

website/auth.py imports fastapi + bcrypt (not in bart's venv); they're
stubbed so the pure sqlite logic can be tested in isolation. The emailer
module is stdlib-only and loaded directly.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_AUTH_PATH = _REPO / "website" / "auth.py"
_EMAILER_PATH = _REPO / "website" / "emailer.py"


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
    auth = _load_module("bart_web_auth_creator", _AUTH_PATH)
    auth.DB_PATH = tmp_path / "creator_test.db"
    auth.init_db()
    return auth


def _apply(auth, email="creator@example.com", name="Sam Creator"):
    return auth.create_creator_application(
        name=name, email=email, audience="200k on TikTok",
        links="tiktok.com/@sam", pitch="I make study-tips videos for premeds",
    )


# ── Applications ───────────────────────────────────────────────────────

def test_application_is_stored_and_listed(tmp_path):
    auth = _load_auth(tmp_path)
    app_id = _apply(auth)
    assert app_id > 0
    pending = auth.list_creator_applications("pending")
    assert len(pending) == 1
    assert pending[0]["email"] == "creator@example.com"
    assert pending[0]["status"] == "pending"


def test_latest_application_for_email(tmp_path):
    auth = _load_auth(tmp_path)
    _apply(auth)
    row = auth.latest_application_for_email("creator@example.com")
    assert row is not None and row["status"] == "pending"
    assert auth.latest_application_for_email("nobody@example.com") is None


# ── Approval → creator + referral code ─────────────────────────────────

def test_approve_creates_creator_with_unique_code(tmp_path):
    auth = _load_auth(tmp_path)
    app_id = _apply(auth)
    creator = auth.approve_creator_application(app_id)
    assert creator is not None
    code = creator["referral_code"]
    assert len(code) >= 8 and code.isalnum() and code.upper() == code
    # Application is now marked approved.
    assert auth.get_creator_application(app_id)["status"] == "approved"
    # Code resolves back to the creator.
    assert auth.get_creator_by_code(code)["id"] == creator["id"]
    assert auth.referral_code_is_valid(code)
    assert not auth.referral_code_is_valid("NOTACODE")


def test_approve_is_idempotent(tmp_path):
    auth = _load_auth(tmp_path)
    app_id = _apply(auth)
    c1 = auth.approve_creator_application(app_id)
    c2 = auth.approve_creator_application(app_id)
    assert c1["referral_code"] == c2["referral_code"]
    # Exactly one creator row for this email.
    assert auth.get_creator_by_email("creator@example.com") is not None


def test_reject_application(tmp_path):
    auth = _load_auth(tmp_path)
    app_id = _apply(auth)
    assert auth.reject_creator_application(app_id) is True
    assert auth.get_creator_application(app_id)["status"] == "rejected"
    # Re-rejecting a non-pending application is a no-op.
    assert auth.reject_creator_application(app_id) is False


def test_referral_codes_are_distinct_across_creators(tmp_path):
    auth = _load_auth(tmp_path)
    a = auth.approve_creator_application(_apply(auth, "a@example.com"))
    b = auth.approve_creator_application(_apply(auth, "b@example.com"))
    assert a["referral_code"] != b["referral_code"]


# ── Referral attribution ───────────────────────────────────────────────

def test_referred_user_records_code(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    uid = auth.create_user("student@example.com", "pw123456", "Student",
                           referred_by=code)
    row = auth.find_user_by_id(uid)
    assert row["referred_by"] == code


# ── Commissions: only on verified payment, idempotent ──────────────────

def test_commission_credits_creator(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    uid = auth.create_user("s@example.com", "pw123456", "S")
    # Flat $2.00 commission per verified payment.
    assert auth.CREATOR_COMMISSION_CENTS == 200
    inserted = auth.record_commission(
        creator_id=creator["id"], referred_user_id=uid,
        amount_cents=auth.CREATOR_COMMISSION_CENTS, currency="usd",
        stripe_ref="cs_test_1",
    )
    assert inserted is True
    summary = auth.creator_summary(auth.get_creator_by_code(creator["referral_code"]))
    assert summary["earnings_cents"] == auth.CREATOR_COMMISSION_CENTS
    assert summary["payments"] == 1
    assert summary["subscribed"] == 1


def test_commission_cents_is_env_overridable_and_positive(tmp_path, monkeypatch):
    monkeypatch.setenv("BART_CREATOR_COMMISSION_CENTS", "350")
    auth = _load_auth(tmp_path)
    assert isinstance(auth.CREATOR_COMMISSION_CENTS, int)
    assert auth.CREATOR_COMMISSION_CENTS == 350


def test_commission_is_idempotent_on_stripe_ref(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    uid = auth.create_user("s@example.com", "pw123456", "S")
    first = auth.record_commission(creator["id"], uid, 300, "usd", "cs_dup")
    second = auth.record_commission(creator["id"], uid, 300, "usd", "cs_dup")
    assert first is True
    assert second is False  # retried webhook → no double credit
    summary = auth.creator_summary(auth.get_creator_by_code(creator["referral_code"]))
    assert summary["earnings_cents"] == 300
    assert summary["payments"] == 1


def test_commission_requires_stripe_ref(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    assert auth.record_commission(creator["id"], None, 300, "usd", "") is False


def test_creator_summary_counts_signups(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    auth.create_user("s1@example.com", "pw123456", referred_by=code)
    auth.create_user("s2@example.com", "pw123456", referred_by=code)
    auth.create_user("s3@example.com", "pw123456")  # not referred
    summary = auth.creator_summary(auth.get_creator_by_code(code))
    assert summary["signups"] == 2
    assert summary["commission_cents"] == 200


# ── Emailer (graceful when SMTP not configured) ────────────────────────

def test_emailer_graceful_without_smtp(monkeypatch):
    for var in ("SMTP_USER", "SMTP_PASS"):
        monkeypatch.delenv(var, raising=False)
    emailer = _load_module("bart_web_emailer", _EMAILER_PATH)
    assert emailer.smtp_configured() is False
    # Unconfigured → returns False, never raises.
    assert emailer.send_email("x@example.com", "subj", "body") is False
    assert emailer.notify_creator_application(
        {"id": 1, "name": "A", "email": "a@example.com"}
    ) is False
    assert emailer.TEAM_EMAIL == "bartcompanyai@gmail.com"
