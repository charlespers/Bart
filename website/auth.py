"""SQLite-backed auth and per-user run metadata for the bart website.

Single-file DB at <website>/bart.db. Three tables:
  users    (id, email UNIQUE, name, password_hash, created_at)
  sessions (id, user_id, created_at, expires_at)
  runs     (run_id, user_id, subject, focus, preset, days, created_at)

Session is a 256-bit random hex stored in the `bart_sid` cookie.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import Cookie, HTTPException, Response


HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "bart.db"
SESSION_COOKIE = "bart_sid"
SESSION_DAYS = 30


# ─── schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE,
  name          TEXT,
  password_hash TEXT,                       -- nullable: google-only accounts skip this
  created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id         TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS runs (
  run_id     TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  subject    TEXT,
  focus      TEXT,
  preset     TEXT,
  days       INTEGER,
  source_count INTEGER DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS friendships (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  requester_id INTEGER NOT NULL,
  recipient_id INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('pending','accepted')),
  created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  accepted_at  TEXT,
  UNIQUE (requester_id, recipient_id),
  CHECK (requester_id <> recipient_id),
  FOREIGN KEY (requester_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_friendships_recipient ON friendships(recipient_id, status);
CREATE INDEX IF NOT EXISTS idx_friendships_requester ON friendships(requester_id, status);

-- favorites: a run someone else shared with you that you've saved.
-- We snapshot owner_id + subject at save-time so the row stays readable
-- even if the original owner later revokes the share.
CREATE TABLE IF NOT EXISTS favorites (
  user_id        INTEGER NOT NULL,
  run_id         TEXT NOT NULL,
  owner_id       INTEGER NOT NULL,
  saved_subject  TEXT,
  saved_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, run_id),
  FOREIGN KEY (user_id)  REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (run_id)   REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id, saved_at DESC);

-- creator program: applications anyone can submit; we review them.
CREATE TABLE IF NOT EXISTS creator_applications (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  email       TEXT NOT NULL,
  audience    TEXT,            -- where / how they reach students
  links       TEXT,            -- channel / profile URLs
  pitch       TEXT,            -- why they'd be a good creator
  status      TEXT NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','approved','rejected')),
  user_id     INTEGER,         -- the account that applied, when logged in
  created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  decided_at  TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_creator_apps_status
  ON creator_applications(status, created_at DESC);

-- approved creators: each carries a unique referral code.
CREATE TABLE IF NOT EXISTS creators (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER UNIQUE,
  name          TEXT,
  email         TEXT NOT NULL,
  referral_code TEXT NOT NULL UNIQUE,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_creators_code ON creators(referral_code);
CREATE INDEX IF NOT EXISTS idx_creators_email ON creators(email);

-- commissions: one row per verified payment from a referred subscriber.
-- stripe_ref is the idempotency key — a webhook delivered twice never
-- double-credits a creator.
CREATE TABLE IF NOT EXISTS commissions (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id       INTEGER NOT NULL,
  referred_user_id INTEGER,
  amount_cents     INTEGER NOT NULL DEFAULT 0,
  currency         TEXT NOT NULL DEFAULT 'usd',
  stripe_ref       TEXT UNIQUE,
  created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE CASCADE,
  FOREIGN KEY (referred_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_commissions_creator
  ON commissions(creator_id, created_at DESC);
"""


