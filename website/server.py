"""bart website backend — serves splash/login/app and drives bart runs.

Auth (SQLite + cookie sessions):
  POST /api/auth/signup    create account + log in
  POST /api/auth/login     log in
  POST /api/auth/logout    log out
  GET  /api/auth/me        current user (or 401)

Static pages:
  GET  /                   splash.html (public)
  GET  /login              login.html  (public)
  GET  /app                bart.html   (requires session, else redirects)
  GET  /<file>             static files in website/

App API (require session):
  POST /api/upload         multipart files → materials/users/<uid>/
  POST /api/run            kicks off ./bart run with BART_MATERIALS / BART_OUTPUT
                           pointed at the user's workspace, runs serialized
                           with a global lock, records the run in DB on exit 0
  GET  /api/runs           DB-backed list filtered by user
  GET  /api/events/<tok>   SSE stream of subprocess stdout
  GET  /output/            JSON list of the user's own runs
  GET  /output/<run_id>/.. serves packet HTML — gated on run ownership
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import auth
import emailer

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# Stripe config — all from env. Set via fly secrets before deploying.
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY = os.environ.get("STRIPE_PRICE_MONTHLY", "")
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "https://studywithbart.com")
# Premium ("claude") web-run authoring model. Sonnet 4.6 is ~5x cheaper than
# Opus on the heavy authoring step and keeps the $10/mo tier profitable with
# the creator commission stacked on (see the creator-accounts design spec).
WEB_PRIMARY_MODEL = os.environ.get("BART_WEB_PRIMARY_MODEL", "claude-sonnet-4-6")


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MATERIALS_ROOT = ROOT / "materials" / "users"
OUTPUT_ROOT = ROOT / "output" / "users"
CONFIG_ROOT = ROOT / ".workspaces"
RUN_SCRIPT = ROOT / "run"
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _user_claude_dir(user_id: int) -> Path:
    """Per-user CLAUDE_CONFIG_DIR — where `claude /login` writes credentials
    when we spawn it with this dir as $HOME or as CLAUDE_CONFIG_DIR."""
    p = CONFIG_ROOT / str(user_id) / ".claude"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _claude_connected(user_id: int) -> bool:
    """True when the per-user workspace has usable claude credentials.
    Claude Code 2.x writes ~/.claude.json after `claude auth login` completes.
    Older variants also wrote files into ~/.claude/, so we check both."""
    workspace = CONFIG_ROOT / str(user_id)
    if (workspace / ".claude.json").is_file():
        return True
    d = workspace / ".claude"
    if d.is_dir():
        for name in (".credentials.json", "credentials.json", "auth.json"):
            if (d / name).is_file():
                return True
    return False

MATERIALS_ROOT.mkdir(parents=True, exist_ok=True)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
CONFIG_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="bart website")
auth.init_db()


# Canonicalize: anyone hitting <app>.fly.dev gets 301'd to the studywithbart.com
# equivalent. /api/health is excluded so Fly's machine health checks don't
# follow the redirect and mark the app unhealthy.
@app.middleware("http")
async def redirect_to_canonical_host(request: Request, call_next):
    host = (request.headers.get("host") or "").lower().split(":")[0]
    if host.endswith(".fly.dev") and request.url.path != "/api/health":
        target = f"https://studywithbart.com{request.url.path}"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(url=target, status_code=301)
    return await call_next(request)


# IP-based rate limiter for the public auth endpoints. Honors
# X-Forwarded-For when uvicorn is started with --proxy-headers (set in the
# Fly deployment); on localhost it just sees 127.0.0.1.
_limiter = Limiter(key_func=get_remote_address)
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ─── streaming + run state ───────────────────────────────────────────────────

_streams: dict[str, asyncio.Queue] = {}
_procs: dict[str, asyncio.subprocess.Process] = {}
# Per-user locks — one concurrent run per user (prevents accidental double-fire
# from clobbering the user's own output dir). Per-user, NOT global, so users
# don't queue behind each other.
_user_locks: dict[int, asyncio.Lock] = {}
# Global concurrency cap — how many bart subprocesses can run at once across
# the whole server. Sized to the box; tune via BART_MAX_CONCURRENT_RUNS.
_MAX_CONCURRENT_RUNS = int(os.environ.get("BART_MAX_CONCURRENT_RUNS", "6"))
_run_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_RUNS)
# token -> {user_id, subject, focus, preset, days, materials_dir, output_dir}
_run_meta: dict[str, dict] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
ETA_RE = re.compile(r"est\.?\s*time:\s*~?\s*(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|m\b|sec(?:ond)?s?|s\b)", re.IGNORECASE)
ARTIFACTS_RE = re.compile(r"artifacts:\s*(\d+)", re.IGNORECASE)
# bart's orchestrator emits `✓ [N/M] filename.ext` after each artifact completes.
# (✗ for failed; we count both as a step done so progress doesn't stall.)
ARTIFACT_DONE_RE = re.compile(r"[✓✗]\s*\[(\d+)/(\d+)\]\s+(\S+)")
SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BOX_CHARS = set("─│╭╮╯╰┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋")

BART_VOICE = [
    ("first-run setup",        "first time — i need to install my tools. takes about a minute."),
    ("Extracting materials",   "okay, peeking at your notes now…"),
    ("Building corpus",        "stitching everything together so i can read it as one stack."),
    ("warm-up check",          "quick sanity-call to the model — making sure auth works before we go big."),
    ("planning",               "figuring out how to budget the days. pinning the trickier topics earlier."),
    ("researcher",             "sent out the researcher agents to fan through your corpus in parallel."),
    ("drafting day",           "writing dense, with worked examples and quick-checks."),
    ("critic",                 "critic agent is grading the draft — i'll revise if it falls below threshold."),
    ("schematics",             "composing schematics — diagrams, formulae, and the traps you keep falling for."),
    ("whimsical notes",        "writing the mnemonics. these are the ones that actually stick."),
    ("short study guide",      "and the 60-minute version for the morning of."),
    ("practice exam",          "now the practice exam — a real 3-hour run with a key you can self-grade."),
    ("rendering html",         "almost there — rendering the html packet."),
    ("Run failed",             "uh oh — something broke. the system line below has the details."),
    ("No files in",            "i couldn't find your files in materials/. try dropping them again and re-running."),
    ("claude /login",          "you need to log into claude code first. open a terminal and run: claude /login"),
    ("rate limit",             "rate-limited by the subscription — i'll keep retrying for you."),
    ("finished with exit code 0", "all done! your packet is ready below. open day 1 first."),
]


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _clean_line(raw: bytes) -> str | None:
    text = _strip_ansi(raw.decode("utf-8", errors="replace"))
    if "\r" in text:
        text = text.split("\r")[-1]
    text = text.rstrip()
    if not text.strip():
        return None
    stripped = text.strip()
    if all(c in SPINNER_CHARS + " " for c in stripped):
        return None
    if stripped and all(c in BOX_CHARS or c == " " for c in stripped):
        return None
    return text


def _bart_voice_for(text: str) -> str | None:
    low = text.lower()
    for needle, voice in BART_VOICE:
        if needle.lower() in low:
            return voice
    return None


# ─── per-user workspace ──────────────────────────────────────────────────────

def _user_materials(user_id: int) -> Path:
    p = MATERIALS_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_output(user_id: int) -> Path:
    p = OUTPUT_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_config_path(user_id: int) -> Path:
    p = CONFIG_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / ".bart_config.json"


def _latest_run_dir(user_id: int) -> Path | None:
    out = _user_output(user_id)
    runs = sorted(
        (d for d in out.iterdir() if d.is_dir() and d.name.startswith("run_")),
        key=lambda p: p.name,
    )
    return runs[-1] if runs else None


# How many recent runs to keep per user before GC kicks in. Tune via env.
# Generated packets are ~5–10 MB each, so 10 ≈ 50–100 MB/user — leaves
# headroom on a 3 GB volume for several hundred users.
KEEP_RUNS_PER_USER = int(os.environ.get("BART_KEEP_RUNS_PER_USER", "10"))


def _gc_user_runs(user_id: int, keep: int = KEEP_RUNS_PER_USER) -> int:
    """Delete a user's oldest runs beyond `keep`, skipping shared/favorited
    ones. Returns the number of runs removed. Safe to call any time —
    no-ops if the user is under the limit."""
    victims = auth.runs_eligible_for_gc(user_id, keep)
    if not victims:
        return 0
    out = _user_output(user_id)
    removed = 0
    for run_id in victims:
        run_dir = out / run_id
        try:
            if run_dir.is_dir():
                shutil.rmtree(run_dir)
            auth.delete_run_row(run_id, user_id)
            removed += 1
        except Exception as e:
            print(f"[gc] failed to delete {run_id} for user={user_id}: {e}",
                  file=sys.stderr, flush=True)
    if removed:
        print(f"[gc] removed {removed} old run(s) for user={user_id}",
              file=sys.stderr, flush=True)
    return removed


def _archive_materials_into_latest_run(user_id: int) -> tuple[int, str] | None:
    run = _latest_run_dir(user_id)
    if not run:
        return None
    src_root = _user_materials(user_id)
    sources = [p for p in src_root.iterdir() if p.is_file()]
    if not sources:
        return None
    dest = run / "_source"
    dest.mkdir(exist_ok=True)
    moved = 0
    for src in sources:
        try:
            shutil.move(str(src), str(dest / src.name))
            moved += 1
        except OSError:
            pass
    return (moved, run.name)


# ─── auth endpoints ──────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


def _user_payload(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


# Cookie a referral link drops so a creator gets credited even if the visitor
# signs up days later. 90-day life — long enough to bridge "saw it / bought it".
REFERRAL_COOKIE = "bart_ref"
_REFERRAL_COOKIE_MAX_AGE = 90 * 24 * 3600


def _referral_from_request(request: Request) -> Optional[str]:
    """The referral code carried by this request's cookie — but only when it
    maps to a real active creator. Anything else is dropped so we never store
    a junk code on a user row."""
    code = (request.cookies.get(REFERRAL_COOKIE) or "").strip().upper()
    if code and auth.referral_code_is_valid(code):
        return code
    return None


# Cookie that carries the acquisition source (the ?utm_source= tag on a
# landing link) from the splash page through to signup — mirrors the referral
# cookie. 90-day life so a visitor who arrives from a post and signs up days
# later is still attributed to the channel that found them.
SOURCE_COOKIE = "bart_src"
_SOURCE_COOKIE_MAX_AGE = 90 * 24 * 3600
_SOURCE_STRIP_RE = re.compile(r"[^a-z0-9_\-]")


def _clean_source(raw: str) -> str:
    """Normalise a utm_source value to a short, safe channel tag (lowercase
    alphanumerics, dash and underscore only). Bounds it so a hostile query
    string can't bloat the cookie or a user row."""
    return _SOURCE_STRIP_RE.sub("", (raw or "").strip().lower())[:64]


