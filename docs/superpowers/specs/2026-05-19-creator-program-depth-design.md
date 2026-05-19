# Creator Program — Depth — Design Spec

**Date:** 2026-05-19
**Status:** Approved, ready for implementation planning
**Builds on:** `2026-05-18-creator-accounts-commission-dashboard-design.md`

## Goal

Deepen the bart creator program along four axes: a **tiered commission** that
rewards volume, **automated monthly payouts** (no admin button), **referral
analytics** (a clicks→signups→subscribers funnel), and **creator notification
emails**. Each is additive — none changes the core $2 flat commission for a
creator below the first tier breakpoint.

## Background — current state (after the prior spec shipped)

- A creator earns a **flat $2.00** (`auth.CREATOR_COMMISSION_CENTS`) per
  verified subscription payment, credited from the Stripe webhook on the
  initial payment and every renewal (`invoice.payment_succeeded`).
- Payouts: a `payouts` table, `auth.run_payouts(minimum_cents, period=None,
  transfer_fn=None)` (two-phase, `BEGIN IMMEDIATE`), `mark_payout_paid`,
  `list_payouts`. The monthly batch is run **manually** by an admin via
  `POST /api/admin/payouts/run`.
- `auth.creator_earnings(creator)` powers the dashboard; `creator_analytics`
  does not exist yet.
- `/r/{code}` drops a 90-day referral cookie; it does **not** count clicks.
- `emailer.py` has `send_email`, `smtp_configured`, `notify_creator_application`
  — all best-effort, no-op when SMTP is unconfigured.
- The FastAPI app has **no startup/lifespan hook**.

## Design

### 1. Tiered commission

The flat rate becomes the first rung of a tier ladder keyed on the creator's
**current active-subscriber count**.

- `auth.py` adds:
  ```python
  # (min_active_subscribers, cents) — ascending. The creator's current active-
  # subscriber count selects the tier; the rate is retroactive — crossing a
  # breakpoint lifts the rate on every one of that creator's subscribers.
  CREATOR_TIERS = [(0, 200), (50, 225)]
  ```
- `commission_cents_for(active_subscribers: int) -> int` — returns the cents of
  the highest tier whose threshold is `<= active_subscribers`.
- `creator_commission_cents(creator) -> int` — counts the creator's active
  subscribers (`users.subscription_status = 'active'` for `referred_by = code`)
  and returns `commission_cents_for(count)`.
- `CREATOR_COMMISSION_CENTS` is **kept** as the tier-1 base (200) — still read
  by tests and as the default; `CREATOR_TIERS[0]` must equal `(0,
  CREATOR_COMMISSION_CENTS)`.
- `_credit_referral_commission` (server.py) computes the commission as
  `min(auth.creator_commission_cents(creator), amount_cents)` instead of the
  flat constant.
- `creator_earnings.monthly_run_rate_cents` becomes `active_subscribers *
  commission_cents_for(active_subscribers)`.
- `creator_earnings` gains two keys for the dashboard: `commission_cents`
  (the creator's current tiered rate) and `next_tier` (a dict `{at:
  <subs>, cents: <rate>}` for the next breakpoint, or `None` if already at the
  top tier).

**Edge case (documented, accepted):** when a brand-new subscriber's first
payment is credited via `invoice.payment_succeeded`, that subscriber's
`subscription_status` may not yet be persisted as `active`, so the tier count
can be off by one exactly at a breakpoint. It self-corrects on the next
payment. Not worth ordering-proofing.

**Unit economics:** top tier ($2.25) nets ~$2.76/subscriber at average
consumption (`$10 − $4.20 Claude − $0.59 Stripe − $0.20 infra − $2.25`).
Still comfortably profitable.

### 2. Automated monthly payouts

New `payout_runs` table — an audit log that also de-dups the cron:
```sql
CREATE TABLE IF NOT EXISTS payout_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  period      TEXT NOT NULL,                 -- 'YYYY-MM'
  trigger     TEXT NOT NULL CHECK (trigger IN ('cron','admin')),
  ran_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  n_payouts   INTEGER NOT NULL DEFAULT 0,
  total_cents INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_payout_runs_period ON payout_runs(period);