def init_db() -> None:
    with _connect() as db:
        db.executescript(SCHEMA)
        # idempotent column add — sqlite doesn't support IF NOT EXISTS on
        # ALTER TABLE, so we check pragma_table_info first.
        cols = {row["name"] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
        if "share_token" not in cols:
            db.execute("ALTER TABLE runs ADD COLUMN share_token TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_share_token "
                "ON runs(share_token) WHERE share_token IS NOT NULL"
            )
        user_cols = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        if "google_id" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id "
                "ON users(google_id) WHERE google_id IS NOT NULL"
            )
        # Stripe / subscription columns — added in a second pass so existing rows
        # are back-filled with is_grandfathered=1 (every account that exists at
        # deploy time gets unlimited free access forever).
        billing_added = False
        if "stripe_customer_id" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_stripe_customer "
                "ON users(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL"
            )
            billing_added = True
        if "subscription_status" not in user_cols:
            # values: free | active | past_due | canceled | incomplete | trialing
            db.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT DEFAULT 'free'")
            billing_added = True
        if "subscription_id" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN subscription_id TEXT")
            billing_added = True
        if "current_period_end" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN current_period_end TEXT")
            billing_added = True
        if "is_grandfathered" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN is_grandfathered INTEGER DEFAULT 0")
            # Back-fill: only the owner gets free-forever access. Everyone else
            # (existing accounts and future signups) follows the paywall.
            db.execute(
                "UPDATE users SET is_grandfathered = 1 WHERE email = ?",
                ("loctran0323@gmail.com",),
            )
            billing_added = True
        # Metered usage — a $10/month subscription includes a monthly
        # allowance of Claude-powered (premium) runs; local Gemma runs are
        # always unlimited and free. `usage_period` is the "YYYY-MM" the
        # counter belongs to; a month rollover lazily resets the counter.
        if "claude_runs_used" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN claude_runs_used INTEGER DEFAULT 0")
            billing_added = True
        if "usage_period" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN usage_period TEXT")
            billing_added = True
        # Creator program — the referral code (if any) that brought this user.
        # Set once at signup; read when their payment is verified to credit
        # the referring creator.
        if "referred_by" not in user_cols:
            db.execute("ALTER TABLE users ADD COLUMN referred_by TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_referred_by "
                "ON users(referred_by) WHERE referred_by IS NOT NULL"
            )
            billing_added = True
        if billing_added:
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_subscription_id "
                "ON users(subscription_id) WHERE subscription_id IS NOT NULL"
            )


@contextmanager
def _connect():
    # timeout=10 — if another connection is mid-write, retry for up to 10s
    # instead of immediately raising "database is locked". busy_timeout below
    # is the SQLite-internal equivalent; both are belt-and-suspenders.
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    # WAL: readers don't block writers and vice-versa — the win for a
    # concurrent web server. synchronous=NORMAL is the recommended pair
    # (still fsyncs on commit, just not on every page write).
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA busy_timeout = 5000")
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ─── password ────────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False  # google-only accounts have no password to check
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ─── users ───────────────────────────────────────────────────────────────────

def create_user(email: str, password: str, name: str = "",
                 referred_by: str | None = None) -> int:
    email = email.strip().lower()
    ref = (referred_by or "").strip() or None
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO users (email, name, password_hash, referred_by) "
            "VALUES (?, ?, ?, ?)",
            (email, name.strip() or None, hash_password(password), ref),
        )
        return cur.lastrowid


def find_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with _connect() as db:
        return db.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()


def find_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with _connect() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ─── billing ─────────────────────────────────────────────────────────────────

# Subscription states that grant /api/run access. "trialing" included so we
# could enable a free trial later without changing the gate logic.
ACTIVE_STATUSES = ("active", "trialing")


def has_run_access(user) -> bool:
    """True iff this user is allowed to start a bart run.
    Grandfathered users always pass. Otherwise must be in an active state."""
    if user is None:
        return False
    # sqlite3.Row supports dict-like access but not .get(); be explicit.
    try:
        gf = user["is_grandfathered"]
    except (IndexError, KeyError):
        gf = 0
    if gf:
        return True
    try:
        status = user["subscription_status"] or "free"
    except (IndexError, KeyError):
        status = "free"
    return status in ACTIVE_STATUSES


def set_stripe_customer(user_id: int, customer_id: str) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
            (customer_id, user_id),
        )


def find_user_by_stripe_customer(customer_id: str) -> Optional[sqlite3.Row]:
    with _connect() as db:
        return db.execute(
            "SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()


def update_subscription(
    user_id: int,
    subscription_id: Optional[str],
    status: str,
    current_period_end: Optional[str],
) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE users SET subscription_id = ?, subscription_status = ?, "
            "current_period_end = ? WHERE id = ?",
            (subscription_id, status, current_period_end, user_id),
        )