def _source_from_request(request: Request) -> Optional[str]:
    """The acquisition source carried by this request's cookie, if any."""
    return _clean_source(request.cookies.get(SOURCE_COOKIE) or "") or None


@app.post("/api/auth/signup")
@_limiter.limit("3/hour")
async def signup(req: SignupRequest, request: Request, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    if len(req.password) < 6:
        raise HTTPException(400, "password must be at least 6 characters.")
    if auth.find_user_by_email(req.email):
        raise HTTPException(409, "an account with that email already exists.")
    user_id = auth.create_user(
        req.email, req.password, req.name,
        referred_by=_referral_from_request(request),
        signup_source=_source_from_request(request),
    )
    sid = auth.create_session(user_id)
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    row = auth.find_user_by_id(user_id)
    return {"user": _user_payload(row)}


@app.post("/api/auth/login")
@_limiter.limit("5/minute")
async def login(req: LoginRequest, request: Request, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    row = auth.find_user_by_email(req.email)
    if not row or not auth.verify_password(req.password, row["password_hash"]):
        raise HTTPException(401, "email or password didn't match.")
    sid = auth.create_session(row["id"])
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    return {"user": _user_payload(row)}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(auth.SESSION_COOKIE)
    if sid:
        auth.delete_session(sid)
    auth.clear_session_cookie(response)
    return {"ok": True}


class GoogleAuthRequest(BaseModel):
    credential: str   # the JWT id_token from Google Identity Services


@app.post("/api/auth/google")
@_limiter.limit("10/minute")
async def auth_google(req: GoogleAuthRequest, request: Request, response: Response):
    """Sign in (or sign up) with Google. Front end uses Google Identity Services
    to produce a signed JWT (id_token); we verify it against Google's public
    keys and check that the audience matches our client_id, then issue a
    session cookie."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "google sign-in isn't configured on the server.")
    # Lazy import so the package is only required if google auth is enabled.
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    try:
        idinfo = google_id_token.verify_oauth2_token(
            req.credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        raise HTTPException(401, f"google token couldn't be verified: {e}")

    if not idinfo.get("email_verified", False):
        raise HTTPException(401, "google says this email isn't verified.")

    google_sub = idinfo.get("sub")
    email = (idinfo.get("email") or "").strip().lower()
    name  = idinfo.get("name") or idinfo.get("given_name") or ""
    if not (google_sub and email):
        raise HTTPException(401, "google token missing required fields.")

    user = auth.find_or_create_google_user(
        google_sub, email, name,
        referred_by=_referral_from_request(request),
        signup_source=_source_from_request(request),
    )
    sid = auth.create_session(user["id"])
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    return {"user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@app.get("/api/auth/google/config")
async def google_config():
    """Public endpoint — returns the GIS client_id so the login page can render
    the button without us having to template the HTML."""
    return {"client_id": GOOGLE_CLIENT_ID}


# ─── billing (Stripe) ────────────────────────────────────────────────────────

def _stripe():
    """Lazy-init: return the stripe module configured with our key, or raise 503.
    Lazy because we don't want server startup to fail if Stripe isn't set up yet."""
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "stripe isn't configured on the server.")
    import stripe as _s
    _s.api_key = STRIPE_SECRET_KEY
    return _s


def _ensure_stripe_customer(user) -> str:
    """Return the user's stripe_customer_id, creating one in Stripe if needed."""
    if user["stripe_customer_id"]:
        return user["stripe_customer_id"]
    s = _stripe()
    customer = s.Customer.create(
        email=user["email"],
        name=user["name"] or None,
        metadata={"user_id": str(user["id"])},
    )
    auth.set_stripe_customer(user["id"], customer.id)
    return customer.id


@app.get("/api/billing/status")
async def billing_status(user=Depends(auth.current_user)):
    """Lightweight status the UI reads to decide which Settings card to show.

    Self-healing: if the user has a stripe_customer_id but their status looks
    stale (free / null) — likely the webhook missed — query Stripe live and
    back-fill our DB. Keeps the UI honest without depending on retries."""
    needs_sync = (
        user["stripe_customer_id"]
        and (user["subscription_status"] in (None, "", "free"))
        and STRIPE_SECRET_KEY
    )
    if needs_sync:
        try:
            s = _stripe()
            subs = s.Subscription.list(customer=user["stripe_customer_id"], limit=1, status="all")
            if subs.data:
                sub = subs.data[0]
                auth.update_subscription(
                    user_id=user["id"],
                    subscription_id=sub.id,
                    status=sub.status,
                    current_period_end=_period_end_from_sub(sub),
                )
                # Refresh local copy so the response reflects the update.
                user = auth.find_user_by_id(user["id"])
        except Exception as e:
            print(f"[billing-status] live-sync failed for user={user['id']}: {e}",
                  file=sys.stderr, flush=True)

    return {
        "is_grandfathered": bool(user["is_grandfathered"]),
        "status": user["subscription_status"] or "free",
        "current_period_end": user["current_period_end"],
        "has_run_access": auth.has_run_access(user),
        "usage": auth.claude_run_usage(user),
    }


@app.post("/api/billing/checkout")
@_limiter.limit("10/minute")
async def billing_checkout(request: Request, user=Depends(auth.current_user)):
    """Create a Stripe Checkout session for the monthly plan; return its URL.
    The browser redirects to it; Stripe collects payment; we get the result
    from a webhook (not the redirect, since the redirect is forgeable)."""
    if not STRIPE_PRICE_MONTHLY:
        raise HTTPException(503, "subscription price isn't configured.")
    if auth.has_run_access(user):
        # Already covered (grandfathered or active) — no point billing them.
        raise HTTPException(400, "you already have access — no subscription needed.")
    s = _stripe()
    customer_id = _ensure_stripe_customer(user)
    session = s.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": STRIPE_PRICE_MONTHLY, "quantity": 1}],
        success_url=f"{APP_PUBLIC_URL}/app/settings?subscribed=1",
        cancel_url=f"{APP_PUBLIC_URL}/pricing?canceled=1",
        allow_promotion_codes=True,
        client_reference_id=str(user["id"]),
    )
    return {"url": session.url}


@app.post("/api/billing/portal")
@_limiter.limit("10/minute")
async def billing_portal(request: Request, user=Depends(auth.current_user)):
    """Create a Stripe Customer Portal session — the hosted page where users
    can update payment method, see invoices, and cancel."""
    if not user["stripe_customer_id"]:
        raise HTTPException(400, "no stripe customer on file yet — subscribe first.")
    s = _stripe()
    session = s.billing_portal.Session.create(
        customer=user["stripe_customer_id"],
        return_url=f"{APP_PUBLIC_URL}/app/settings",
    )
    return {"url": session.url}


def _period_end_from_sub(sub_obj) -> Optional[str]:
    """Stripe's current_period_end is a Unix timestamp. Store ISO for sanity.
    Avoid .get() — StripeObject overrides it in unexpected ways."""
    try:
        ts = sub_obj["current_period_end"]
    except (KeyError, AttributeError, TypeError):
        ts = getattr(sub_obj, "current_period_end", None)
    if not ts:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _credit_referral_commission(user_id: int, session_data: dict) -> None:
    """Pay a creator their share of a *verified* subscription payment.

    Called only from inside the signature-verified Stripe webhook, after
    Stripe has actually collected the money — never on a forgeable redirect.
    No-op when the subscriber wasn't referred, or the referral code is stale.
    Idempotent: keyed on the Stripe checkout-session id, so a webhook Stripe
    retries never credits a creator twice."""
    try:
        user = auth.find_user_by_id(user_id)
        if user is None:
            return
        code = auth._row_get(user, "referred_by")
        if not code:
            return
        creator = auth.get_creator_by_code(code)
        if creator is None:
            return
        amount_cents = int(session_data.get("amount_total") or 0)
        if amount_cents <= 0:
            return
        currency = session_data.get("currency") or "usd"
        # Flat $2 per verified payment, capped at the amount actually
        # collected so a discounted/zero payment never overpays.
        commission = min(auth.CREATOR_COMMISSION_CENTS, amount_cents)
        ref = str(session_data.get("id") or f"session-user-{user_id}")
        if auth.record_commission(
            creator_id=creator["id"], referred_user_id=user_id,
            amount_cents=commission, currency=currency, stripe_ref=ref,
        ):
            print(f"[creator] credited code {creator['referral_code']} "
                  f"{commission}¢ for verified payment by user {user_id}",
                  file=sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001 — commission bookkeeping is best-effort
        print(f"[creator] commission credit failed: {e}",
              file=sys.stderr, flush=True)


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe hits this on every subscription event. We verify the signature,
    then mirror the subscription state into our DB so /api/run can gate on it.

    Signature verification is critical — without it, anyone can POST here and
    grant themselves a subscription. Stripe signs with STRIPE_WEBHOOK_SECRET."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "stripe webhook secret not configured.")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    s = _stripe()
    try:
        event = s.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        print(f"[stripe-webhook] signature verification failed: {e}",
              file=sys.stderr, flush=True)
        raise HTTPException(400, "bad signature")

    etype = event["type"]
    obj   = event["data"]["object"]
    # StripeObject subclasses dict but overrides .get / .pop in ways that raise
    # AttributeError for anything not in the underlying dict. Normalise to a
    # plain dict so .get(default) works as expected.
    data  = dict(obj)
    print(f"[stripe-webhook] {etype}", file=sys.stderr, flush=True)

    if etype == "checkout.session.completed":
        # Successful subscription purchase. client_reference_id is our user.id;
        # also stamp the customer_id in case we didn't have it yet.
        user_id_str     = data.get("client_reference_id")
        customer_id     = data.get("customer")
        subscription_id = data.get("subscription")
        if user_id_str and customer_id:
            try:
                user_id = int(user_id_str)
                auth.set_stripe_customer(user_id, customer_id)
                if subscription_id:
                    sub = s.Subscription.retrieve(subscription_id)
                    auth.update_subscription(
                        user_id=user_id,
                        subscription_id=sub.id,
                        status=sub.status,
                        current_period_end=_period_end_from_sub(sub),
                    )
                # Creator commission — credited ONLY here, inside the
                # signature-verified webhook, i.e. only after Stripe has
                # actually collected the payment. If this subscriber arrived
                # through a creator's referral link, pay that creator their
                # share. Idempotent on the checkout-session id, so a retried
                # webhook never double-credits.
                _credit_referral_commission(user_id, data)
            except Exception as e:
                print(f"[stripe-webhook] checkout.session.completed handler error: {e}",
                      file=sys.stderr, flush=True)

    elif etype in ("customer.subscription.created",
                   "customer.subscription.updated",
                   "customer.subscription.deleted"):
        customer_id = data.get("customer")
        u = auth.find_user_by_stripe_customer(customer_id) if customer_id else None
        if u is not None:
            status = data.get("status")
            if etype == "customer.subscription.deleted":
                status = "canceled"
            auth.update_subscription(
                user_id=u["id"],
                subscription_id=data.get("id"),
                status=status or "free",
                current_period_end=_period_end_from_sub(obj),
            )

    return {"received": True}


@app.get("/api/auth/me")
async def me(user=Depends(auth.current_user)):
    return {
        "user": _user_payload(user),
        "usage": auth.claude_run_usage(user),
        "trial_credits": auth.trial_credits(user),
    }


@app.get("/api/usage")
async def usage(user=Depends(auth.current_user)):
    """Premium (Claude) run allowance for the current month. The run row
    polls this so the student always sees how many premium runs remain;
    Gemma runs are unlimited and never counted."""
    return auth.claude_run_usage(user)


# ─── creator program ─────────────────────────────────────────────────────────

class CreatorApplyRequest(BaseModel):
    name: str = ""
    email: str = ""
    audience: str = ""   # where / how they reach students
    links: str = ""      # channel / profile URLs
    pitch: str = ""      # why they'd be a good creator


@app.post("/api/creator/apply")
@_limiter.limit("5/hour")
async def creator_apply(req: CreatorApplyRequest, request: Request,
                        user=Depends(auth.current_user_optional)):
    """Anyone can apply to the creator program. The application is stored and
    emailed to the bart team (bartcompanyai@gmail.com) for review."""
    name = (req.name or "").strip()
    email = (req.email or "").strip().lower()
    if not email and user is not None:
        email = (user["email"] or "").strip().lower()
    if not name:
        raise HTTPException(400, "please tell us your name.")
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "we need a valid email to reach you at.")
    if len((req.pitch or "").strip()) < 10:
        raise HTTPException(400, "tell us a little about your audience and why bart.")

    # Already an approved creator? Nothing to apply for.
    if user is not None and auth.get_creator_for_user(user) is not None:
        raise HTTPException(409, "you're already a bart creator — sign in to see your link.")
    if auth.get_creator_by_email(email) is not None:
        raise HTTPException(409, "you're already a bart creator — sign in to see your link.")
    # One pending application at a time.
    prior = auth.latest_application_for_email(email)
    if prior is not None and prior["status"] == "pending":
        raise HTTPException(409, "your application is already in review — we'll be in touch.")

    app_id = auth.create_creator_application(
        name=name, email=email, audience=req.audience, links=req.links,
        pitch=req.pitch, user_id=user["id"] if user else None,
    )
    app_row = auth.get_creator_application(app_id)
    # Email the team. Delivery failures are swallowed by the emailer — the
    # application is safely in the DB and visible in the admin queue regardless.
    emailer.notify_creator_application(dict(app_row))
    return {"ok": True, "application_id": app_id}


@app.get("/api/creator/me")
async def creator_me(user=Depends(auth.current_user)):
    """The signed-in user's creator status: their dashboard (referral link,
    referrals, earnings) once approved, otherwise their application status."""
    creator = auth.get_creator_for_user(user)
    if creator is not None:
        summary = auth.creator_summary(creator)
        summary["is_creator"] = True
        summary["referral_url"] = f"{APP_PUBLIC_URL}/r/{summary['referral_code']}"
        return summary
    app = auth.latest_application_for_email(user["email"] or "")
    return {
        "is_creator": False,
        "application_status": app["status"] if app is not None else None,
    }


@app.get("/api/admin/creator-applications")
async def admin_creator_applications(status: str = "pending",
                                     user=Depends(auth.current_user)):
    """Review queue — admin only. ?status=pending|approved|rejected|all."""
    _require_admin(user)
    want = None if status == "all" else status
    return {"applications": auth.list_creator_applications(want)}


@app.post("/api/admin/creator-applications/{app_id}/approve")
async def admin_approve_creator(app_id: int, user=Depends(auth.current_user)):
    """Approve an application — mints the creator + a unique referral code,
    and emails the applicant their link."""
    _require_admin(user)
    creator = auth.approve_creator_application(app_id)
    if creator is None:
        raise HTTPException(404, "no such application.")
    code = creator["referral_code"]
    referral_url = f"{APP_PUBLIC_URL}/r/{code}"
    emailer.send_email(
        creator["email"],
        "you're in — welcome to the bart creator program",
        (
            f"Hi {creator['name'] or 'there'},\n\n"
            "Your application to the bart creator program was approved.\n\n"
            f"Your referral link:\n  {referral_url}\n\n"
            "Share it anywhere. When someone subscribes through it, you earn "
            f"${auth.CREATOR_COMMISSION_CENTS / 100:.2f} every month they stay "
            "subscribed — credited automatically once each payment clears.\n\n"
            "Sign in and open the creator page to see your referrals and "
            "earnings any time.\n\n— the bart team\n"
        ),
    )
    return {"ok": True, "referral_code": code, "referral_url": referral_url}


@app.post("/api/admin/creator-applications/{app_id}/reject")
async def admin_reject_creator(app_id: int, user=Depends(auth.current_user)):
    _require_admin(user)
    if not auth.reject_creator_application(app_id):
        raise HTTPException(404, "no such pending application.")
    return {"ok": True}


# ─── trial codes (single-use free-packet coupons) ────────────────────────────

class RedeemCodeRequest(BaseModel):
    code: str = ""


@app.post("/api/codes/redeem")
@_limiter.limit("10/minute")
async def redeem_code(req: RedeemCodeRequest, request: Request,
                      user=Depends(auth.current_user)):
    """Redeem a single-use trial code. Grants the signed-in account one free
    premium (Claude) packet generation. A code works exactly once, ever —
    no matter which account redeems it."""
    try:
        result = auth.redeem_trial_code(req.code, user["id"])
    except auth.TrialCodeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "trial_credits": result["trial_credits"]}


