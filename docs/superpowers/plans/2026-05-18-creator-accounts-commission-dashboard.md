# Creator Accounts, $2 Commission & Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 30% creator commission with a flat $2/month-per-active-subscriber model, add a payout pipeline (Stripe Connect Express + manual fallback) and a full creator dashboard, and route premium web-run authoring to Sonnet 4.6 so the economics stay profitable.

**Architecture:** All payout/earnings logic lives as pure-SQLite functions in `website/auth.py` (testable in isolation, the established pattern — see `tests/test_creator_program.py`). The payout *run* is orchestrated by `auth.run_payouts(...)`, which takes a `transfer_fn` callback so the Stripe transfer can be injected (real in `server.py`, fake in tests). `website/server.py` adds thin HTTP endpoints. UI is added to the existing `website/creators.html` (state-driven) and `website/admin-creators.html`.

**Tech Stack:** Python 3, SQLite (WAL), FastAPI, Stripe Python SDK, pytest, vanilla JS/HTML.

---

## Reference — codebase facts the engineer needs

- `website/auth.py` holds all DB logic. Schema is the `SCHEMA` string + idempotent `ALTER TABLE` blocks inside `init_db()` (SQLite has no `ALTER TABLE ... IF NOT EXISTS`, so each add checks `PRAGMA table_info` first).
- Tests load `auth.py` in isolation by stubbing `fastapi` and `bcrypt` (they are not in bart's venv). Copy the `_load_auth` helper from `tests/test_creator_program.py` exactly.
- `creators` table columns today: `id, user_id, name, email, referral_code, status, created_at`.
- `commissions` table: `id, creator_id, referred_user_id, amount_cents, currency, stripe_ref, created_at`.
- `users` has `referred_by` (the referral code) and `subscription_status` (`free|active|past_due|canceled|incomplete|trialing`).
- `record_commission(creator_id, referred_user_id, amount_cents, currency, stripe_ref)` → `bool`; idempotent on `stripe_ref`; returns `False` on empty `stripe_ref`.
- `server.py`: `_credit_referral_commission` (line ~554) computes the commission; `_stripe()` (line ~438) returns the configured `stripe` module or raises 503; `_require_admin(user)` (line ~1692) raises 404 for non-admins; `APP_PUBLIC_URL`, `STRIPE_SECRET_KEY` are module globals.
- HTML files are served by a generic route — `creators.html` and `admin-creators.html` already resolve at `/creators` and `/admin-creators`. No new file-serving route is needed.
- Run `pytest` from the repo root. Full suite is currently 164 tests passing.

---

## Task 1: Flat $2 commission

**Files:**
- Modify: `website/auth.py` (the `# ─── creator program ───` block, ~line 922-1156)
- Modify: `website/server.py` (`_credit_referral_commission`, ~line 554-577; approval email, ~line 773-778)
- Test: `tests/test_creator_program.py`

- [ ] **Step 1: Update the commission tests to expect a flat $2**

In `tests/test_creator_program.py`, replace `test_commission_credits_creator` and add a cap test:

```python
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
    assert summary["earnings_cents"] == 200
    assert summary["payments"] == 1
    assert summary["subscribed"] == 1


def test_commission_cents_is_env_overridable_and_positive(tmp_path):
    auth = _load_auth(tmp_path)
    assert isinstance(auth.CREATOR_COMMISSION_CENTS, int)
    assert auth.CREATOR_COMMISSION_CENTS >= 0
```

In `test_creator_summary_counts_signups`, delete the line
`assert 0.0 < summary["commission_rate"] <= 1.0` and replace with
`assert summary["commission_cents"] == 200`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_creator_program.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'CREATOR_COMMISSION_CENTS'`.

- [ ] **Step 3: Replace the rate constant in `auth.py`**

Find (`auth.py` ~line 924-927):
```python
# Share of each verified subscription payment paid to the referring creator.
# Tunable per-deployment; 0.30 = 30%.
CREATOR_COMMISSION_RATE = max(0.0, min(1.0, float(
    os.environ.get("BART_CREATOR_COMMISSION_RATE", "0.30"))))
```
Replace with:
```python
# Flat commission paid to the referring creator for each verified subscription
# payment — $2.00 by default. Paid every billing cycle the referred user keeps
# paying, so a creator earns $2/mo per active subscriber for as long as they
# stay subscribed. Tunable per-deployment.
CREATOR_COMMISSION_CENTS = max(0, int(
    os.environ.get("BART_CREATOR_COMMISSION_CENTS", "200")))
```

- [ ] **Step 4: Update `creator_summary` to report cents not rate**

In `creator_summary` (`auth.py` ~line 1147-1153), change the returned dict's
last key from `"commission_rate": CREATOR_COMMISSION_RATE,` to
`"commission_cents": CREATOR_COMMISSION_CENTS,`.

- [ ] **Step 5: Update `_credit_referral_commission` in `server.py`**

Replace the line `commission = round(amount_cents * auth.CREATOR_COMMISSION_RATE)` with:
```python
        # Flat $2 per verified payment, capped at the amount actually
        # collected so a discounted/zero payment never overpays.
        commission = min(auth.CREATOR_COMMISSION_CENTS, amount_cents)
```

- [ ] **Step 6: Update the approval email copy in `server.py`**

In `admin_approve_creator` (~line 773-778) replace the earnings sentence:
```python
            "Share it anywhere. When someone subscribes through it, you earn "
            f"{int(auth.CREATOR_COMMISSION_RATE * 100)}% of their payment — "
            "credited automatically once the payment clears.\n\n"
```
with:
```python
            "Share it anywhere. When someone subscribes through it, you earn "
            f"${auth.CREATOR_COMMISSION_CENTS / 100:.0f} every month they stay "
            "subscribed — credited automatically once each payment clears.\n\n"
```

- [ ] **Step 7: Search for any other `CREATOR_COMMISSION_RATE` reference**

Run: `grep -rn "CREATOR_COMMISSION_RATE" website/ tests/`
Expected: no matches. If any remain, update them to the cents model.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_creator_program.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add website/auth.py website/server.py tests/test_creator_program.py
git commit -m "feat(creators): flat \$2 commission per verified payment, replacing 30%"
```

---

## Task 2: Payouts schema — `payouts` table, `commissions.payout_id`, `creators` payout columns

**Files:**
- Modify: `website/auth.py` (`SCHEMA` string ~line 130-142; `init_db()` ~line 161-250)
- Test: `tests/test_payouts.py` (create)

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_payouts.py`. Copy the `_REPO`, `_AUTH_PATH`, `_load_module`, `_load_auth`, and `_apply` helpers verbatim from `tests/test_creator_program.py` (the `fastapi`/`bcrypt` stubs, `auth.DB_PATH`, `auth.init_db()`), naming the loaded module `"bart_web_auth_payouts"`. Then add:

```python
def _columns(auth, table):
    with auth._connect() as db:
        return {r["name"] for r in db.execute(
            f"PRAGMA table_info({table})").fetchall()}


def test_migration_adds_payout_columns_and_table(tmp_path):
    auth = _load_auth(tmp_path)
    creator_cols = _columns(auth, "creators")
    assert {"stripe_account_id", "payout_method",
            "payout_details", "payouts_enabled"} <= creator_cols
    assert "payout_id" in _columns(auth, "commissions")
    assert _columns(auth, "payouts") == {
        "id", "creator_id", "amount_cents", "currency", "method",
        "status", "stripe_transfer_id", "note", "period",
        "created_at", "paid_at",
    }


def test_init_db_is_idempotent(tmp_path):
    auth = _load_auth(tmp_path)
    auth.init_db()   # second call must not raise
    auth.init_db()
    assert "payout_id" in _columns(auth, "commissions")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_payouts.py -v`
Expected: FAIL — `payouts` table missing / `payout_id` column missing.

- [ ] **Step 3: Add the `payouts` table to `SCHEMA`**

In `auth.py`, after the `commissions` table + its index in the `SCHEMA` string (~line 142), add:
```sql
-- payouts: one row per batch payment of accumulated commissions to a creator.
-- A payout claims its commission rows by stamping commissions.payout_id.
CREATE TABLE IF NOT EXISTS payouts (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id         INTEGER NOT NULL,
  amount_cents       INTEGER NOT NULL,
  currency           TEXT NOT NULL DEFAULT 'usd',
  method             TEXT NOT NULL,            -- 'stripe' | 'manual'
  status             TEXT NOT NULL,            -- 'pending' | 'paid' | 'failed'
  stripe_transfer_id TEXT,
  note               TEXT,
  period             TEXT,                     -- 'YYYY-MM' the payout covers
  created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at            TEXT,
  FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_payouts_creator
  ON payouts(creator_id, created_at DESC);
```

- [ ] **Step 4: Add idempotent column migrations to `init_db()`**

In `init_db()`, after the `trial_credits` migration block (~line 246-247) and before `if billing_added:`, add:
```python
        # Creator payouts — Stripe Connect account + payout preferences, and
        # the link from each commission to the payout that settled it.
        creator_cols = {row["name"] for row in
                        db.execute("PRAGMA table_info(creators)").fetchall()}
        if "stripe_account_id" not in creator_cols:
            db.execute("ALTER TABLE creators ADD COLUMN stripe_account_id TEXT")
        if "payout_method" not in creator_cols:
            db.execute("ALTER TABLE creators ADD COLUMN payout_method TEXT")
        if "payout_details" not in creator_cols:
            db.execute("ALTER TABLE creators ADD COLUMN payout_details TEXT")
        if "payouts_enabled" not in creator_cols:
            db.execute("ALTER TABLE creators ADD COLUMN payouts_enabled INTEGER DEFAULT 0")
        commission_cols = {row["name"] for row in
                           db.execute("PRAGMA table_info(commissions)").fetchall()}
        if "payout_id" not in commission_cols:
            db.execute("ALTER TABLE commissions ADD COLUMN payout_id INTEGER")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_commissions_payout "
                "ON commissions(payout_id)"
            )
```
(The `payouts` table itself is created by `executescript(SCHEMA)` at the top of `init_db()`, so no extra step is needed for it.)

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_payouts.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): payouts table + creator payout columns + commissions.payout_id"
```

---

## Task 3: `creator_earnings` — the dashboard's numbers

**Files:**
- Modify: `website/auth.py` (add after `creator_summary`, ~line 1156)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_payouts.py`:
```python
def test_creator_earnings_breakdown(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    # two referred users, one subscribed-active, one not
    u1 = auth.create_user("u1@example.com", "pw123456", referred_by=code)
    u2 = auth.create_user("u2@example.com", "pw123456", referred_by=code)
    auth.update_subscription(u1, "sub_1", "active", None)
    # three $2 commissions for u1 over three months
    for i in range(3):
        auth.record_commission(creator["id"], u1, 200, "usd", f"cs_{i}")
    e = auth.creator_earnings(auth.get_creator_by_code(code))
    assert e["lifetime_earnings_cents"] == 600
    assert e["pending_balance_cents"] == 600     # none paid out yet
    assert e["paid_out_cents"] == 0
    assert e["total_referred"] == 2
    assert e["active_subscribers"] == 1
    assert e["monthly_run_rate_cents"] == 200    # 1 active sub * $2
    assert e["conversion_pct"] == 50.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_payouts.py::test_creator_earnings_breakdown -v`
Expected: FAIL — `creator_earnings` not defined.

- [ ] **Step 3: Implement `creator_earnings`**

Add to `auth.py` immediately after `creator_summary`:
```python
def creator_earnings(creator) -> dict:
    """Full earnings + referral breakdown for the creator dashboard.

    `pending_balance_cents` is the sum of commissions not yet attached to a
    payout — that is what a payout run pays out. `monthly_run_rate_cents`
    projects next month's income at $2 per currently-active subscriber."""
    code = creator["referral_code"]
    cid = creator["id"]
    period = _current_period()
    with _connect() as db:
        lifetime = db.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS c "
            "FROM commissions WHERE creator_id = ?", (cid,)
        ).fetchone()["c"]
        pending = db.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS c FROM commissions "
            "WHERE creator_id = ? AND payout_id IS NULL", (cid,)
        ).fetchone()["c"]
        this_month = db.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS c FROM commissions "
            "WHERE creator_id = ? AND substr(created_at, 1, 7) = ?",
            (cid, period)
        ).fetchone()["c"]
        total_referred = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?", (code,)
        ).fetchone()["n"]
        active = db.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE referred_by = ? AND subscription_status = 'active'", (code,)
        ).fetchone()["n"]
    return {
        "lifetime_earnings_cents": lifetime,
        "pending_balance_cents": pending,
        "paid_out_cents": lifetime - pending,
        "this_month_cents": this_month,
        "total_referred": total_referred,
        "active_subscribers": active,
        "monthly_run_rate_cents": active * CREATOR_COMMISSION_CENTS,
        "conversion_pct": round(100.0 * active / total_referred, 1)
                          if total_referred else 0.0,
    }