# ─── metered usage ─────────────────────────────────────────────────────────────

# A $10/month subscription includes this many Claude-powered (premium-quality)
# runs per calendar month. Local Gemma runs are always free and unlimited, so a
# subscriber who exhausts the premium allowance can still generate full packets
# — the quality of any single packet is never reduced, only the premium engine
# is metered. Tunable per-deployment without a code change.
CLAUDE_RUNS_PER_MONTH = max(1, int(os.environ.get("BART_CLAUDE_RUNS_PER_MONTH", "12")))


def _current_period() -> str:
    """The billing period the usage counter belongs to — "YYYY-MM" (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _row_get(row, key, default=None):
    """sqlite3.Row has no .get(); read a possibly-absent column safely."""
    try:
        val = row[key]
    except (IndexError, KeyError):
        return default
    return default if val is None else val


def claude_run_usage(user) -> dict:
    """Return this user's Claude-run allowance status for the current month.

    Lazily rolls the counter over at a month boundary. The returned dict:
      { unlimited: bool, used: int, limit: int, remaining: int, period: str }

    Grandfathered accounts (and anyone without a tracked subscription row)
    report `unlimited=True`.
    """
    period = _current_period()
    if user is None:
        return {"unlimited": False, "used": 0, "limit": CLAUDE_RUNS_PER_MONTH,
                "remaining": 0, "period": period}

    if _row_get(user, "is_grandfathered", 0):
        return {"unlimited": True, "used": 0, "limit": CLAUDE_RUNS_PER_MONTH,
                "remaining": CLAUDE_RUNS_PER_MONTH, "period": period}

    user_id = user["id"]
    with _connect() as db:
        row = db.execute(
            "SELECT claude_runs_used, usage_period FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        used = _row_get(row, "claude_runs_used", 0) if row else 0
        stored_period = _row_get(row, "usage_period") if row else None
        if stored_period != period:
            # Month rollover (or first-ever run) — reset the counter.
            db.execute(
                "UPDATE users SET claude_runs_used = 0, usage_period = ? WHERE id = ?",
                (period, user_id),
            )
            used = 0

    used = max(0, int(used))
    remaining = max(0, CLAUDE_RUNS_PER_MONTH - used)
    return {"unlimited": False, "used": used, "limit": CLAUDE_RUNS_PER_MONTH,
            "remaining": remaining, "period": period}


def has_claude_run_quota(user) -> bool:
    """True iff the user may start another Claude-powered run this month."""
    usage = claude_run_usage(user)
    return usage["unlimited"] or usage["remaining"] > 0


def consume_claude_run(user_id: int) -> None:
    """Record that a Claude-powered run was started — decrements the monthly
    allowance. Resets the counter first if the stored period is stale, so a
    run that straddles a month boundary is always counted against the right
    month. No-op semantics for grandfathered users are handled by callers
    (they skip the quota path entirely)."""
    period = _current_period()
    with _connect() as db:
        row = db.execute(
            "SELECT claude_runs_used, usage_period FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return
        if _row_get(row, "usage_period") != period:
            db.execute(
                "UPDATE users SET claude_runs_used = 1, usage_period = ? WHERE id = ?",
                (period, user_id),
            )
        else:
            db.execute(
                "UPDATE users SET claude_runs_used = claude_runs_used + 1 WHERE id = ?",
                (user_id,),
            )


def find_or_create_google_user(google_id: str, email: str, name: str = "",
                               referred_by: str | None = None) -> sqlite3.Row:
    """Look up a Google-authenticated user, or create one. Matching rules:
      1. exact match on google_id → return that row
      2. exact match on email → link by stamping google_id onto the existing row
         (so an email/password user can later sign in with Google seamlessly)
      3. otherwise → create a new google-only user (password_hash stays NULL)
    Returns the resulting user row.
    """
    email = email.strip().lower()
    with _connect() as db:
        row = db.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
        if row:
            return row
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            db.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, row["id"]))
            return db.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        # Empty string (not NULL) — the existing DB was created when password_hash
        # was NOT NULL, and SQLite won't drop that constraint without a full table
        # migration. verify_password() rejects empty/falsy hashes so this is
        # equivalent to "no password" from a login perspective.
        cur = db.execute(
            "INSERT INTO users (email, name, password_hash, google_id, referred_by) "
            "VALUES (?, ?, '', ?, ?)",
            (email, name.strip() or None, google_id,
             (referred_by or "").strip() or None),
        )
        return db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


def update_password(user_id: int, new_password: str) -> None:
    with _connect() as db:
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )


def delete_sessions_for_user(user_id: int, keep_sid: Optional[str] = None) -> None:
    """Invalidate every session for a user — except optionally one to keep.
    Used after a password change so other devices are forced to re-log-in."""
    with _connect() as db:
        if keep_sid:
            db.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id <> ?",
                (user_id, keep_sid),
            )
        else:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ─── sessions ────────────────────────────────────────────────────────────────

def create_session(user_id: int) -> str:
    sid = secrets.token_hex(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with _connect() as db:
        db.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (sid, user_id, expires),
        )
    return sid


def delete_session(sid: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def _user_from_sid(sid: Optional[str]) -> Optional[sqlite3.Row]:
    if not sid:
        return None
    with _connect() as db:
        row = db.execute(
            "SELECT u.* FROM users u "
            "JOIN sessions s ON s.user_id = u.id "
            "WHERE s.id = ? AND s.expires_at > ?",
            (sid, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return row


def set_session_cookie(resp: Response, sid: str, secure: bool = False) -> None:
    """`secure` should be True when the cookie is being sent over HTTPS.
    Caller (server.py) computes this from request.url.scheme so we honour
    proxy headers (Fly / Render set X-Forwarded-Proto)."""
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=sid,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/")


# ─── FastAPI dependencies ────────────────────────────────────────────────────

async def current_user_optional(bart_sid: Optional[str] = Cookie(default=None)) -> Optional[sqlite3.Row]:
    return _user_from_sid(bart_sid)


async def current_user(bart_sid: Optional[str] = Cookie(default=None)) -> sqlite3.Row:
    row = _user_from_sid(bart_sid)
    if row is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return row


# ─── runs ────────────────────────────────────────────────────────────────────

def record_run(
    run_id: str,
    user_id: int,
    subject: str = "",
    focus: str = "",
    preset: str = "default",
    days: int = 7,
    source_count: int = 0,
) -> None:
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO runs (run_id, user_id, subject, focus, preset, days, source_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, user_id, subject, focus, preset, days, source_count),
        )


def list_runs(user_id: int) -> list[dict]:
    with _connect() as db:
        rows = db.execute(
            "SELECT run_id, subject, focus, preset, days, source_count, share_token, created_at "
            "FROM runs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def runs_eligible_for_gc(user_id: int, keep: int) -> list[str]:
    """Return oldest-first run_ids that should be deleted to keep at most `keep`
    runs for this user. Excludes runs that are shared (share_token set) or
    favorited by anyone — those are someone's saved content, so we'd rather
    keep a few extra MB than silently delete them out from under a friend.
    """
    with _connect() as db:
        rows = db.execute(
            "SELECT r.run_id FROM runs r "
            "WHERE r.user_id = ? "
            "  AND (r.share_token IS NULL) "
            "  AND NOT EXISTS (SELECT 1 FROM favorites f WHERE f.run_id = r.run_id) "
            "ORDER BY r.created_at DESC",
            (user_id,),
        ).fetchall()
    # Skip the newest `keep`; everything older is fair game.
    return [r["run_id"] for r in rows[keep:]]


def runs_per_user_top(limit: int = 20) -> list[dict]:
    """Top users by run count — for admin/GC tuning."""
    with _connect() as db:
        rows = db.execute(
            "SELECT u.id, u.email, COUNT(r.run_id) AS runs "
            "FROM users u LEFT JOIN runs r ON r.user_id = u.id "
            "GROUP BY u.id ORDER BY runs DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"user_id": r["id"], "email": r["email"], "runs": r["runs"]} for r in rows]


def delete_run_row(run_id: str, user_id: int) -> bool:
    """Delete a run row owned by user_id. Cascades to favorites (via FK)."""
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        )
        return cur.rowcount > 0


def run_owner(run_id: str) -> Optional[int]:
    with _connect() as db:
        row = db.execute(
            "SELECT user_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return row["user_id"] if row else None


# ─── sharing ─────────────────────────────────────────────────────────────────

def create_share(run_id: str, user_id: int) -> Optional[str]:
    """Generate (or rotate) a share token for a run the caller owns.
    Returns the token, or None if the run isn't owned by user_id."""
    token = secrets.token_urlsafe(18)  # ~24 chars, URL-safe
    with _connect() as db:
        cur = db.execute(
            "UPDATE runs SET share_token = ? WHERE run_id = ? AND user_id = ?",
            (token, run_id, user_id),
        )
        if cur.rowcount == 0:
            return None
    return token


