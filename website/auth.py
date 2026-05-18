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

def create_user(email: str, password: str, name: str = "") -> int:
    email = email.strip().lower()
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)",
            (email, name.strip() or None, hash_password(password)),
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


def find_or_create_google_user(google_id: str, email: str, name: str = "") -> sqlite3.Row:
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
            "INSERT INTO users (email, name, password_hash, google_id) VALUES (?, ?, '', ?)",
            (email, name.strip() or None, google_id),
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