```

Note: `_current_period()` already exists in `auth.py` (returns `"YYYY-MM"`); confirm with `grep -n "_current_period" website/auth.py`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_payouts.py::test_creator_earnings_breakdown -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): creator_earnings — balance, run-rate, conversion"
```

---

## Task 4: Payout helpers — `run_payouts`, `mark_payout_paid`, `list_payouts`

**Files:**
- Modify: `website/auth.py` (add after `creator_earnings`)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_payouts.py`:
```python
def _seed_creator_with_balance(auth, cents, email="c@example.com"):
    creator = auth.approve_creator_application(
        auth.create_creator_application(
            name="C", email=email, audience="x", links="x", pitch="x"))
    uid = auth.create_user(f"sub-{email}", "pw123456",
                           referred_by=creator["referral_code"])
    n, rem = divmod(cents, 200)
    for i in range(n):
        auth.record_commission(creator["id"], uid, 200, "usd", f"cs-{email}-{i}")
    if rem:
        auth.record_commission(creator["id"], uid, rem, "usd", f"cs-{email}-r")
    return auth.get_creator_by_code(creator["referral_code"])


def test_run_payouts_skips_creators_below_minimum(tmp_path):
    auth = _load_auth(tmp_path)
    _seed_creator_with_balance(auth, 2000, "low@example.com")  # $20 < $25
    results = auth.run_payouts(minimum_cents=2500, period="2026-05")
    assert results == []