class MintCodesRequest(BaseModel):
    note: str = ""
    count: int = 1


@app.post("/api/admin/trial-codes")
async def admin_mint_trial_codes(req: MintCodesRequest,
                                 user=Depends(auth.current_user)):
    """Mint one or more single-use trial codes — admin only. Minting here is
    the ONLY way a trial code ever comes into existence."""
    _require_admin(user)
    n = max(1, min(50, int(req.count or 1)))
    codes = [auth.create_trial_code(req.note) for _ in range(n)]
    return {"ok": True, "codes": codes}


@app.get("/api/admin/trial-codes")
async def admin_list_trial_codes(user=Depends(auth.current_user)):
    """Every minted trial code and its redemption state — admin only."""
    _require_admin(user)
    return {"codes": auth.list_trial_codes()}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
@_limiter.limit("5/minute")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user=Depends(auth.current_user),
):
    if len(req.new_password) < 6:
        raise HTTPException(400, "new password must be at least 6 characters.")
    if req.new_password == req.current_password:
        raise HTTPException(400, "new password is the same as the current one.")
    if not auth.verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(401, "current password didn't match.")
    auth.update_password(user["id"], req.new_password)
    # Keep this device logged in; kick every other session.
    current_sid = request.cookies.get(auth.SESSION_COOKIE)
    auth.delete_sessions_for_user(user["id"], keep_sid=current_sid)
    return {"ok": True}