def revoke_share(run_id: str, user_id: int) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE runs SET share_token = NULL WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        )
        return cur.rowcount > 0


def get_share_token(run_id: str, user_id: int) -> Optional[str]:
    with _connect() as db:
        row = db.execute(
            "SELECT share_token FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
    return row["share_token"] if row and row["share_token"] else None


def run_by_share_token(token: str) -> Optional[sqlite3.Row]:
    """Resolve a share token back to its run record (run_id, user_id, ...)."""
    with _connect() as db:
        return db.execute(
            "SELECT * FROM runs WHERE share_token = ?", (token,)
        ).fetchone()


# ─── friendships ─────────────────────────────────────────────────────────────

class FriendError(Exception): ...


def send_friend_request(requester_id: int, recipient_email: str) -> dict:
    """Create a pending request. Returns the friendship row as a dict.
    Raises FriendError with a user-readable reason on the common failures."""
    recipient = find_user_by_email(recipient_email)
    if not recipient:
        raise FriendError("no account with that email — ask them to sign up first.")
    if recipient["id"] == requester_id:
        raise FriendError("you can't friend yourself.")
    with _connect() as db:
        # already friends or pending in either direction? short-circuit.
        existing = db.execute(
            "SELECT id, requester_id, recipient_id, status FROM friendships "
            "WHERE (requester_id=? AND recipient_id=?) "
            "   OR (requester_id=? AND recipient_id=?)",
            (requester_id, recipient["id"], recipient["id"], requester_id),
        ).fetchone()
        if existing:
            if existing["status"] == "accepted":
                raise FriendError("you're already friends.")
            # pending: if THEY requested US, auto-accept; otherwise it's a dup.
            if existing["requester_id"] == recipient["id"]:
                db.execute(
                    "UPDATE friendships SET status='accepted', accepted_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (existing["id"],),
                )
                row = db.execute("SELECT * FROM friendships WHERE id=?", (existing["id"],)).fetchone()
                return dict(row)
            raise FriendError("you've already sent them a request.")
        cur = db.execute(
            "INSERT INTO friendships (requester_id, recipient_id, status) VALUES (?, ?, 'pending')",
            (requester_id, recipient["id"]),
        )
        row = db.execute("SELECT * FROM friendships WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_friends(user_id: int) -> dict:
    """Return {accepted, incoming, outgoing} — each a list of {id, user: {id,email,name}, since}."""
    with _connect() as db:
        rows = db.execute(
            "SELECT f.id, f.requester_id, f.recipient_id, f.status, f.created_at, f.accepted_at, "
            "       u.id AS u_id, u.email AS u_email, u.name AS u_name "
            "FROM friendships f "
            "JOIN users u ON u.id = CASE WHEN f.requester_id=? THEN f.recipient_id ELSE f.requester_id END "
            "WHERE f.requester_id=? OR f.recipient_id=? "
            "ORDER BY f.created_at DESC",
            (user_id, user_id, user_id),
        ).fetchall()
    accepted, incoming, outgoing = [], [], []
    for r in rows:
        entry = {
            "id":    r["id"],
            "user":  {"id": r["u_id"], "email": r["u_email"], "name": r["u_name"]},
            "since": r["accepted_at"] or r["created_at"],
        }
        if r["status"] == "accepted":
            accepted.append(entry)
        elif r["recipient_id"] == user_id:
            incoming.append(entry)
        else:
            outgoing.append(entry)
    return {"accepted": accepted, "incoming": incoming, "outgoing": outgoing}


def accept_friend_request(friendship_id: int, user_id: int) -> bool:
    """Accept a pending request — only the recipient can accept."""
    with _connect() as db:
        cur = db.execute(
            "UPDATE friendships SET status='accepted', accepted_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND recipient_id=? AND status='pending'",
            (friendship_id, user_id),
        )
        return cur.rowcount > 0


def decline_friend_request(friendship_id: int, user_id: int) -> bool:
    """Decline (delete) a pending request — only the recipient can decline."""
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM friendships WHERE id=? AND recipient_id=? AND status='pending'",
            (friendship_id, user_id),
        )
        return cur.rowcount > 0


def unfriend(user_id: int, other_user_id: int) -> bool:
    """Remove an accepted friendship in either direction."""
    with _connect() as db:
        cur = db.execute(
            "DELETE FROM friendships "
            "WHERE status='accepted' AND ((requester_id=? AND recipient_id=?) OR (requester_id=? AND recipient_id=?))",
            (user_id, other_user_id, other_user_id, user_id),
        )
        return cur.rowcount > 0


# ─── favorites ───────────────────────────────────────────────────────────────

def save_favorite(user_id: int, share_token: str) -> dict:
    """Save someone else's shared packet to my favorites. Returns the saved row.
    Raises FriendError on bad token / saving your own / dup."""
    run = run_by_share_token(share_token)
    if not run:
        raise FriendError("that share link is invalid or was revoked.")
    if run["user_id"] == user_id:
        raise FriendError("you can't favorite your own packet.")
    with _connect() as db:
        try:
            db.execute(
                "INSERT INTO favorites (user_id, run_id, owner_id, saved_subject) "
                "VALUES (?, ?, ?, ?)",
                (user_id, run["run_id"], run["user_id"], run["subject"] or ""),
            )
        except sqlite3.IntegrityError:
            raise FriendError("you've already saved this one.")
        row = db.execute(
            "SELECT * FROM favorites WHERE user_id=? AND run_id=?",
            (user_id, run["run_id"]),
        ).fetchone()
    return dict(row)


def list_favorites(user_id: int) -> list[dict]:
    """List saved packets joined with current share_token + owner email so the
    UI can show "from <owner>" and link to /share/<token>. share_token is NULL
    if the owner revoked the share since you saved it."""
    with _connect() as db:
        rows = db.execute(
            "SELECT f.run_id, f.owner_id, f.saved_subject, f.saved_at, "
            "       r.share_token, u.email AS owner_email, u.name AS owner_name "
            "FROM favorites f "
            "JOIN runs  r ON r.run_id = f.run_id "
            "JOIN users u ON u.id     = f.owner_id "
            "WHERE f.user_id=? "
            "ORDER BY f.saved_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def remove_favorite(user_id: int, run_id: str) -> bool:
    with _connect() as db:
        cur = db.execute("DELETE FROM favorites WHERE user_id=? AND run_id=?", (user_id, run_id))
        return cur.rowcount > 0


# ─── leaderboard ─────────────────────────────────────────────────────────────

def leaderboard_for(user_id: int) -> list[dict]:
    """Return stats for me + every friend, ordered by packets_created DESC.
    Stats: packets_created, files_uploaded, days_active, last_active."""
    with _connect() as db:
        # 1. collect user_ids: me + my accepted friends.
        friend_ids = [
            row["other"] for row in db.execute(
                "SELECT CASE WHEN requester_id=? THEN recipient_id ELSE requester_id END AS other "
                "FROM friendships WHERE status='accepted' AND (requester_id=? OR recipient_id=?)",
                (user_id, user_id, user_id),
            ).fetchall()
        ]
        all_ids = [user_id, *friend_ids]
        if not all_ids:
            return []
        placeholders = ",".join("?" * len(all_ids))
        rows = db.execute(
            f"""SELECT u.id, u.email, u.name,
                       COUNT(r.run_id)                       AS packets_created,
                       COALESCE(SUM(r.source_count), 0)      AS files_uploaded,
                       COUNT(DISTINCT date(r.created_at))    AS days_active,
                       MAX(r.created_at)                     AS last_active
                FROM users u
                LEFT JOIN runs r ON r.user_id = u.id
                WHERE u.id IN ({placeholders})
                GROUP BY u.id
                ORDER BY packets_created DESC, files_uploaded DESC, days_active DESC""",
            all_ids,
        ).fetchall()
    return [
        {
            "id":              r["id"],
            "email":           r["email"],
            "name":            r["name"],
            "is_you":          r["id"] == user_id,
            "packets_created": r["packets_created"],
            "files_uploaded":  r["files_uploaded"],
            "days_active":     r["days_active"],
            "last_active":     r["last_active"],
        }
        for r in rows
    ]


# ─── creator program ───────────────────────────────────────────────────────────

# Share of each verified subscription payment paid to the referring creator.
# Tunable per-deployment; 0.30 = 30%.
CREATOR_COMMISSION_RATE = max(0.0, min(1.0, float(
    os.environ.get("BART_CREATOR_COMMISSION_RATE", "0.30"))))

# Referral codes: unambiguous uppercase alphabet (no 0/O, 1/I) — easy to read,
# type, and say aloud.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def _gen_referral_code() -> str:
    """A referral code unique across the creators table."""
    with _connect() as db:
        for _ in range(40):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
            hit = db.execute(
                "SELECT 1 FROM creators WHERE referral_code = ?", (code,)
            ).fetchone()
            if hit is None:
                return code
    # Astronomically unlikely — fall back to a longer code.
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN + 6))