def test_run_payouts_manual_creates_pending_and_claims_commissions(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 3000, "ok@example.com")  # $30
    results = auth.run_payouts(minimum_cents=2500, period="2026-05")
    assert len(results) == 1
    assert results[0]["amount_cents"] == 3000
    assert results[0]["status"] == "pending"   # no transfer_fn -> manual
    # balance is now zero — commissions were claimed
    assert auth.creator_earnings(creator)["pending_balance_cents"] == 0
    # running again pays nothing (rows already claimed)
    assert auth.run_payouts(minimum_cents=2500, period="2026-05") == []


def test_run_payouts_stripe_success_marks_paid(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 4000, "s@example.com")
    auth.set_creator_stripe_account(creator["id"], "acct_123")
    auth.set_creator_payouts_enabled(creator["id"], True)
    calls = []
    def transfer(creator_row, amount_cents):
        calls.append((creator_row["id"], amount_cents))
        return "tr_abc"
    results = auth.run_payouts(2500, "2026-05", transfer_fn=transfer)
    assert calls == [(creator["id"], 4000)]
    assert results[0]["status"] == "paid"
    assert results[0]["stripe_transfer_id"] == "tr_abc"


def test_run_payouts_stripe_failure_rolls_back(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 4000, "f@example.com")
    auth.set_creator_stripe_account(creator["id"], "acct_x")
    auth.set_creator_payouts_enabled(creator["id"], True)
    def transfer(creator_row, amount_cents):
        raise RuntimeError("stripe down")
    results = auth.run_payouts(2500, "2026-05", transfer_fn=transfer)
    assert results[0]["status"] == "failed"
    # balance is restored — the commissions were un-claimed
    assert auth.creator_earnings(creator)["pending_balance_cents"] == 4000