# ─── health (public — useful for the splash to gauge config) ────────────────

@app.get("/api/health")
async def health():
    claude = shutil.which("claude")
    return {
        "ok": True,
        "claude_cli": claude,
        "claude_present": claude is not None,
    }


# ─── materials (auth) ────────────────────────────────────────────────────────

@app.get("/api/materials")
async def materials(user=Depends(auth.current_user)):
    d = _user_materials(user["id"])
    return {
        "files": [
            {"name": p.name, "size": p.stat().st_size}
            for p in d.iterdir() if p.is_file()
        ]
    }


MAX_UPLOAD_BYTES = 25 * 1024 * 1024     # 25 MB per file
MAX_USER_STORAGE = 250 * 1024 * 1024    # 250 MB per account
_UPLOAD_CHUNK = 64 * 1024


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), user=Depends(auth.current_user)):
    dest = _user_materials(user["id"])
    used = sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
    saved = []
    for f in files:
        if not f.filename:
            continue
        safe = Path(f.filename).name
        target = dest / safe
        size = 0
        try:
            with target.open("wb") as out:
                while True:
                    chunk = await f.read(_UPLOAD_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            413, f"`{safe}` is too large — 25 MB max per file."
                        )
                    if used + size > MAX_USER_STORAGE:
                        raise HTTPException(
                            413, "you've hit the 250 MB storage limit. "
                                 "delete some files first.",
                        )
                    out.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        used += size
        saved.append({"name": safe, "size": size})
    return {"saved": saved}