# ── applications ──

def create_creator_application(
    name: str, email: str, audience: str = "", links: str = "",
    pitch: str = "", user_id: Optional[int] = None,
) -> int:
    """Store a creator-program application. Returns the new application id."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO creator_applications "
            "(name, email, audience, links, pitch, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name.strip(), email.strip().lower(), audience.strip(),
             links.strip(), pitch.strip(), user_id),
        )
        return cur.lastrowid


def latest_application_for_email(email: str) -> Optional[sqlite3.Row]:
    """Most recent application for an email — used to show applicants their
    status and to stop duplicate pending applications."""
    with _connect() as db:
        return db.execute(
            "SELECT * FROM creator_applications WHERE email = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (email.strip().lower(),),
        ).fetchone()


def list_creator_applications(status: Optional[str] = "pending") -> list[dict]:
    """Applications for the admin review queue. status=None → all."""
    with _connect() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM creator_applications WHERE status = ? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM creator_applications ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_creator_application(app_id: int) -> Optional[sqlite3.Row]:
    with _connect() as db:
        return db.execute(
            "SELECT * FROM creator_applications WHERE id = ?", (app_id,)
        ).fetchone()


# ── creators ──

def get_creator_by_code(code: str) -> Optional[sqlite3.Row]:
    if not code:
        return None
    with _connect() as db:
        return db.execute(
            "SELECT * FROM creators WHERE referral_code = ? AND status = 'active'",
            (code.strip().upper(),),
        ).fetchone()


def referral_code_is_valid(code: str) -> bool:
    """True iff `code` maps to an active creator — guards what we'll store on
    a user's `referred_by` and credit later."""
    return get_creator_by_code(code) is not None