def test_mark_payout_paid(tmp_path):
    auth = _load_auth(tmp_path)
    _seed_creator_with_balance(auth, 3000, "m@example.com")
    payout = auth.run_payouts(2500, "2026-05")[0]
    assert auth.mark_payout_paid(payout["id"], note="venmo sent") is True
    rows = auth.list_payouts()
    assert rows[0]["status"] == "paid" and rows[0]["paid_at"] is not None
    assert rows[0]["note"] == "venmo sent"
    # marking an unknown / already-paid payout is a no-op False
    assert auth.mark_payout_paid(payout["id"]) is False
    assert auth.mark_payout_paid(999999) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_payouts.py -v -k "payout"`
Expected: FAIL — `run_payouts` / `set_creator_stripe_account` not defined.

- [ ] **Step 3: Implement the creator payout-setting helpers**

Add to `auth.py` (after `creator_earnings`):
```python
def set_creator_stripe_account(creator_id: int, account_id: str) -> None:
    """Store the creator's Stripe Connect account id."""
    with _connect() as db:
        db.execute(
            "UPDATE creators SET stripe_account_id = ?, payout_method = 'stripe' "
            "WHERE id = ?", (account_id, creator_id),
        )


def set_creator_payouts_enabled(creator_id: int, enabled: bool) -> None:
    """Flag whether the creator's Connect account can receive transfers."""
    with _connect() as db:
        db.execute(
            "UPDATE creators SET payouts_enabled = ? WHERE id = ?",
            (1 if enabled else 0, creator_id),
        )


def set_creator_payout_method(creator_id: int, details: str) -> None:
    """Save a manual payout handle (e.g. a PayPal/Venmo email)."""
    with _connect() as db:
        db.execute(
            "UPDATE creators SET payout_method = 'manual', payout_details = ? "
            "WHERE id = ?", ((details or "").strip() or None, creator_id),
        )
```

- [ ] **Step 4: Implement `run_payouts`, `mark_payout_paid`, `list_payouts`**

Add to `auth.py`:
```python
def _unpaid_commission_ids(db, creator_id: int) -> tuple[list[int], int]:
    """Return (ids, total_cents) of a creator's not-yet-paid-out commissions."""
    rows = db.execute(
        "SELECT id, amount_cents FROM commissions "
        "WHERE creator_id = ? AND payout_id IS NULL", (creator_id,)
    ).fetchall()
    return [r["id"] for r in rows], sum(r["amount_cents"] for r in rows)