# ─── run (auth + global lock) ────────────────────────────────────────────────

class RunRequest(BaseModel):
    subject: str = ""
    days: int = 7
    focus: str = ""
    preset: str = "default"   # default | fast | turbo
    # Which engine bart should run on:
    #   "claude" — Claude via the user's claude.ai subscription (default)
    #   "gemma"  — local Gemma 4 open weights (free, no API key; bart
    #              detects the host's hardware and auto-picks + downloads
    #              the best-fitting Gemma 4 variant on first run)
    model: str = "claude"


@app.post("/api/run")
async def start_run(req: RunRequest, user=Depends(auth.current_user)):
    # Per-user single-run gate — prevents accidental double-fire from this same
    # user clobbering their own output dir. Other users are unaffected.
    if _user_lock(user["id"]).locked():
        raise HTTPException(
            409, "you already have a run in progress — wait for it to finish."
        )

    # Gemma 4 runs entirely on local open weights — no `claude` CLI and no
    # connected claude.ai account required. The Claude path keeps both
    # preconditions.
    use_gemma = (req.model or "claude").strip().lower() in (
        "gemma", "gemma4", "gemma-4", "local",
    )

    # Paywall — bypass if grandfathered, else require an active subscription.
    # A single-use trial code grants one free premium (Claude) packet, so an
    # unsubscribed account holding a trial credit may run once on Claude. The
    # credit is spent only after the run actually launches (see below).
    # 402 Payment Required is the canonical status; the UI listens for it and
    # routes to the subscribe modal / pricing page.
    use_trial = False
    if not auth.has_run_access(user):
        if not use_gemma and auth.trial_credits(user) > 0:
            use_trial = True
        else:
            raise HTTPException(
                402, "subscribe to run bart — $10/month, cancel anytime."
            )

    materials_dir = _user_materials(user["id"])
    sources = [p for p in materials_dir.iterdir() if p.is_file()]
    if not sources:
        raise HTTPException(
            400, "drop some files first, then click let bart cook."
        )

    if not use_gemma:
        # Monthly Claude-run allowance. Local Gemma runs are unlimited, so a
        # subscriber who's used their premium runs can always switch the
        # model toggle to "gemma 4" and keep generating full packets — the
        # quality of any single packet is never reduced. A trial-credit run
        # has its own one-shot allowance, so the monthly meter doesn't apply.
        if not use_trial:
            usage = auth.claude_run_usage(user)
            if not usage["unlimited"] and usage["remaining"] <= 0:
                raise HTTPException(
                    429,
                    f"you've used all {usage['limit']} premium (claude) runs this "
                    f"month. switch the model to gemma 4 for unlimited free runs, "
                    f"or your allowance resets on the 1st.",
                )

        if shutil.which("claude") is None:
            raise HTTPException(
                503,
                "the `claude` CLI isn't on PATH. install Claude Code from "
                "https://claude.ai/code, run `claude /login`, then try again.",
            )

        if not _claude_connected(user["id"]):
            raise HTTPException(
                401,
                "your claude account isn't connected — go to settings → connect.",
            )

    days = max(1, min(60, req.days))
    from datetime import date, timedelta
    exam = (date.today() + timedelta(days=days)).isoformat()

    config = {
        "auth_mode": "local" if use_gemma else "claude-code",
        "api_key": "",
        "exam_date": exam,
        "subject": (req.subject or "your course").strip()[:200],
        "guidance": (req.focus or "").strip(),
        "student_level": "undergraduate",
        "style": "academic-rigorous",
        "daily_hours": 3.0,
        "primary_model": WEB_PRIMARY_MODEL,
        "daily_model": "claude-sonnet-4-6",
        "fast_model": "claude-haiku-4-5-20251001",
        "deep_research": False,
        # auth_mode=="local": empty model_key → bart detects the host's
        # hardware and auto-picks the best-fitting Gemma 4 variant.
        "local_model_key": "",
        "local_model_family": "gemma4" if use_gemma else "auto",
        # Custom illustrations on by default for web runs — generated via the
        # free, open Pollinations backend and embedded in the packet. Degrades
        # gracefully to a placeholder if the network is unavailable.
        "image_generation": True,
    }
    config_path = _user_config_path(user["id"])
    config_path.write_text(json.dumps(config, indent=2))

    output_dir = _user_output(user["id"])

    # The local Gemma 4 inference server is single-slot — fan-out would just
    # queue behind one slot, so request 1. Claude keeps the ×4 fan-out.
    max_parallel = "1" if use_gemma else "4"
    if VENV_PY.exists():
        cmd = [str(VENV_PY), "-m", "bart", "run",
               "--max-parallel", max_parallel, "--days", str(days)]
    else:
        cmd = [str(RUN_SCRIPT), "run",
               "--max-parallel", max_parallel, "--days", str(days)]
    if req.preset == "fast":
        cmd.append("--fast")
    elif req.preset == "turbo":
        cmd.append("--turbo")

    token = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()
    _streams[token] = queue
    _run_meta[token] = {
        "user_id": user["id"],
        "subject": config["subject"],
        "focus": config["guidance"],
        "preset": req.preset,
        "model": "gemma" if use_gemma else "claude",
        "days": days,
        "source_count": len(sources),
        "materials_dir": str(materials_dir),
        "output_dir": str(output_dir),
    }

    env = dict(os.environ)
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    # Per-user workspace — bart/paths.py picks these up at import time.
    env["BART_MATERIALS"] = str(materials_dir)
    env["BART_OUTPUT"] = str(output_dir)
    # Per-user bart config — bart/config.py reads this instead of the legacy
    # shared ROOT/.bart_config.json. This is what removes the global lock.
    env["BART_CONFIG"] = str(config_path)
    if use_gemma:
        # Gemma 4 weights are large (up to ~16 GB). The HOME override below
        # would otherwise sandbox the model cache per-user and force every
        # user to re-download. Point all local runs at one shared cache so
        # the weights are fetched exactly once for the whole deployment.
        shared_cache = ROOT / ".model-cache"
        shared_cache.mkdir(parents=True, exist_ok=True)
        env["BART_CACHE_DIR"] = str(shared_cache)
    # Per-user claude credentials. HOME override means the spawned `claude`
    # CLI reads <workspace>/.claude/, not the host's ~/.claude/, so each user
    # runs against their own claude.ai session.
    env["HOME"] = str(CONFIG_ROOT / str(user["id"]))
    # cwd is per-user so any incidental write goes into that user's workspace,
    # not ROOT. PYTHONPATH=ROOT keeps `python -m bart` resolvable from cwd.
    cwd = CONFIG_ROOT / str(user["id"])
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(ROOT)

    user_lock = _user_lock(user["id"])
    # Race against /api/run firing twice for the same user before the first
    # call's lock acquire — re-check here under the lock.
    if user_lock.locked():
        raise HTTPException(
            409, "you already have a run in progress — wait for it to finish."
        )
    await user_lock.acquire()
    # Block on the global semaphore — fast path when below the cap, queues
    # politely when at the cap. Released in the reader's finally.
    await _run_semaphore.acquire()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        _run_semaphore.release()
        user_lock.release()
        raise HTTPException(500, f"could not launch bart: {e}")
    _procs[token] = proc
    _run_meta[token]["user_lock"] = user_lock

    # The run launched successfully — charge it. A trial-credit run spends the
    # free credit instead of the monthly meter. Otherwise count it against the
    # monthly premium allowance: Gemma runs are free and unlimited so they're
    # never counted, and grandfathered accounts skip the counter (the call is
    # harmless bookkeeping since their quota is never checked).
    if use_trial:
        try:
            auth.consume_trial_credit(user["id"])
        except Exception:  # noqa: BLE001 — never fail a launched run on bookkeeping
            pass
    elif not use_gemma and not auth._row_get(user, "is_grandfathered", 0):
        try:
            auth.consume_claude_run(user["id"])
        except Exception:  # noqa: BLE001 — never fail a launched run on bookkeeping
            pass

    async def reader():
        try:
            assert proc.stdout is not None
            seen_bart_voices: set[str] = set()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = _clean_line(line)
                if text is None:
                    continue
                voice = _bart_voice_for(text)
                if voice and voice not in seen_bart_voices:
                    seen_bart_voices.add(voice)
                    await queue.put({"kind": "bart", "line": voice})
                m_eta = ETA_RE.search(text)
                if m_eta:
                    n = float(m_eta.group(1))
                    unit = m_eta.group(2).lower()
                    secs = int(n * 60) if unit.startswith("m") else int(n)
                    await queue.put({"kind": "eta", "seconds": secs})
                m_art = ARTIFACTS_RE.search(text)
                if m_art:
                    await queue.put({"kind": "artifacts", "total": int(m_art.group(1))})
                m_done = ARTIFACT_DONE_RE.search(text)
                if m_done:
                    await queue.put({
                        "kind": "artifact_done",
                        "done": int(m_done.group(1)),
                        "total": int(m_done.group(2)),
                        "file": m_done.group(3),
                    })
                await queue.put({"kind": "sys", "line": text})
            rc = await proc.wait()
            meta = _run_meta.get(token, {})
            user_id = meta.get("user_id")

            # Record the run if a run dir exists, regardless of exit code.
            # Bart may exit non-zero on partial failures (one artifact failed,
            # rate limit on the last step, etc.) but the artifacts on disk are
            # still useful — without a DB row they're invisible to /output/.
            run_id = None
            if user_id is not None:
                if rc == 0:
                    archived = _archive_materials_into_latest_run(user_id)
                    if archived:
                        run_id = archived[1]
                if run_id is None:
                    latest = _latest_run_dir(user_id)
                    run_id = latest.name if latest else None
                if run_id:
                    try:
                        auth.record_run(
                            run_id=run_id,
                            user_id=user_id,
                            subject=meta.get("subject", ""),
                            focus=meta.get("focus", ""),
                            preset=meta.get("preset", "default"),
                            days=meta.get("days", 7),
                            source_count=meta.get("source_count", 0),
                        )
                        print(f"[run-recorder] recorded {run_id} (user={user_id}, rc={rc})",
                              file=sys.stderr, flush=True)
                    except Exception as e:
                        print(f"[run-recorder] FAILED to record {run_id}: {e}",
                              file=sys.stderr, flush=True)
                # GC after each new run lands. Skipped on the exception path
                # (record_run failed), since we want a stable DB before pruning.
                try:
                    _gc_user_runs(user_id)
                except Exception as e:
                    print(f"[gc] error during post-run gc for user={user_id}: {e}",
                          file=sys.stderr, flush=True)

            if rc == 0:
                await queue.put({"kind": "bart",
                                 "line": "all done! your packet is ready below — click any day to open it."})
            else:
                await queue.put({"kind": "sys",
                                 "line": f"[bart exited with code {rc}] — partial output kept; check past runs"})
        except Exception as e:
            await queue.put({"kind": "sys", "line": f"[server error reading stdout: {e}]"})
        finally:
            await queue.put(None)
            meta = _run_meta.pop(token, None)
            ul = meta.get("user_lock") if meta else None
            if ul is not None and ul.locked():
                ul.release()
            _run_semaphore.release()

    asyncio.create_task(reader())
    return {"token": token, "cmd": cmd}


