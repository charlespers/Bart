# Creator Accounts, $2 Commission & Dashboard — Design Spec

**Date:** 2026-05-18
**Status:** Approved, ready for implementation planning

## Goal

Flesh out the bart creator program into a complete account: a flat **$2/month
commission per active referred subscriber** (paid for as long as that subscriber
stays subscribed), a real payout pipeline, and a fully-featured creator
dashboard. The change must keep bart **profitable per subscriber** — which
requires fixing the premium-tier cost structure at the same time.

## Background — current state

- Creators apply (`creator_applications`), an admin approves them, and they get
  a unique referral code + `/r/CODE` link (`creators` table).
- `referred_by` is stamped on a user at signup from the referral cookie.
- On a *verified* Stripe payment, `_credit_referral_commission` records one
  `commissions` row at **30% of the payment** (`CREATOR_COMMISSION_RATE`),
  idempotent on `stripe_ref`.
- `creator_summary` powers a minimal dashboard rendered in `creators.html`.
- There is **no payout mechanism** — earnings only accumulate.
- Premium ("claude") web runs use `primary_model: claude-opus-4-7`
  (`server.py` run config), metered at **12 premium runs/month for $10**.

## The unit-economics problem and fix

Using the cost model in `bart/orchestrator.py:_estimate_cost` and the pricing
table in `bart/telemetry.py`, one premium packet on the **Opus** pipeline costs
~**$3.5** (4 Opus authors ≈ $1.79, Opus reviewer pass ≈ $0.86, Sonnet daily
authors ≈ $0.69, Haiku sidecars ≈ $0.14). At a realistic ~3 packets/month the
COGS (~$10.5) plus Stripe fees already exceeds the $10 price — the premium tier
loses money *before* any commission.

**Fix:** route premium authoring to **Sonnet 4.6**. The heavy step (top-level
authors + reviewer) drops ~5× to ~$0.53 combined; per-packet cost falls to
~**$1.40**. Sonnet 4.6 remains genuinely top-tier quality, so the
`pricing.html` "top-tier Claude quality" copy stays accurate and is left
unchanged.

### Per-subscriber monthly P&L (post-fix)

| Line | Avg (~3 packets/mo) | Worst case (12 packets) |
|---|---|---|
| Revenue | +$10.00 | +$10.00 |
| Claude API (~$1.40/packet, Sonnet) | −$4.20 | −$16.80 |
| Stripe Checkout fee (2.9% + $0.30) | −$0.59 | −$0.59 |
| Infra (Fly.io, amortized) | −$0.20 | −$0.20 |
| Creator commission | −$2.00 | −$2.00 |
| **Net profit / referred subscriber** | **+$3.01** | **−$9.59** |

- **Break-even ≈ 5.1 premium packets/month** — comfortably above realistic
  consumption (~3, spiking near exams). The 12-packet meter caps the downside;
  the free unlimited Gemma engine absorbs heavy users.
- Non-referred subscriber nets **+$5.01/mo** at average consumption.
- The $2 is a recurring revenue-share funded by recurring revenue, not an
  upfront CAC. bart keeps ~$3/mo, the creator gets $2/mo — a sustainable
  ~60/40 split of post-COGS margin.

## Design

### 1. Commission model

Replace the 30% rate with a **flat $2.00 (200¢)** commission per verified
subscription payment.

- New constant `CREATOR_COMMISSION_CENTS`, configurable via
  `BART_CREATOR_COMMISSION_CENTS` (default `200`). `CREATOR_COMMISSION_RATE`
  is removed.
- `_credit_referral_commission` records `commission = min(CREATOR_COMMISSION_CENTS,
  amount_cents)` — the cap guarantees a discounted/zero payment never pays out
  more than was collected.
- Idempotency on `stripe_ref` is unchanged: one payment → at most one
  commission row.
- All "30%" copy is updated to "$2 every month they stay subscribed": the
  approval email in `server.py` and the program description in `creators.html`.

### 2. Data model (idempotent migrations, same pattern as `init_db`)

**`creators` — added columns:**
- `stripe_account_id TEXT` — Stripe Connect account id (`acct_…`), NULL until onboarded
- `payout_method TEXT` — `'stripe'` | `'manual'` | NULL
- `payout_details TEXT` — for manual payouts, the creator's payout handle (e.g. PayPal/Venmo email)
- `payouts_enabled INTEGER DEFAULT 0` — 1 once the Connect account can receive transfers