def run_payouts(minimum_cents: int, period: str,
                transfer_fn=None) -> list[dict]:
    """Pay out every creator whose unpaid-commission balance >= minimum_cents.

    For each eligible creator: create a `payouts` row, atomically claim that
    creator's unpaid commission rows (stamping `commissions.payout_id`), then —
    if the creator has Connect enabled and `transfer_fn` is given — attempt the
    transfer. `transfer_fn(creator_row, amount_cents)` returns a transfer id or
    raises; on a raise the payout is marked 'failed' and its commissions are
    un-claimed so the balance is restored for the next run. Creators without
    Connect (or when `transfer_fn` is None) get a 'pending' manual payout.

    Returns one result dict per payout created."""
    results: list[dict] = []
    with _connect() as db:
        creators = db.execute(
            "SELECT * FROM creators WHERE status = 'active'").fetchall()
        for creator in creators:
            ids, total = _unpaid_commission_ids(db, creator["id"])
            if total < minimum_cents or not ids:
                continue
            use_stripe = bool(
                transfer_fn is not None
                and creator["stripe_account_id"]
                and creator["payouts_enabled"])
            method = "stripe" if use_stripe else "manual"
            cur = db.execute(
                "INSERT INTO payouts "
                "(creator_id, amount_cents, currency, method, status, period) "
                "VALUES (?, ?, 'usd', ?, 'pending', ?)",
                (creator["id"], total, method, period),
            )
            payout_id = cur.lastrowid
            db.execute(
                f"UPDATE commissions SET payout_id = ? WHERE id IN "
                f"({','.join('?' * len(ids))})",
                (payout_id, *ids),
            )
            status, transfer_id = "pending", None
            if use_stripe:
                try:
                    transfer_id = transfer_fn(creator, total)
                    status = "paid"
                except Exception as e:  # noqa: BLE001 — roll the claim back
                    status = "failed"
                    db.execute(
                        f"UPDATE commissions SET payout_id = NULL WHERE id IN "
                        f"({','.join('?' * len(ids))})", tuple(ids),
                    )
                    print(f"[payout] transfer failed for creator "
                          f"{creator['id']}: {e}", file=sys.stderr, flush=True)
            db.execute(
                "UPDATE payouts SET status = ?, stripe_transfer_id = ?, "
                "paid_at = CASE WHEN ? = 'paid' THEN CURRENT_TIMESTAMP END "
                "WHERE id = ?",
                (status, transfer_id, status, payout_id),
            )
            results.append({
                "id": payout_id, "creator_id": creator["id"],
                "amount_cents": total, "method": method, "status": status,
                "stripe_transfer_id": transfer_id,
            })
    return results


def mark_payout_paid(payout_id: int, note: str = "") -> bool:
    """Settle a still-pending payout (manual path). Returns True iff a pending
    payout was actually updated — already-paid/failed/unknown ids return False."""
    with _connect() as db:
        cur = db.execute(
            "UPDATE payouts SET status = 'paid', paid_at = CURRENT_TIMESTAMP, "
            "note = COALESCE(NULLIF(?, ''), note) "
            "WHERE id = ? AND status = 'pending'",
            ((note or "").strip(), payout_id),
        )
        return cur.rowcount > 0


def list_payouts(creator_id: Optional[int] = None,
                 status: Optional[str] = None) -> list[dict]:
    """Payouts newest-first, optionally filtered by creator and/or status."""
    clauses, args = [], []
    if creator_id is not None:
        clauses.append("creator_id = ?"); args.append(creator_id)
    if status:
        clauses.append("status = ?"); args.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as db:
        return [dict(r) for r in db.execute(
            f"SELECT * FROM payouts{where} ORDER BY created_at DESC, id DESC",
            tuple(args),
        ).fetchall()]
```

Confirm `import sys` and `from typing import Optional` are already present at the top of `auth.py` (they are — used elsewhere). If not, the engineer must add them.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_payouts.py -v`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): run_payouts pipeline + manual settle + payout listing"
```

---

## Task 5: Route premium web runs to Sonnet 4.6

**Files:**
- Modify: `website/server.py` (module constants near `STRIPE_*`, ~line 60; run config ~line 1020)

- [ ] **Step 1: Add the env-overridable model constant**

In `server.py`, near the other `os.environ` constants (~line 60-63), add:
```python
# Premium ("claude") web-run authoring model. Sonnet 4.6 is ~5x cheaper than
# Opus on the heavy authoring step and keeps the $10/mo tier profitable with
# the creator commission stacked on (see the creator-accounts design spec).
WEB_PRIMARY_MODEL = os.environ.get("BART_WEB_PRIMARY_MODEL", "claude-sonnet-4-6")
```

- [ ] **Step 2: Use it in the run config**

In the run-config dict (~line 1020), change:
```python
        "primary_model": "claude-opus-4-7",
```
to:
```python
        "primary_model": WEB_PRIMARY_MODEL,