@app.get("/api/events/{token}")
async def events(token: str, user=Depends(auth.current_user)):
    queue = _streams.get(token)
    if queue is None:
        raise HTTPException(404, "unknown or expired run token")
    meta = _run_meta.get(token)
    if meta and meta["user_id"] != user["id"]:
        raise HTTPException(403, "not your run")

    async def stream():
        try:
            while True:
                try:
                    # 25s timeout — under the typical 30-60s proxy idle cap.
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # SSE comment, ignored by EventSource
                    continue
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _streams.pop(token, None)
            proc = _procs.pop(token, None)
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── runs list + packet serving (auth, ownership-gated) ──────────────────────

def _sync_orphan_runs(user_id: int) -> int:
    """Find run_* dirs on disk that aren't in the DB and back-fill them.
    Returns the number of runs added. Defensive against the recording path
    dropping a run (subprocess crash, server restart mid-run, etc.) — by the
    time the user opens past runs, every dir on disk should be visible.
    """
    out = _user_output(user_id)
    db_run_ids = {r["run_id"] for r in auth.list_runs(user_id)}
    added = 0
    for child in out.iterdir():
        if not (child.is_dir() and child.name.startswith("run_")):
            continue
        if child.name in db_run_ids:
            continue
        # Try to recover subject from bart's per-run config snapshot if present.
        cfg_path = child / ".bart_config.json"
        subject = ""
        if cfg_path.is_file():
            try:
                subject = json.loads(cfg_path.read_text()).get("subject", "") or ""
            except (OSError, ValueError):
                pass
        src_dir = child / "_source"
        source_count = sum(1 for _ in src_dir.iterdir()) if src_dir.is_dir() else 0
        try:
            auth.record_run(
                run_id=child.name,
                user_id=user_id,
                subject=subject,
                focus="",
                preset="default",
                days=7,
                source_count=source_count,
            )
            added += 1
        except Exception as e:
            print(f"[orphan-sync] failed to back-fill {child.name}: {e}",
                  file=sys.stderr, flush=True)
    if added:
        print(f"[orphan-sync] back-filled {added} run(s) for user={user_id}",
              file=sys.stderr, flush=True)
    return added


@app.get("/api/runs")
async def list_user_runs(user=Depends(auth.current_user)):
    _sync_orphan_runs(user["id"])
    rows = auth.list_runs(user["id"])
    out = _user_output(user["id"])
    enriched = []
    for r in rows:
        run_dir = out / r["run_id"]
        # mtime + sources kept for compatibility with the existing PastRuns UI
        try:
            mtime = run_dir.stat().st_mtime
        except OSError:
            mtime = None
        src_dir = run_dir / "_source"
        sources = (
            [p.name for p in src_dir.iterdir() if p.is_file()]
            if src_dir.is_dir() else []
        )
        enriched.append({**r, "mtime": mtime, "sources": sources})
    return {"runs": enriched}


@app.get("/output/")
async def list_output(user=Depends(auth.current_user)):
    entries = []
    out = _user_output(user["id"])
    for child in sorted(out.iterdir()):
        if child.is_dir() and child.name.startswith("run_"):
            entries.append({
                "type": "directory",
                "name": child.name,
                "relative": f"/{child.name}/",
            })
    return JSONResponse({"files": entries})


@app.post("/api/runs/{run_id}/share")
async def share_run(run_id: str, user=Depends(auth.current_user)):
    token = auth.create_share(run_id, user["id"])
    if not token:
        raise HTTPException(404, "run not found")
    return {"token": token, "url": f"/share/{token}"}