**New `payouts` table:**
```sql
CREATE TABLE IF NOT EXISTS payouts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id         INTEGER NOT NULL,
  amount_cents       INTEGER NOT NULL,
  currency           TEXT NOT NULL DEFAULT 'usd',
  method             TEXT NOT NULL,            -- 'stripe' | 'manual'
  status             TEXT NOT NULL,            -- 'pending' | 'paid' | 'failed'
  stripe_transfer_id TEXT,                     -- tr_… when method='stripe'
  note               TEXT,                     -- admin note for manual payouts
  period             TEXT,                     -- 'YYYY-MM' the payout covers
  created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at            TEXT,
  FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_payouts_creator ON payouts(creator_id, created_at DESC);
```

**`commissions` — added column:**
- `payout_id INTEGER` — the payout that settled this row; NULL = unpaid (counts
  toward pending balance). `FOREIGN KEY (payout_id) REFERENCES payouts(id)`.

### 3. Earnings computation

A `creator_earnings(creator)` function (extends/replaces `creator_summary`):
- `lifetime_earnings_cents` = SUM of all the creator's commissions
- `pending_balance_cents` = SUM of commissions WHERE `payout_id IS NULL`
- `paid_out_cents` = SUM of commissions WHERE `payout_id IS NOT NULL`
- `this_month_cents` = SUM of commissions created in the current `YYYY-MM`
- `total_referred` = COUNT(users WHERE `referred_by` = code)
- `active_subscribers` = COUNT(users WHERE `referred_by` = code AND
  `subscription_status = 'active'`)
- `monthly_run_rate_cents` = `active_subscribers × CREATOR_COMMISSION_CENTS`
  (projected next-month income)
- `conversion_pct` = `active_subscribers / total_referred` (0 when no referrals)

### 4. Payout pipeline

Mode is selected by `BART_STRIPE_CONNECT` (default `0` = manual; `1` = Connect).
Manual mode ships fully working with zero external setup.

**Stripe Connect onboarding (Connect mode):**
- `POST /api/creator/connect/start` — if the creator has no `stripe_account_id`,
  create a Stripe Express account (`stripe.Account.create(type='express')`) and
  store the id; then create an `AccountLink` (`type='account_onboarding'`) and
  return its hosted URL for the frontend to redirect to.
- `GET /api/creator/connect/refresh` — `stripe.Account.retrieve`; set
  `payouts_enabled` from the account's `payouts_enabled`/`charges_enabled`
  flags. Also called implicitly when `/api/creator/me` is loaded.

**Manual mode:**
- `POST /api/creator/payout-method` — the creator saves a payout handle into
  `payout_details` (`payout_method = 'manual'`).

**Monthly payout run** — `POST /api/admin/payouts/run` (admin only):
For each creator whose `pending_balance_cents >= BART_PAYOUT_MINIMUM_CENTS`
(default `2500` = $25):
1. Snapshot the creator's unpaid commission row ids and their exact sum.
2. Insert a `payouts` row (`status='pending'`, `period` = current month,
   `method` = `'stripe'` if Connect-enabled for that creator else `'manual'`).
3. Atomically claim exactly those commission rows:
   `UPDATE commissions SET payout_id = ? WHERE id IN (...)`.
4. **Connect, account ready:** `stripe.Transfer.create(amount, currency,
   destination=stripe_account_id)` → store `stripe_transfer_id`,
   `status='paid'`, `paid_at=now`. On Stripe error: `status='failed'` and
   un-claim the rows (`payout_id = NULL`) so the balance is restored and the
   next run retries.
5. **Manual (or Connect not onboarded):** leave `status='pending'` — it appears
   in the admin manual-payout queue.

**Manual settle** — `POST /api/admin/payouts/{id}/mark-paid` (admin): sets a
pending payout to `status='paid'`, `paid_at=now`, with an optional `note`.

### 5. API endpoints

Creator-facing (all behind `current_user`, must resolve to a creator):
- `GET /api/creator/me` — extended payload: earnings breakdown, payout method &
  status, `active_subscribers`, `monthly_run_rate`, and recent payout history.