def get_creator_by_email(email: str) -> Optional[sqlite3.Row]:
    if not email:
        return None
    with _connect() as db:
        return db.execute(
            "SELECT * FROM creators WHERE email = ?", (email.strip().lower(),)
        ).fetchone()


def get_creator_for_user(user) -> Optional[sqlite3.Row]:
    """The creator record for a logged-in user — matched by account id or by
    the email they applied/were approved with."""
    if user is None:
        return None
    try:
        uid = user["id"]
        email = (user["email"] or "").strip().lower()
    except (KeyError, IndexError, TypeError):
        return None
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM creators WHERE user_id = ?", (uid,)
        ).fetchone()
        if row is None and email:
            row = db.execute(
                "SELECT * FROM creators WHERE email = ?", (email,)
            ).fetchone()
            # Opportunistically bind the account so future lookups are direct.
            if row is not None and row["user_id"] is None:
                db.execute(
                    "UPDATE creators SET user_id = ? WHERE id = ?", (uid, row["id"])
                )
        return row


def approve_creator_application(app_id: int) -> Optional[dict]:
    """Approve an application: mark it approved and create the creator record
    (idempotent — re-approving returns the existing creator). Returns a dict
    with the creator + referral_code, or None if the application is missing."""
    app = get_creator_application(app_id)
    if app is None:
        return None
    email = (app["email"] or "").strip().lower()
    with _connect() as db:
        existing = db.execute(
            "SELECT * FROM creators WHERE email = ?", (email,)
        ).fetchone()
        if existing is not None:
            db.execute(
                "UPDATE creator_applications SET status = 'approved', "
                "decided_at = CURRENT_TIMESTAMP WHERE id = ?", (app_id,),
            )
            return dict(existing)
    code = _gen_referral_code()
    # Link to an account if one already exists for this email.
    linked = find_user_by_email(email)
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO creators (user_id, name, email, referral_code) "
            "VALUES (?, ?, ?, ?)",
            (linked["id"] if linked else None, app["name"], email, code),
        )
        db.execute(
            "UPDATE creator_applications SET status = 'approved', "
            "decided_at = CURRENT_TIMESTAMP WHERE id = ?", (app_id,),
        )
        row = db.execute(
            "SELECT * FROM creators WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def reject_creator_application(app_id: int) -> bool:
    with _connect() as db:
        cur = db.execute(
            "UPDATE creator_applications SET status = 'rejected', "
            "decided_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (app_id,),
        )
        return cur.rowcount > 0


# ── commissions ──

def record_commission(
    creator_id: int, referred_user_id: Optional[int], amount_cents: int,
    currency: str, stripe_ref: str,
) -> bool:
    """Credit a creator for a verified payment. Idempotent on `stripe_ref` —
    a webhook delivered twice (Stripe retries) never double-pays. Returns
    True iff a new commission row was actually inserted."""
    if not stripe_ref:
        return False
    with _connect() as db:
        dup = db.execute(
            "SELECT 1 FROM commissions WHERE stripe_ref = ?", (stripe_ref,)
        ).fetchone()
        if dup is not None:
            return False
        db.execute(
            "INSERT INTO commissions "
            "(creator_id, referred_user_id, amount_cents, currency, stripe_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            (creator_id, referred_user_id, max(0, int(amount_cents)),
             (currency or "usd").lower(), stripe_ref),
        )
    return True


def creator_summary(creator) -> dict:
    """Public-facing stats for a creator's dashboard: referral link inputs,
    how many people they've referred, how many subscribed, and lifetime
    earnings."""
    code = creator["referral_code"]
    cid = creator["id"]
    with _connect() as db:
        signups = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?", (code,)
        ).fetchone()["n"]
        comm = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents), 0) AS cents "
            "FROM commissions WHERE creator_id = ?", (cid,)
        ).fetchone()
        subscribed = db.execute(
            "SELECT COUNT(DISTINCT referred_user_id) AS n "
            "FROM commissions WHERE creator_id = ?", (cid,)
        ).fetchone()["n"]
    return {
        "referral_code": code,
        "name": creator["name"],
        "signups": signups,
        "subscribed": subscribed,
        "payments": comm["n"],
        "earnings_cents": comm["cents"],
        "earnings_usd": round(comm["cents"] / 100.0, 2),
        "commission_rate": CREATOR_COMMISSION_RATE,
    }
