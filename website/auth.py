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
  password_hash TEXT NOT NULL,
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
"""


def init_db() -> None:
    with _connect() as db:
        db.executescript(SCHEMA)


@contextmanager
def _connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ─── password ────────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(pw: str, hashed: str) -> bool:
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


def set_session_cookie(resp: Response, sid: str) -> None:
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=sid,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        # secure=False for local dev — flip to True behind HTTPS.
        secure=False,
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
            "SELECT run_id, subject, focus, preset, days, source_count, created_at "
            "FROM runs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def run_owner(run_id: str) -> Optional[int]:
    with _connect() as db:
        row = db.execute(
            "SELECT user_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return row["user_id"] if row else None
