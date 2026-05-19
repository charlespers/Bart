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


def notify_creator_new_subscriber(creator_email: str, creator_name: str,
                                  amount_cents: int) -> bool:
    """Tell a creator a referred user just subscribed (their first payment)."""
    if not creator_email:
        return False
    dollars = (amount_cents or 0) / 100
    subject = "someone just subscribed through your bart link"
    body = (
        f"Hi {creator_name or 'there'},\n\n"
        "Good news — someone subscribed to bart through your referral link.\n\n"
        f"You earned ${dollars:.2f}, and you'll keep earning every month they "
        "stay subscribed. See your earnings on your creator dashboard:\n"
        "  https://studywithbart.com/creators\n\n"
        "— the bart team\n"
    )
    return send_email(creator_email, subject, body)


def notify_creator_payout(creator_email: str, creator_name: str,
                          amount_cents: int, method: str) -> bool:
    """Tell a creator a payout has been sent."""
    if not creator_email:
        return False
    dollars = (amount_cents or 0) / 100
    how = ("to your connected bank account" if method == "stripe"
           else "— the bart team will send it to your payout handle")
    subject = f"your bart creator payout — ${dollars:.2f}"
    body = (
        f"Hi {creator_name or 'there'},\n\n"
        f"We've sent your bart creator payout of ${dollars:.2f} {how}.\n\n"
        "Thanks for helping students find bart.\n\n"
        "— the bart team\n"
    )
    return send_email(creator_email, subject, body)


def notify_creator_monthly_summary(creator_email: str, creator_name: str,
                                   summary: dict) -> bool:
    """Send a creator their monthly earnings digest. `summary` keys:
    this_month_cents, active_subscribers, commission_cents, pending_balance_cents."""
    if not creator_email:
        return False
    earned = (summary.get("this_month_cents", 0) or 0) / 100
    rate = (summary.get("commission_cents", 0) or 0) / 100
    balance = (summary.get("pending_balance_cents", 0) or 0) / 100
    subject = "your bart creator month in review"
    body = (
        f"Hi {creator_name or 'there'},\n\n"
        "Here's your bart creator month:\n\n"
        f"  Earned this month:   ${earned:.2f}\n"
        f"  Active subscribers:  {summary.get('active_subscribers', 0)}\n"
        f"  Your rate:           ${rate:.2f} per subscriber / month\n"
        f"  Pending balance:     ${balance:.2f}\n\n"
        "Full detail on your dashboard:\n"
        "  https://studywithbart.com/creators\n\n"
        "— the bart team\n"
    )
    return send_email(creator_email, subject, body)