```
Leave `daily_model` and `fast_model` unchanged.

- [ ] **Step 3: Verify**

Run: `grep -n "WEB_PRIMARY_MODEL\|primary_model" website/server.py`
Expected: the constant defaults to `claude-sonnet-4-6` and `primary_model` references `WEB_PRIMARY_MODEL`; no remaining hardcoded `claude-opus-4-7` in the run config.

- [ ] **Step 4: Commit**

```bash
git add website/server.py
git commit -m "feat(web): route premium runs to Sonnet 4.6 to keep the tier profitable"
```

---

## Task 6: Creator-facing API endpoints

**Files:**
- Modify: `website/server.py` (extend `creator_me` ~line 730-744; add new endpoints after it)

Note: `server.py` HTTP endpoints are not exercised by the isolated test harness; verify by reading and a manual smoke test. The DB logic they call is already covered by Tasks 1-4.

- [ ] **Step 1: Extend `/api/creator/me` with earnings + payouts**

Replace the body of `creator_me` (~line 730-744) so the creator branch returns:
```python
@app.get("/api/creator/me")
async def creator_me(user=Depends(auth.current_user)):
    """The signed-in user's creator status: full dashboard payload once
    approved (referral link, earnings, payout state, payout history),
    otherwise their application status."""
    creator = auth.get_creator_for_user(user)
    if creator is not None:
        earnings = auth.creator_earnings(creator)
        payouts = auth.list_payouts(creator_id=creator["id"])[:20]
        return {
            "is_creator": True,
            "name": creator["name"],
            "referral_code": creator["referral_code"],
            "referral_url": f"{APP_PUBLIC_URL}/r/{creator['referral_code']}",
            "commission_cents": auth.CREATOR_COMMISSION_CENTS,
            "payout_minimum_cents": PAYOUT_MINIMUM_CENTS,
            "connect_mode": STRIPE_CONNECT_ENABLED,
            "payout_method": creator["payout_method"],
            "payout_details": creator["payout_details"],
            "payouts_enabled": bool(creator["payouts_enabled"]),
            "has_stripe_account": bool(creator["stripe_account_id"]),
            "earnings": earnings,
            "payouts": payouts,
        }
    app = auth.latest_application_for_email(user["email"] or "")
    return {
        "is_creator": False,
        "application_status": app["status"] if app is not None else None,
    }
```

- [ ] **Step 2: Add the two new module constants**

Near `WEB_PRIMARY_MODEL` (Task 5), add:
```python
# Creator payouts. PAYOUT_MINIMUM_CENTS is the balance a creator must reach
# before a payout run pays them. STRIPE_CONNECT_ENABLED switches the dashboard
# between Connect onboarding and manual-handle entry.
PAYOUT_MINIMUM_CENTS = max(0, int(
    os.environ.get("BART_PAYOUT_MINIMUM_CENTS", "2500")))
STRIPE_CONNECT_ENABLED = os.environ.get("BART_STRIPE_CONNECT", "0") == "1"
```

- [ ] **Step 3: Add the payout-method endpoint**

After `creator_me`, add:
```python
class PayoutMethodRequest(BaseModel):
    details: str = ""


@app.post("/api/creator/payout-method")
async def creator_payout_method(req: PayoutMethodRequest,
                                user=Depends(auth.current_user)):
    """Save a manual payout handle (PayPal/Venmo email) for the creator."""
    creator = auth.get_creator_for_user(user)
    if creator is None:
        raise HTTPException(403, "you're not a bart creator.")
    details = (req.details or "").strip()
    if not details:
        raise HTTPException(400, "enter a payout handle.")
    auth.set_creator_payout_method(creator["id"], details)
    return {"ok": True}
```

- [ ] **Step 4: Add the Stripe Connect onboarding endpoints**

After the payout-method endpoint, add:
```python
@app.post("/api/creator/connect/start")
async def creator_connect_start(user=Depends(auth.current_user)):
    """Begin Stripe Connect Express onboarding — create the connected account
    if needed, then return a hosted onboarding URL for the browser to open."""
    if not STRIPE_CONNECT_ENABLED:
        raise HTTPException(409, "stripe payouts aren't enabled — your bart "
                                 "payout is handled manually.")
    creator = auth.get_creator_for_user(user)
    if creator is None:
        raise HTTPException(403, "you're not a bart creator.")
    stripe = _stripe()
    account_id = creator["stripe_account_id"]
    if not account_id:
        acct = stripe.Account.create(
            type="express",
            email=creator["email"],
            capabilities={"transfers": {"requested": True}},
        )
        account_id = acct["id"]
        auth.set_creator_stripe_account(creator["id"], account_id)
    link = stripe.AccountLink.create(
        account=account_id,
        refresh_url=f"{APP_PUBLIC_URL}/creators?connect=refresh",
        return_url=f"{APP_PUBLIC_URL}/creators?connect=done",
        type="account_onboarding",
    )
    return {"url": link["url"]}


@app.get("/api/creator/connect/refresh")
async def creator_connect_refresh(user=Depends(auth.current_user)):
    """Re-sync the creator's Connect account status from Stripe."""
    creator = auth.get_creator_for_user(user)
    if creator is None:
        raise HTTPException(403, "you're not a bart creator.")
    if not creator["stripe_account_id"]:
        return {"payouts_enabled": False}
    acct = _stripe().Account.retrieve(creator["stripe_account_id"])
    enabled = bool(acct.get("payouts_enabled") and acct.get("charges_enabled"))
    auth.set_creator_payouts_enabled(creator["id"], enabled)
    return {"payouts_enabled": enabled}