@app.delete("/api/runs/{run_id}/share")
async def unshare_run(run_id: str, user=Depends(auth.current_user)):
    if not auth.revoke_share(run_id, user["id"]):
        raise HTTPException(404, "run not found")
    return {"ok": True}


@app.get("/share/{token}")
async def share_root(token: str):
    row = auth.run_by_share_token(token)
    if not row:
        raise HTTPException(404, "this share link is invalid or was revoked.")
    return RedirectResponse(url=f"/share/{token}/index.html", status_code=302)


@app.get("/share/{token}/{path:path}")
async def serve_shared(token: str, path: str):
    row = auth.run_by_share_token(token)
    if not row:
        raise HTTPException(404, "this share link is invalid or was revoked.")
    base = _user_output(row["user_id"]) / row["run_id"]
    target = (base / path).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "bad path")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.get("/output/{run_id}/download.zip")
async def download_packet(run_id: str, user=Depends(auth.current_user)):
    owner = auth.run_owner(run_id)
    if owner is None or owner != user["id"]:
        raise HTTPException(404, "run not found")
    base = _user_output(owner) / run_id
    if not base.is_dir():
        raise HTTPException(404, "run dir missing on disk")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in base.rglob("*"):
            if path.is_file():
                z.write(path, arcname=str(path.relative_to(base)))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'},
    )


@app.get("/output/{run_id}/{path:path}")
async def serve_packet(run_id: str, path: str, user=Depends(auth.current_user)):
    owner = auth.run_owner(run_id)
    if owner is None or owner != user["id"]:
        raise HTTPException(404, "run not found")
    base = _user_output(owner) / run_id
    target = (base / path).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "bad path")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.get("/output/{run_id}/")
async def serve_packet_index(run_id: str, user=Depends(auth.current_user)):
    return await serve_packet(run_id=run_id, path="index.html", user=user)


# ─── claude account integration ─────────────────────────────────────────────
#
# `claude /login` is a device-flow OAuth. We spawn it with HOME pointed at the
# per-user workspace dir so credentials land in <workspace>/.claude/ and stay
# isolated from other users. The CLI prints a URL like
#   `https://claude.ai/oauth/...code=ABCD-EFGH`
# (exact format varies by CLI version), then blocks until the user completes
# the flow in their browser. We capture the URL from stdout, surface it via
# SSE, and the run terminates when the user authorizes (or times out).

_CLAUDE_LOGIN_URL_RE = re.compile(
    r"(https?://(?:claude\.com|claude\.ai|platform\.claude\.com|console\.anthropic\.com|anthropic\.com|auth\.anthropic\.com)\S+)",
    re.IGNORECASE,
)
_claude_proc: dict[int, asyncio.subprocess.Process] = {}     # user_id -> proc
_claude_queue: dict[int, asyncio.Queue] = {}                  # user_id -> events


@app.get("/api/auth/anthropic")
async def anthropic_status(user=Depends(auth.current_user)):
    return {
        "connected": _claude_connected(user["id"]),
        "mode": "claude-code",
        "login_in_progress": user["id"] in _claude_proc,
    }


@app.post("/api/auth/anthropic/connect")
async def anthropic_connect(user=Depends(auth.current_user)):
    """Kick off `claude /login` for the current user. Returns a token the
    client uses to subscribe to /api/auth/anthropic/events for the device
    URL + completion."""
    if user["id"] in _claude_proc:
        raise HTTPException(409, "a connect flow is already in progress.")
    if shutil.which("claude") is None:
        raise HTTPException(
            503,
            "the `claude` CLI isn't installed on this server. "
            "see https://claude.ai/code to install it.",
        )

    user_id = user["id"]
    # Use HOME override so claude writes to <workspace>/.claude/ — isolated
    # from any other user (and from the host's own ~/.claude).
    workspace = CONFIG_ROOT / str(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(workspace)
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)

    queue: asyncio.Queue = asyncio.Queue()
    _claude_queue[user_id] = queue

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "auth", "login", "--claudeai",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        _claude_queue.pop(user_id, None)
        raise HTTPException(500, f"couldn't launch claude auth login: {e}")
    _claude_proc[user_id] = proc

    async def reader():
        try:
            assert proc.stdout is not None
            url_seen = False
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = ANSI_RE.sub("", line.decode("utf-8", errors="replace")).rstrip()
                if not text.strip():
                    continue
                await queue.put({"kind": "log", "line": text})
                if not url_seen:
                    m = _CLAUDE_LOGIN_URL_RE.search(text)
                    if m:
                        url_seen = True
                        await queue.put({"kind": "url", "url": m.group(1)})
            rc = await proc.wait()
            if rc == 0 and _claude_connected(user_id):
                await queue.put({"kind": "ok"})
            else:
                await queue.put({"kind": "failed", "code": rc})
        except Exception as e:
            await queue.put({"kind": "failed", "error": str(e)})
        finally:
            await queue.put(None)
            _claude_proc.pop(user_id, None)

    asyncio.create_task(reader())
    return {"ok": True}


@app.get("/api/auth/anthropic/events")
async def anthropic_events(user=Depends(auth.current_user)):
    queue = _claude_queue.get(user["id"])
    if queue is None:
        raise HTTPException(404, "no connect flow in progress.")

    async def stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _claude_queue.pop(user["id"], None)

    return StreamingResponse(stream(), media_type="text/event-stream")


class ClaudeCodeBody(BaseModel):
    code: str


@app.post("/api/auth/anthropic/code")
async def anthropic_paste_code(body: ClaudeCodeBody, user=Depends(auth.current_user)):
    """Forward the OAuth code the user pasted from claude.com back into the
    waiting `claude auth login` subprocess via stdin."""
    proc = _claude_proc.get(user["id"])
    if proc is None or proc.returncode is not None:
        raise HTTPException(409, "no active connect flow — click connect first.")
    if proc.stdin is None:
        raise HTTPException(500, "subprocess stdin is closed.")
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, "paste the code you got after signing in on claude.com.")
    try:
        proc.stdin.write((code + "\n").encode("utf-8"))
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        raise HTTPException(500, "subprocess stopped accepting input.")
    return {"ok": True}


@app.delete("/api/auth/anthropic")
async def anthropic_disconnect(user=Depends(auth.current_user)):
    # kill any in-progress flow first
    proc = _claude_proc.pop(user["id"], None)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    # wipe per-user credentials (file at workspace root + dir)
    workspace = CONFIG_ROOT / str(user["id"])
    creds_file = workspace / ".claude.json"
    if creds_file.is_file():
        try:
            creds_file.unlink()
        except OSError:
            pass
    d = workspace / ".claude"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


# ─── friends ─────────────────────────────────────────────────────────────────

class FriendRequestBody(BaseModel):
    email: str


@app.get("/api/friends")
async def get_friends(user=Depends(auth.current_user)):
    return auth.list_friends(user["id"])


@app.post("/api/friends/request")
async def post_friend_request(req: FriendRequestBody, user=Depends(auth.current_user)):
    try:
        row = auth.send_friend_request(user["id"], req.email)
    except auth.FriendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "friendship": row}


@app.post("/api/friends/{friendship_id}/accept")
async def accept_friend(friendship_id: int, user=Depends(auth.current_user)):
    if not auth.accept_friend_request(friendship_id, user["id"]):
        raise HTTPException(404, "request not found or not yours to accept.")
    return {"ok": True}