```

- `auth.record_payout_run(period, trigger, n_payouts, total_cents) -> int`.
- `auth.payout_run_exists(period) -> bool` — true if any run (cron or admin)
  is recorded for that period.
- `auth.list_payout_runs(limit=12) -> list[dict]` — newest first, for the
  admin panel.

**Scheduler:** a background `asyncio` task started from a FastAPI **lifespan**
handler (the app has none today — add one). Loop: `await asyncio.sleep(6 *
3600)`, then — if `datetime.now().day >= PAYOUT_DAY` and not
`payout_run_exists(current_period)` — run `auth.run_payouts(...)`, record the
run with `trigger='cron'`, send payout emails for `paid` results, and send the
monthly summaries (§4). All wrapped so an exception only logs and the loop
continues. Constants in `server.py`:
- `PAYOUT_DAY = int(os.environ.get("BART_PAYOUT_DAY", "1"))`
- `PAYOUT_CRON_ENABLED = os.environ.get("BART_PAYOUT_CRON", "1") == "1"`
  — when false the task is never started.

The admin `POST /api/admin/payouts/run` endpoint also calls
`record_payout_run(period, 'admin', ...)` — so a manual run that month makes
the cron self-skip. Concurrency: `run_payouts`'s existing `BEGIN IMMEDIATE`
plus the per-period check keep a double run harmless (the second claims no
commissions). Assumes a single web instance; with multiple instances each
would schedule independently — set `BART_PAYOUT_CRON=0` on all but one, or
rely on the harmless-double-run property.

### 3. Referral analytics

New `referral_clicks` table — one row per creator per day:
```sql
CREATE TABLE IF NOT EXISTS referral_clicks (
  creator_id INTEGER NOT NULL,
  day        TEXT NOT NULL,                  -- 'YYYY-MM-DD'
  clicks     INTEGER NOT NULL DEFAULT 0,
  UNIQUE (creator_id, day),
  FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE CASCADE
);
```

- `auth.record_referral_click(code)` — resolves `code` → active creator;
  upserts `INSERT ... ON CONFLICT(creator_id, day) DO UPDATE SET clicks =
  clicks + 1`. No-op for an unknown/inactive code.
- The `/r/{code}` route calls `record_referral_click(safe)` after the code is
  validated (best-effort — wrapped so a failure never breaks the redirect).
- `auth.creator_analytics(creator) -> dict`:
  - `total_clicks` — SUM over `referral_clicks`.
  - `funnel` — `{clicks, signups, subscribers}` (signups = `total_referred`,
    subscribers = `active_subscribers`) plus `click_to_signup_pct` and
    `signup_to_subscriber_pct`.
  - `series` — a list of the last 30 days, each `{day, clicks, signups}`
    (clicks from `referral_clicks`, signups from `users.created_at` where
    `referred_by = code`). Days with no activity are present with zeros.
- `GET /api/creator/analytics` (server.py) — returns `creator_analytics` for
  the signed-in creator; 403 if the caller is not a creator. Kept separate
  from `/api/creator/me` so that endpoint stays focused.
- The creator dashboard adds a **Referral analytics** section: the funnel
  (three counts + two conversion percentages) and a 30-day bar chart of clicks
  vs. signups rendered with plain CSS/SVG bars — no chart library, matching the
  no-dependency QR-code approach.

Click counts are raw — bot/crawler inflation is accepted for this iteration
(noted as out of scope).

### 4. Notification emails

Three new functions in `emailer.py`, all routed through the existing
best-effort `send_email` (silent no-op when SMTP is unconfigured):

- `notify_creator_new_subscriber(creator_email, creator_name, amount_cents)` —
  "Someone subscribed through your bart link." Fired from
  `_credit_referral_commission` **only when the just-recorded commission is the
  first** for that `(creator, referred_user)` pair. Detected via new helper
  `auth.commission_count_for_referred(creator_id, referred_user_id) -> int` —
  send only when the count is exactly 1. No email on renewals.
- `notify_creator_payout(creator_email, creator_name, amount_cents, method)` —
  "We just sent your $X creator payout." Fired when a payout becomes `paid`:
  from the admin `mark-paid` endpoint, and from the cron for each
  `status == 'paid'` result of `run_payouts`.
- `notify_creator_monthly_summary(creator_email, creator_name, summary)` —
  one digest per creator per month (earnings this month, active subscribers,
  current tier rate, whether a payout went out). Sent by the cron after the
  monthly payout run, iterating every active creator.

To let callers send payout emails without `auth.py` importing `emailer`
(which would break the isolated-DB test setup), `run_payouts`'s result dicts
gain a `creator_email` field (the creator row is already in hand during
Phase 1). `mark_payout_paid` keeps its `bool` return; the admin endpoint looks
up the payout + creator to get the email.

### 5. Admin panel

`admin-creators.html` payouts panel gains a line showing the most recent
`payout_runs` entry (period, trigger, when, count, total) from a new
`GET /api/admin/payout-runs` endpoint (admin-only, returns
`list_payout_runs()`), so an admin can see that the cron is working.

## Configuration — new env vars

| Var | Default | Purpose |
|---|---|---|
| `BART_PAYOUT_DAY` | `1` | Day of month on/after which the cron runs the batch |
| `BART_PAYOUT_CRON` | `1` | `0` disables the in-process payout scheduler |

`CREATOR_TIERS` is a code constant, not an env var (changing payout tiers is a
deliberate, reviewed change).

## Testing

Unit-testable in `auth.py` (isolated-DB harness):
- `commission_cents_for` — boundary values (0, 49, 50, 51, large).
- `creator_commission_cents` — counts active subs, returns the right tier.
- `creator_earnings` — `monthly_run_rate_cents` uses the tiered rate;
  `commission_cents` and `next_tier` are correct below and at the top tier.
- `record_referral_click` — first hit inserts, subsequent hits increment the
  same day; unknown code is a no-op; a second day is a new row.
- `creator_analytics` — funnel counts and conversion %s; the 30-day series has
  30 entries with correct clicks/signups.
- `record_payout_run` / `payout_run_exists` / `list_payout_runs`.
- `commission_count_for_referred` — 0, 1, after a renewal 2.
- `run_payouts` result dicts include `creator_email`.
- Migration idempotency for the two new tables.

Not unit-testable (no server.py / scheduler / SMTP harness) — verified by
review: the lifespan hook, the scheduler loop, the webhook email trigger, the
`/r/` click call, and the new endpoints.

## Out of scope

- Per-referred-subscriber identity lists.
- Bot/fraud filtering of referral clicks.
- Multi-instance scheduler coordination (leader election).
- In-app (non-email) notifications.
- Configurable tiers via env var.
