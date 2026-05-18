"""Outbound email for the bart website.

Today this carries one thing: creator-program application notifications to
the bart team. Kept tiny and dependency-free (stdlib ``smtplib``).

SMTP is configured entirely through environment variables:

    SMTP_HOST   default "smtp.gmail.com"
    SMTP_PORT   default 587 (STARTTLS)
    SMTP_USER   the sending account (e.g. bartcompanyai@gmail.com)
    SMTP_PASS   an app password for that account
    SMTP_FROM   optional explicit From: (defaults to SMTP_USER)

When SMTP is not configured, ``send_email`` logs the message to stderr and
returns ``False``. A missing mail config must never break a user action —
the caller still records the application in the database either way.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr


# Where creator-program applications are sent for review.
TEAM_EMAIL = "bartcompanyai@gmail.com"


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))


def send_email(to: str, subject: str, body: str, *, reply_to: str = "") -> bool:
    """Send a plain-text email. Returns True on success, False otherwise.

    Never raises — a delivery failure is logged and swallowed so the caller's
    primary action (e.g. saving an application) always completes.
    """
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM", "") or user

    if not (user and password):
        # Not configured — log so the message isn't silently lost, and the
        # operator can still see/act on it from the server logs.
        print(
            f"[emailer] SMTP not configured; would have emailed {to}\n"
            f"  subject: {subject}\n"
            f"  ---\n{body}\n  ---",
            file=sys.stderr, flush=True,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("bart", sender))
    msg["To"] = to
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 — delivery failure must not propagate
        print(f"[emailer] send to {to} failed: {e}", file=sys.stderr, flush=True)
        return False


def notify_creator_application(app: dict) -> bool:
    """Email the bart team a new creator-program application for review."""
    app_id = app.get("id", "?")
    name = app.get("name", "")
    email = app.get("email", "")
    subject = f"[bart creators] new application — {name or email}"
    body = (
        "A new bart creator-program application is waiting for review.\n\n"
        f"  Application #{app_id}\n"
        f"  Name:     {name}\n"
        f"  Email:    {email}\n"
        f"  Audience: {app.get('audience', '') or '(none given)'}\n"
        f"  Links:    {app.get('links', '') or '(none given)'}\n\n"
        "  Why they'd be a good fit:\n"
        f"  {app.get('pitch', '') or '(none given)'}\n\n"
        "Review it (sign in with the admin account first):\n"
        "  https://studywithbart.com/admin-creators\n\n"
        "Approving mints the creator a unique referral link and emails it "
        "to them automatically.\n"
    )
    return send_email(TEAM_EMAIL, subject, body, reply_to=email)