```

`BaseModel` is already imported in `server.py` (used by `CreatorApplyRequest`); confirm with `grep -n "from pydantic" website/server.py`.

- [ ] **Step 5: Verify the module imports and runs**

Run: `python -c "import ast; ast.parse(open('website/server.py').read()); print('ok')"`
Expected: `ok` (syntax valid).

- [ ] **Step 6: Commit**

```bash
git add website/server.py
git commit -m "feat(creators): creator dashboard API — earnings, payout method, Stripe Connect onboarding"
```

---

## Task 7: Admin payout API endpoints

**Files:**
- Modify: `website/server.py` (after the admin creator-application endpoints, ~line 800)

- [ ] **Step 1: Add the Stripe transfer helper**

After `_credit_referral_commission` (or near `_stripe`), add:
```python
def _stripe_transfer(creator_row, amount_cents: int) -> str:
    """Transfer `amount_cents` to a creator's Connect account. Returns the
    Stripe transfer id; raises on failure (run_payouts catches and rolls back)."""
    tr = _stripe().Transfer.create(
        amount=amount_cents,
        currency="usd",
        destination=creator_row["stripe_account_id"],
        description=f"bart creator payout — {creator_row['referral_code']}",
    )
    return tr["id"]
```

- [ ] **Step 2: Add the admin payout endpoints**

After `admin_reject_creator` (~line 800), add:
```python
@app.get("/api/admin/payouts")
async def admin_list_payouts(status: str = "all", user=Depends(auth.current_user)):
    """All payouts, newest first — admin only. ?status=pending|paid|failed|all."""
    _require_admin(user)
    want = None if status == "all" else status
    return {"payouts": auth.list_payouts(status=want)}


@app.post("/api/admin/payouts/run")
async def admin_run_payouts(user=Depends(auth.current_user)):
    """Run the monthly payout batch — pays every creator at or above the
    minimum balance. Stripe Connect creators are transferred automatically;
    everyone else gets a pending payout for the manual queue."""
    _require_admin(user)
    period = auth._current_period()
    transfer = _stripe_transfer if STRIPE_CONNECT_ENABLED else None
    results = auth.run_payouts(PAYOUT_MINIMUM_CENTS, period, transfer_fn=transfer)
    return {"ok": True, "period": period, "payouts": results}


class MarkPaidRequest(BaseModel):
    note: str = ""


@app.post("/api/admin/payouts/{payout_id}/mark-paid")
async def admin_mark_payout_paid(payout_id: int, req: MarkPaidRequest,
                                 user=Depends(auth.current_user)):
    """Settle a pending (manual) payout once the money has been sent."""
    _require_admin(user)
    if not auth.mark_payout_paid(payout_id, note=req.note):
        raise HTTPException(404, "no such pending payout.")
    return {"ok": True}
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "import ast; ast.parse(open('website/server.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add website/server.py
git commit -m "feat(creators): admin payout API — list, run monthly batch, mark paid"
```

---

## Task 8: Creator dashboard UI

**Files:**
- Modify: `website/creators.html`

The page already fetches `/api/creator/me` and renders a minimal dashboard for approved creators. This task replaces that approved-creator branch with the full dashboard. The not-a-creator (apply form + trial code) and pending states are unchanged.

- [ ] **Step 1: Read the current file**

Read `website/creators.html` in full. Locate (a) the JS that fetches `/api/creator/me` and the function that renders the approved-creator state, and (b) the matching markup container. Note the existing CSS class names / design tokens so the new dashboard matches the site style.

- [ ] **Step 2: Replace the approved-creator dashboard markup + render code**

Render, for `is_creator === true`, these sections (use the page's existing
card/button classes; all money is `(_cents / 100)` formatted as `$X.XX`):

1. **Header** — "Creator dashboard", the creator's name, an "active" badge.
2. **Earnings cards** (4): Pending balance (`earnings.pending_balance_cents`,
   visually primary), This month (`earnings.this_month_cents`), Lifetime earned
   (`earnings.lifetime_earnings_cents`), Paid out (`earnings.paid_out_cents`).
3. **Referral link** — show `referral_url` in a read-only field with a Copy
   button (`navigator.clipboard.writeText`); render a QR code for the URL
   using a `<canvas>` and a tiny inline QR routine, OR an `<img>` pointing at
   `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=<encoded url>`
   (use the `<img>` approach — no dependency); pre-written share text
   (`"I use bart to make study packets — try it: <url>"`) with X, WhatsApp, and
   email share links.
4. **Performance** — Total referred (`earnings.total_referred`), Active
   subscribers (`earnings.active_subscribers`), Monthly run-rate
   (`earnings.monthly_run_rate_cents`, labelled "projected / month"),
   Conversion (`earnings.conversion_pct` + "%").
5. **Payout setup** — branch on `connect_mode`:
   - `connect_mode === true`: if `payouts_enabled`, show "Payouts enabled ✓";
     else a "Connect your bank" button that `POST`s `/api/creator/connect/start`
     and on success does `window.location = resp.url`. On page load, if the URL
     has `?connect=done`, call `GET /api/creator/connect/refresh` then re-fetch
     `/api/creator/me`.
   - `connect_mode === false`: a text input pre-filled with `payout_details`
     and a Save button that `POST`s `/api/creator/payout-method`
     (`{details}`); helper text "we pay out monthly once you reach
     $`payout_minimum_cents/100`."
6. **Payout history** — a table over `payouts[]`: created_at (date only),
   amount (`amount_cents`), method, status. Show an empty-state line when
   `payouts` is empty.
7. **How it works** — static copy: "$`commission_cents/100` per active
   subscriber, every month they stay subscribed. Paid out monthly once your
   balance reaches $`payout_minimum_cents/100`."

- [ ] **Step 3: Update the program-description copy**

Search `creators.html` for "30%" and any "% of" earnings copy and replace with
the flat-$2 language ("earn $2 every month each subscriber stays subscribed").

- [ ] **Step 4: Smoke-test in a browser**

Start the server (`cd website && ./dev.sh` or the documented dev command),
sign in as an approved creator, open `/creators`. Confirm the dashboard renders,
the copy button works, and the payout-setup section matches the configured
mode. Confirm a non-creator still sees the apply form.

- [ ] **Step 5: Commit**

```bash
git add website/creators.html
git commit -m "feat(creators): full creator dashboard — earnings, referral kit, payouts"
```

---

## Task 9: Admin creator/payout UI

**Files:**
- Modify: `website/admin-creators.html`

- [ ] **Step 1: Read the current file**

Read `website/admin-creators.html`; note its fetch/render pattern and CSS classes.

- [ ] **Step 2: Add a payouts panel**

Below the existing applications panel, add a "Payouts" section that:
- Has a **"Run monthly payouts"** button → `POST /api/admin/payouts/run`; on
  success show a summary ("N payouts, $X total") and re-load the list.
- Fetches `GET /api/admin/payouts?status=all` and renders a table: creator id,
  amount, method, status, created_at, period.
- For each `status === "pending"` row, shows a **"Mark paid"** action with an
  optional note input → `POST /api/admin/payouts/{id}/mark-paid` (`{note}`),
  then re-loads.
- Shows program totals computed from the rows: total paid, total pending.

- [ ] **Step 3: Smoke-test**

As an admin, open `/admin-creators`, confirm the payouts panel loads, run a
payout batch against seeded data, and mark a pending payout paid.

- [ ] **Step 4: Commit**

```bash
git add website/admin-creators.html
git commit -m "feat(admin): creator payouts panel — run batch, mark paid, totals"
```

---

## Task 10: Full suite + deploy

**Files:** none (verification + git)

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -q`
Expected: all tests pass — the prior 164 plus the new `tests/test_payouts.py`
tests and the updated `tests/test_creator_program.py`. If anything fails, fix
it before continuing.

