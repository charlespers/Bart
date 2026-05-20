"""Outbound email for the bart website.

Today this carries: creator-program application notifications to the team,
approval/payout/summary emails back to creators, and admin-delivered trial
codes. Two delivery backends are supported; the first one configured wins:

    1. Resend HTTP API — set ``RESEND_API_KEY``. Optional ``RESEND_FROM``
       (defaults to ``"bart <onboarding@resend.dev>"`` so you can send
       immediately without verifying a domain).
    2. SMTP — set ``SMTP_USER`` + ``SMTP_PASS`` (and optionally
       ``SMTP_HOST``/``SMTP_PORT``/``SMTP_FROM``).

When neither is configured ``send_email`` logs to stderr and returns
``False``. A missing mail config must never break a user action — the
caller still records the application/code in the database either way.
"""
from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr


# Where creator-program applications are sent for review.
TEAM_EMAIL = "bartcompanyai@gmail.com"

# Resend's shared sandbox sender — works out of the box for testing without
# verifying a domain. For production, set RESEND_FROM to a sender on a
# domain you've verified in the Resend dashboard.
_DEFAULT_RESEND_FROM = "bart <onboarding@resend.dev>"


def resend_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))


def email_configured() -> bool:
    return resend_configured() or smtp_configured()


def _send_via_resend(to: str, subject: str, body: str, *, reply_to: str = "") -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    sender = os.environ.get("RESEND_FROM", "") or _DEFAULT_RESEND_FROM
    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= resp.status < 300:
                return True
            print(
                f"[emailer] resend HTTP {resp.status} sending to {to}",
                file=sys.stderr, flush=True,
            )
            return False
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001 — diagnostic best-effort
            pass
        print(
            f"[emailer] resend send to {to} failed: HTTP {e.code} {detail}",
            file=sys.stderr, flush=True,
        )
        return False
    except Exception as e:  # noqa: BLE001 — delivery failure must not propagate
        print(f"[emailer] resend send to {to} failed: {e}", file=sys.stderr, flush=True)
        return False


def _send_via_smtp(to: str, subject: str, body: str, *, reply_to: str = "") -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM", "") or user

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
        print(f"[emailer] smtp send to {to} failed: {e}", file=sys.stderr, flush=True)
        return False


def send_email(to: str, subject: str, body: str, *, reply_to: str = "") -> bool:
    """Send a plain-text email. Returns True on success, False otherwise.

    Never raises — a delivery failure is logged and swallowed so the caller's
    primary action (e.g. saving an application) always completes. Prefers
    Resend when configured, falls back to SMTP, then to a stderr-logged
    no-op so the message is at least visible in server logs.
    """
    if resend_configured():
        return _send_via_resend(to, subject, body, reply_to=reply_to)
    if smtp_configured():
        return _send_via_smtp(to, subject, body, reply_to=reply_to)

    print(
        f"[emailer] no email backend configured; would have emailed {to}\n"
        f"  subject: {subject}\n"
        f"  ---\n{body}\n  ---",
        file=sys.stderr, flush=True,
    )
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


def send_trial_code(recipient_email: str, code: str, *,
                    recipient_name: str = "", note: str = "") -> bool:
    """Email a freshly-minted trial code to a recipient. `note` is admin-only
    context (e.g. which creator pitch this code is for) and is NOT included
    in the body. Returns True on delivery."""
    if not recipient_email:
        return False
    hi = f"Hi {recipient_name}," if recipient_name else "Hi,"
    subject = "your free bart packet — one-time trial code"
    body = (
        f"{hi}\n\n"
        "Here's a one-time code to try bart on us — it's good for one full "
        "premium (Claude-powered) study packet, no subscription needed.\n\n"
        f"  Your code:  {code}\n\n"
        "How to redeem:\n"
        "  1. Sign up (or sign in) at https://studywithbart.com/creators\n"
        "  2. Paste the code into the \"have a trial code?\" box and hit redeem.\n"
        "  3. Open the app, drop your materials in, and let bart cook.\n\n"
        "The code works exactly once, on any account — so don't share it.\n\n"
        "— the bart team\n"
    )
    return send_email(recipient_email, subject, body)


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