- `POST /api/creator/payout-method` — set the manual payout handle.
- `POST /api/creator/connect/start` — begin Stripe Connect onboarding.
- `GET /api/creator/connect/refresh` — re-sync Connect account status.

Admin-only (`_require_admin`, 404 to non-admins like the trial-code endpoints):
- `GET /api/admin/payouts?status=` — list payouts.
- `POST /api/admin/payouts/run` — run the monthly batch; returns per-creator results.
- `POST /api/admin/payouts/{id}/mark-paid` — settle a pending/manual payout.
- `GET /api/admin/creators` — creators with earnings + payout state (extends the
  existing creator-applications admin view as needed).

### 6. Creator dashboard

The dashboard stays inside `creators.html` (the established pattern — one page,
state-driven), with three states keyed off `/api/creator/me`:
1. **Not a creator** — the existing apply form + trial-code panel.
2. **Application pending** — status message.
3. **Approved creator — full dashboard:**
   - **Earnings cards:** Pending balance (primary), This month, Lifetime
     earned, Paid out.
   - **Referral link:** the `/r/CODE` URL, copy button, a QR code, and
     pre-written share copy with X / WhatsApp / email share actions.
   - **Performance:** total referred, active subscribers (paying now), monthly
     run-rate ($2 × active), conversion %.
   - **Payout setup:** in Connect mode, a "Connect your bank" button →
     onboarding, then a "Payouts enabled ✓" state; in manual mode, a payout-
     handle field. Both explain the $25 minimum / monthly cadence.
   - **Payout history:** a table of past payouts (date, amount, method, status).
   - **How it works:** $2/mo per active subscriber, paid for as long as they
     stay subscribed; $25 minimum; paid monthly.

### 7. Admin UI

`admin-creators.html` gains:
- A **pending-payout queue** — creator, amount, method, with a "mark paid" +
  note action.
- A **"Run monthly payouts"** button calling `/api/admin/payouts/run`, showing
  the per-creator result.
- A **per-creator earnings overview** and program totals (total owed, total
  paid, active creators).

### 8. Pipeline change

In `server.py` run config: `primary_model` for premium ("claude") web runs
changes from `claude-opus-4-7` to `claude-sonnet-4-6`, env-overridable via
`BART_WEB_PRIMARY_MODEL` (default `claude-sonnet-4-6`). `daily_model` (Sonnet)
and `fast_model` (Haiku) are unchanged. `pricing.html` copy is left as-is —
"top-tier Claude quality" is still accurate for Sonnet 4.6.

### 9. Configuration — new env vars

| Var | Default | Purpose |
|---|---|---|
| `BART_CREATOR_COMMISSION_CENTS` | `200` | Flat per-payment commission |
| `BART_PAYOUT_MINIMUM_CENTS` | `2500` | Minimum balance to trigger a payout |
| `BART_STRIPE_CONNECT` | `0` | `1` enables Stripe Connect payout mode |
| `BART_WEB_PRIMARY_MODEL` | `claude-sonnet-4-6` | Premium web-run authoring model |

## Testing

- **Commission:** flat $2 recorded; `min` cap against the payment amount;
  idempotency on `stripe_ref`.
- **Earnings:** `pending_balance` / `paid_out` / `lifetime` / `this_month` math
  against seeded commissions; `active_subscribers` counts only `'active'`.
- **Payout run:** creators below $25 are skipped; eligible creators get one
  payout row; the exact unpaid commission rows are claimed; running twice does
  not double-pay (claimed rows no longer count).
- **Failure rollback:** a simulated `stripe.Transfer` failure marks the payout
  `failed` and restores `payout_id = NULL` on the rows.
- **Manual settle:** `mark-paid` flips status and stamps `paid_at`.
- **Connect:** account creation and status refresh with the Stripe client
  mocked.
- **Migrations:** running `init_db` twice is a no-op (new columns/tables).

## Deployment

After implementation with the full test suite green: add
`https://github.com/loctran0323/bart-copy` as a git remote and push `main`.

## Out of scope

- Stripe Connect express account *deletion* / offboarding.
- Per-referred-subscriber detail lists in the creator dashboard (aggregate
  counts only — avoids exposing referred users' identities).
- Automated (cron) payout scheduling — the monthly run is admin-triggered.
- Multi-currency payouts — `usd` only for now.