@app.post("/api/friends/{friendship_id}/decline")
async def decline_friend(friendship_id: int, user=Depends(auth.current_user)):
    if not auth.decline_friend_request(friendship_id, user["id"]):
        raise HTTPException(404, "request not found or not yours to decline.")
    return {"ok": True}


@app.delete("/api/friends/{other_user_id}")
async def remove_friend(other_user_id: int, user=Depends(auth.current_user)):
    if not auth.unfriend(user["id"], other_user_id):
        raise HTTPException(404, "you aren't friends with that user.")
    return {"ok": True}


# ─── favorites ───────────────────────────────────────────────────────────────

class SaveFavoriteBody(BaseModel):
    share_token: Optional[str] = None
    share_url: Optional[str] = None


_SHARE_URL_RE = re.compile(r"/share/([A-Za-z0-9_\-]+)")


@app.get("/api/favorites")
async def get_favorites(user=Depends(auth.current_user)):
    return {"favorites": auth.list_favorites(user["id"])}


@app.post("/api/favorites")
async def save_favorite(body: SaveFavoriteBody, user=Depends(auth.current_user)):
    token = (body.share_token or "").strip()
    if not token and body.share_url:
        m = _SHARE_URL_RE.search(body.share_url)
        token = m.group(1) if m else ""
    if not token:
        raise HTTPException(400, "paste a /share/<token> link or token.")
    try:
        row = auth.save_favorite(user["id"], token)
    except auth.FriendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "favorite": row}


@app.delete("/api/favorites/{run_id}")
async def remove_favorite(run_id: str, user=Depends(auth.current_user)):
    if not auth.remove_favorite(user["id"], run_id):
        raise HTTPException(404, "not in your favorites.")
    return {"ok": True}


# ─── leaderboard ─────────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
async def leaderboard(user=Depends(auth.current_user)):
    return {"rows": auth.leaderboard_for(user["id"])}


# ─── admin ───────────────────────────────────────────────────────────────────

# Only the owner sees admin endpoints. Grandfathered != admin (early users got
# free access, but they're not operators).
ADMIN_EMAILS = {"loctran0323@gmail.com"}


def _require_admin(user) -> None:
    if (user["email"] or "").lower() not in ADMIN_EMAILS:
        raise HTTPException(404, "not found")  # 404 not 403 — don't advertise existence


def _read_meminfo() -> dict:
    """Parse /proc/meminfo on Linux (Fly). Returns MB. Empty dict off-Linux."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                kb = int(rest.strip().split()[0])
                info[key] = kb // 1024  # MB
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        return {
            "total_mb": total,
            "available_mb": available,
            "used_mb": total - available if total else 0,
            "used_pct": round((total - available) / total * 100, 1) if total else 0,
        }
    except (OSError, ValueError, IndexError):
        return {}


def _wal_size_bytes() -> int:
    wal = Path(str(auth.DB_PATH) + "-wal")
    try:
        return wal.stat().st_size
    except OSError:
        return 0


@app.get("/api/admin/stats")
async def admin_stats(user=Depends(auth.current_user)):
    _require_admin(user)
    # Semaphore internals — `_value` is available permits, `_waiters` is the
    # queue of callers blocked on .acquire(). Both are private but stable
    # across CPython 3.8+ and asyncio has no public equivalent yet.
    sem_available = _run_semaphore._value
    sem_waiting = len(_run_semaphore._waiters) if _run_semaphore._waiters else 0
    active_runs = _MAX_CONCURRENT_RUNS - sem_available
    # Disk on /data (the Fly volume). Off-Fly we fall back to the website dir.
    disk_target = Path("/data") if Path("/data").is_dir() else HERE
    du = shutil.disk_usage(disk_target)
    # Top runs-per-user — load-bearing for GC tuning.
    try:
        top_users = auth.runs_per_user_top(20)
    except Exception as e:
        top_users = [{"error": str(e)}]
    # Where users are coming from — the acquisition-channel breakdown.
    try:
        by_source = auth.signups_by_source(30)
    except Exception as e:
        by_source = [{"error": str(e)}]
    return {
        "concurrency": {
            "max": _MAX_CONCURRENT_RUNS,
            "active": active_runs,
            "queued": sem_waiting,
            "per_user_locks_held": sum(1 for v in _user_locks.values() if v.locked()),
        },
        "memory": _read_meminfo(),
        "disk": {
            "path": str(disk_target),
            "total_gb": round(du.total / 1e9, 2),
            "used_gb": round(du.used / 1e9, 2),
            "free_gb": round(du.free / 1e9, 2),
            "used_pct": round(du.used / du.total * 100, 1),
        },
        "db": {
            "size_mb": round(Path(auth.DB_PATH).stat().st_size / 1e6, 2) if Path(auth.DB_PATH).exists() else 0,
            "wal_mb": round(_wal_size_bytes() / 1e6, 2),
        },
        "gc": {
            "keep_per_user": KEEP_RUNS_PER_USER,
        },
        "top_users_by_runs": top_users,
        "signups_by_source": by_source,
    }


# ─── page routing ────────────────────────────────────────────────────────────

@app.get("/")
async def root_index(request: Request):
    resp = FileResponse(HERE / "splash.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    # First-touch acquisition tracking: if the visitor arrived through a
    # tagged link (studywithbart.com/?utm_source=hn), remember that channel
    # so it can be stamped on their user row at signup. First tag wins — a
    # later untagged or differently-tagged visit doesn't overwrite it.
    src = _clean_source(request.query_params.get("utm_source", ""))
    if src and not request.cookies.get(SOURCE_COOKIE):
        resp.set_cookie(
            SOURCE_COOKIE, src,
            max_age=_SOURCE_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
    return resp


@app.get("/r/{code}")
async def referral_link(code: str):
    """A creator's referral link. Drops a 90-day cookie attributing the visit
    to that creator, then sends the visitor to the splash page. The cookie is
    read at signup; the creator is only ever paid after a verified payment."""
    safe = (code or "").strip().upper()[:32]
    resp = RedirectResponse(url="/", status_code=302)
    if auth.referral_code_is_valid(safe):
        resp.set_cookie(
            REFERRAL_COOKIE, safe,
            max_age=_REFERRAL_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
    return resp


@app.get("/login")
async def login_page(request: Request, user=Depends(auth.current_user_optional)):
    # Already signed in? Skip the form. Honor ?next=... if it's a same-site path
    # (must start with / and not //) so /login?next=/pricing routes correctly.
    if user is not None:
        nxt = request.query_params.get("next") or "/app"
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/app"
        return RedirectResponse(url=nxt, status_code=302)
    return FileResponse(HERE / "login.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


_APP_SUBVIEWS = {"", "runs", "friends", "settings"}


@app.get("/app")
@app.get("/app/")
@app.get("/app/{sub}")
async def app_index(sub: str = "", user=Depends(auth.current_user_optional)):
    if sub and sub not in _APP_SUBVIEWS:
        raise HTTPException(404)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(HERE / "bart.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/{filename:path}")
async def static_files(filename: str):
    # Hard-block lookups that escape the website dir or hit auth/db assets.
    if filename in {"bart.db", "auth.py", "server.py"} or filename.startswith(".."):
        raise HTTPException(403)
    target = (HERE / filename).resolve()
    try:
        target.relative_to(HERE)
    except ValueError:
        raise HTTPException(403)
    if target.is_file():
        return FileResponse(target, headers=_NO_CACHE)
    if target.with_suffix(".html").is_file():
        return FileResponse(target.with_suffix(".html"), headers=_NO_CACHE)
    raise HTTPException(404)