- [ ] **Step 2: Confirm no stale references**

Run: `grep -rn "CREATOR_COMMISSION_RATE\|commission_rate" website/ tests/`
Expected: no matches.

- [ ] **Step 3: Merge the feature branch to `main`**

```bash
git checkout main
git merge --no-ff feat/creator-accounts-commission -m "feat(creators): \$2 commission, payout pipeline & creator dashboard"
```

- [ ] **Step 4: Add the deploy remote and push**

```bash
git remote add bart-copy https://github.com/loctran0323/bart-copy 2>/dev/null || git remote set-url bart-copy https://github.com/loctran0323/bart-copy
git push bart-copy main
```

Expected: `main` pushes to `loctran0323/bart-copy`. If the push is rejected
(remote has history), stop and report — do not force-push without asking.

- [ ] **Step 5: Report**

Summarize: tests passing count, the new env vars to set on the deploy
(`BART_CREATOR_COMMISSION_CENTS`, `BART_PAYOUT_MINIMUM_CENTS`,
`BART_STRIPE_CONNECT`, `BART_WEB_PRIMARY_MODEL`), and that `BART_STRIPE_CONNECT`
must be `1` (plus Connect enabled on the Stripe account) for automated payouts —
otherwise payouts run in manual mode.

---

## Self-review notes

- **Spec coverage:** commission (T1), schema (T2), earnings (T3), payout
  pipeline + Connect helpers (T4), Sonnet routing (T5), creator API (T6), admin
  API (T7), creator dashboard (T8), admin UI (T9), tests + deploy (T10). All
  spec sections map to a task.
- **Out-of-scope items** (Connect offboarding, per-subscriber detail lists,
  cron scheduling, multi-currency) are intentionally not tasked.
- **Type consistency:** `creator_earnings` keys (`pending_balance_cents`,
  `monthly_run_rate_cents`, etc.) are produced in T3 and consumed identically
  in T6/T8. `run_payouts(minimum_cents, period, transfer_fn)` signature is
  identical across T4 (definition), T4 tests, and T7 (call site). `payouts`
  table columns match between the T2 schema and the T4 `list_payouts`/
  `run_payouts` queries.
