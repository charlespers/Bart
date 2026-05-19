# Creator Program — Depth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a volume-tiered commission, automated monthly payouts, referral-click analytics, and creator notification emails to the bart creator program.

**Architecture:** All data logic stays as pure-SQLite functions in `website/auth.py` (unit-tested in isolation via `tests/test_payouts.py`). `website/server.py` adds thin HTTP endpoints, a webhook trigger, a `/r/` click hook, and an in-process `asyncio` payout scheduler. `website/emailer.py` gains three best-effort notification functions. UI is added to `website/creators.html` and `website/admin-creators.html`.

**Tech Stack:** Python 3, SQLite (WAL), FastAPI, Stripe SDK, pytest, vanilla JS/HTML.

---

## Reference — codebase facts the engineer needs

- `website/auth.py`: all DB logic. Schema = the `SCHEMA` string passed to `executescript` + idempotent `ALTER TABLE` blocks in `init_db()`. Imports already include `from datetime import datetime, timedelta, timezone` and `from typing import Optional`. `_connect()` is a context-managed sqlite connection (`row_factory = sqlite3.Row`, commits on exit). `_current_period()` returns `datetime.now(timezone.utc).strftime("%Y-%m")`.
- Existing creator helpers: `get_creator_by_code(code)` (returns **active** creators only), `get_creator_by_email`, `get_creator_for_user`, `creator_earnings(creator)`, `record_commission(...)`, `run_payouts(minimum_cents, period=None, transfer_fn=None)`, `mark_payout_paid`, `list_payouts`, `list_creators()`. `CREATOR_COMMISSION_CENTS` = 200.
- `tests/test_payouts.py` exists and is tracked — append tests there. It has helpers `_load_auth(tmp_path)`, `_apply(auth)`, `_columns(auth, table)`, `_seed_creator_with_balance(auth, cents, email)`. `tests/` is gitignored; new files need `git add -f` but `test_payouts.py` is already tracked so a plain `git add` works.
- `website/server.py`: `app = FastAPI(title="bart website")` at line 111. `auth` and `emailer` are imported. `APP_PUBLIC_URL`, `STRIPE_SECRET_KEY`, `STRIPE_CONNECT_ENABLED`, `PAYOUT_MINIMUM_CENTS`, `WEB_PRIMARY_MODEL` are module constants near line 60-73. `_require_admin(user)` raises `HTTPException(404)`. `_stripe_transfer(creator_row, amount_cents)` exists. `import asyncio` is present. `_credit_referral_commission` is at line 564; the `/r/{code}` route at ~1961; `admin_run_payouts` at ~929; `admin_mark_payout_paid` after it.
- `website/emailer.py`: `send_email(to, subject, body, *, reply_to="")` (best-effort, returns False if SMTP unconfigured), `smtp_configured()`, `notify_creator_application(app)`. Public URL is hardcoded as `https://studywithbart.com` in existing copy.
- Run tests: `python3 -m pytest -q` from repo root. Current suite: 197 passing.

---

## Task 1: Tiered commission logic

**Files:**
- Modify: `website/auth.py` (the `# ─── creator program ───` block — `CREATOR_COMMISSION_CENTS` constant, and `creator_earnings`)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payouts.py`:
```python
def test_commission_cents_for_tier_boundaries(tmp_path):
    auth = _load_auth(tmp_path)
    assert auth.commission_cents_for(0) == 200
    assert auth.commission_cents_for(49) == 200
    assert auth.commission_cents_for(50) == 225
    assert auth.commission_cents_for(51) == 225
    assert auth.commission_cents_for(10000) == 225
    assert auth.CREATOR_TIERS[0] == (0, auth.CREATOR_COMMISSION_CENTS)


def test_next_tier_for(tmp_path):
    auth = _load_auth(tmp_path)
    assert auth.next_tier_for(0) == {"at": 50, "cents": 225}
    assert auth.next_tier_for(49) == {"at": 50, "cents": 225}
    assert auth.next_tier_for(50) is None
    assert auth.next_tier_for(999) is None


def test_creator_commission_cents_uses_live_active_count(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    assert auth.creator_commission_cents(creator) == 200
    # 50 active referred subscribers -> tier 2
    for i in range(50):
        uid = auth.create_user(f"sub{i}@example.com", "pw123456", referred_by=code)
        auth.update_subscription(uid, f"sub_{i}", "active", None)
    assert auth.creator_commission_cents(auth.get_creator_by_code(code)) == 225


def test_creator_earnings_exposes_tier(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    for i in range(3):
        uid = auth.create_user(f"e{i}@example.com", "pw123456", referred_by=code)
        auth.update_subscription(uid, f"s_{i}", "active", None)
    e = auth.creator_earnings(auth.get_creator_by_code(code))
    assert e["commission_cents"] == 200
    assert e["next_tier"] == {"at": 50, "cents": 225}
    assert e["monthly_run_rate_cents"] == 3 * 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_payouts.py -v -k "tier or commission_cents"`
Expected: FAIL — `commission_cents_for` / `next_tier_for` / `creator_commission_cents` not defined.

- [ ] **Step 3: Add the tier constant and functions**

In `website/auth.py`, find the line defining `CREATOR_COMMISSION_CENTS` (in the creator-program block). Immediately after it, add:
```python
# Commission tiers: (min_active_subscribers, cents), ascending. A creator's
# current active-subscriber count selects the tier, and the rate is
# retroactive — crossing a breakpoint lifts the rate on every subscriber.
# CREATOR_TIERS[0] is the base rate (== CREATOR_COMMISSION_CENTS).
CREATOR_TIERS = [(0, CREATOR_COMMISSION_CENTS), (50, 225)]


def commission_cents_for(active_subscribers: int) -> int:
    """The per-payment commission for a creator with this many active
    subscribers — the cents of the highest tier whose threshold is met."""
    rate = CREATOR_TIERS[0][1]
    for threshold, cents in CREATOR_TIERS:
        if active_subscribers >= threshold:
            rate = cents
        else:
            break
    return rate


def next_tier_for(active_subscribers: int):
    """The next tier up as {'at': subs, 'cents': rate}, or None if the
    creator is already in the top tier."""
    for threshold, cents in CREATOR_TIERS:
        if threshold > active_subscribers:
            return {"at": threshold, "cents": cents}
    return None


def creator_commission_cents(creator) -> int:
    """The commission rate (cents per payment) the creator currently earns,
    based on their live count of active referred subscribers."""
    with _connect() as db:
        active = db.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE referred_by = ? AND subscription_status = 'active'",
            (creator["referral_code"],),
        ).fetchone()["n"]
    return commission_cents_for(active)
```

- [ ] **Step 4: Update `creator_earnings` to expose the tier**

In `creator_earnings`, the return dict currently ends with `monthly_run_rate_cents` and `conversion_pct`. Change the `monthly_run_rate_cents` line and add two keys:
```python
        "monthly_run_rate_cents": active * commission_cents_for(active),
        "conversion_pct": round(100.0 * active / total_referred, 1)
                          if total_referred else 0.0,
        "commission_cents": commission_cents_for(active),
        "next_tier": next_tier_for(active),
```
(`active` is already computed in `creator_earnings` — reuse it.)

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests/test_payouts.py -v`
Expected: PASS (all — the new tests plus the existing payout tests).

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): volume-tiered commission (\$2 / \$2.25 at 50 active subs)"
```

---

## Task 2: `payout_runs` table + helpers

**Files:**
- Modify: `website/auth.py` (`SCHEMA` string; add helper functions)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payouts.py`:
```python
def test_payout_runs_record_and_exists(tmp_path):
    auth = _load_auth(tmp_path)
    assert auth.payout_run_exists("2026-05") is False
    rid = auth.record_payout_run("2026-05", "cron", 3, 7500)
    assert rid > 0
    assert auth.payout_run_exists("2026-05") is True
    assert auth.payout_run_exists("2026-04") is False


def test_list_payout_runs_newest_first(tmp_path):
    auth = _load_auth(tmp_path)
    auth.record_payout_run("2026-03", "cron", 1, 100)
    auth.record_payout_run("2026-04", "admin", 2, 200)
    runs = auth.list_payout_runs()
    assert len(runs) == 2
    assert runs[0]["period"] == "2026-04"
    assert runs[0]["trigger"] == "admin"
    assert runs[0]["n_payouts"] == 2
    assert runs[0]["total_cents"] == 200


def test_payout_runs_table_in_schema(tmp_path):
    auth = _load_auth(tmp_path)
    assert _columns(auth, "payout_runs") == {
        "id", "period", "trigger", "ran_at", "n_payouts", "total_cents"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_payouts.py -v -k "payout_run"`
Expected: FAIL — `payout_runs` table / functions missing.

- [ ] **Step 3: Add the `payout_runs` table to `SCHEMA`**

In `website/auth.py`'s `SCHEMA` string, after the `payouts` table + its index, add:
```sql
-- payout_runs: one row per executed monthly payout batch. Audit log, and the
-- de-dup signal for the automated scheduler (it skips a period already run).
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

- [ ] **Step 4: Add the helper functions**

Add to `website/auth.py` near `run_payouts` / `list_payouts`:
```python
def record_payout_run(period: str, trigger: str,
                      n_payouts: int, total_cents: int) -> int:
    """Log an executed payout batch. `trigger` is 'cron' or 'admin'."""
    with _connect() as db:
        cur = db.execute(
            "INSERT INTO payout_runs (period, trigger, n_payouts, total_cents) "
            "VALUES (?, ?, ?, ?)",
            (period, trigger, int(n_payouts), int(total_cents)),
        )
        return cur.lastrowid


def payout_run_exists(period: str) -> bool:
    """True if any payout batch (cron or admin) has run for `period`."""
    with _connect() as db:
        return db.execute(
            "SELECT 1 FROM payout_runs WHERE period = ? LIMIT 1", (period,)
        ).fetchone() is not None


def list_payout_runs(limit: int = 12) -> list[dict]:
    """Recent payout batches, newest first."""
    with _connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM payout_runs ORDER BY ran_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()]
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests/test_payouts.py -v -k "payout_run"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): payout_runs audit table + helpers"
```

---

## Task 3: `referral_clicks` table + `record_referral_click`

**Files:**
- Modify: `website/auth.py` (`SCHEMA` string; add a function + a day helper)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payouts.py`:
```python
def test_referral_clicks_table_in_schema(tmp_path):
    auth = _load_auth(tmp_path)
    assert _columns(auth, "referral_clicks") == {"creator_id", "day", "clicks"}


def test_record_referral_click_counts_and_upserts(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    assert auth.record_referral_click(code) is True
    assert auth.record_referral_click(code) is True
    with auth._connect() as db:
        rows = db.execute(
            "SELECT day, clicks FROM referral_clicks WHERE creator_id = ?",
            (creator["id"],)).fetchall()
    assert len(rows) == 1            # both hits same day -> one row
    assert rows[0]["clicks"] == 2


def test_record_referral_click_unknown_code_is_noop(tmp_path):
    auth = _load_auth(tmp_path)
    assert auth.record_referral_click("NOTACODE") is False
    assert auth.record_referral_click("") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_payouts.py -v -k "referral_click"`
Expected: FAIL — table / function missing.

- [ ] **Step 3: Add the `referral_clicks` table to `SCHEMA`**

In `website/auth.py`'s `SCHEMA` string, after the `payout_runs` table, add:
```sql
-- referral_clicks: one row per creator per day, counting hits on /r/<code>.
CREATE TABLE IF NOT EXISTS referral_clicks (
  creator_id INTEGER NOT NULL,
  day        TEXT NOT NULL,                  -- 'YYYY-MM-DD'
  clicks     INTEGER NOT NULL DEFAULT 0,
  UNIQUE (creator_id, day),
  FOREIGN KEY (creator_id) REFERENCES creators(id) ON DELETE CASCADE
);
```

- [ ] **Step 4: Add a day helper and `record_referral_click`**

In `website/auth.py`, just after `_current_period()`, add:
```python
def _current_day() -> str:
    """Today's date — "YYYY-MM-DD" (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
```
Then near the creator helpers add:
```python
def record_referral_click(code: str) -> bool:
    """Count one click on a creator's referral link. No-op for an unknown or
    inactive code. Returns True iff a click was counted."""
    creator = get_creator_by_code(code)   # active creators only
    if creator is None:
        return False
    with _connect() as db:
        db.execute(
            "INSERT INTO referral_clicks (creator_id, day, clicks) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(creator_id, day) DO UPDATE SET clicks = clicks + 1",
            (creator["id"], _current_day()),
        )
    return True
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests/test_payouts.py -v -k "referral_click"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): referral_clicks table + record_referral_click"
```

---

## Task 4: `creator_analytics`

**Files:**
- Modify: `website/auth.py` (add a function)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_payouts.py`:
```python
def test_creator_analytics_funnel_and_series(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    code = creator["referral_code"]
    auth.record_referral_click(code)
    auth.record_referral_click(code)
    auth.record_referral_click(code)
    auth.record_referral_click(code)        # 4 clicks today
    u1 = auth.create_user("a1@example.com", "pw123456", referred_by=code)
    u2 = auth.create_user("a2@example.com", "pw123456", referred_by=code)
    auth.update_subscription(u1, "s1", "active", None)
    a = auth.creator_analytics(auth.get_creator_by_code(code))
    assert a["total_clicks"] == 4
    assert a["funnel"]["clicks"] == 4
    assert a["funnel"]["signups"] == 2
    assert a["funnel"]["subscribers"] == 1
    assert a["funnel"]["click_to_signup_pct"] == 50.0
    assert a["funnel"]["signup_to_subscriber_pct"] == 50.0
    assert len(a["series"]) == 30
    today = a["series"][-1]
    assert today["clicks"] == 4
    assert today["signups"] == 2
    assert all(set(d.keys()) == {"day", "clicks", "signups"} for d in a["series"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_payouts.py -v -k analytics`
Expected: FAIL — `creator_analytics` not defined.

- [ ] **Step 3: Implement `creator_analytics`**

Add to `website/auth.py` after `creator_earnings`:
```python
def creator_analytics(creator) -> dict:
    """Referral funnel + a 30-day clicks/signups series for the dashboard."""
    code = creator["referral_code"]
    cid = creator["id"]
    with _connect() as db:
        total_clicks = db.execute(
            "SELECT COALESCE(SUM(clicks), 0) AS n FROM referral_clicks "
            "WHERE creator_id = ?", (cid,)
        ).fetchone()["n"]
        signups = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE referred_by = ?", (code,)
        ).fetchone()["n"]
        subscribers = db.execute(
            "SELECT COUNT(*) AS n FROM users "
            "WHERE referred_by = ? AND subscription_status = 'active'", (code,)
        ).fetchone()["n"]
        clicks_by_day = {r["day"]: r["clicks"] for r in db.execute(
            "SELECT day, clicks FROM referral_clicks WHERE creator_id = ?",
            (cid,)
        ).fetchall()}
        signups_by_day = {r["day"]: r["n"] for r in db.execute(
            "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n "
            "FROM users WHERE referred_by = ? GROUP BY day", (code,)
        ).fetchall()}
    today = datetime.now(timezone.utc).date()
    series = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        series.append({
            "day": d,
            "clicks": clicks_by_day.get(d, 0),
            "signups": signups_by_day.get(d, 0),
        })
    return {
        "total_clicks": total_clicks,
        "funnel": {
            "clicks": total_clicks,
            "signups": signups,
            "subscribers": subscribers,
            "click_to_signup_pct": round(100.0 * signups / total_clicks, 1)
                                   if total_clicks else 0.0,
            "signup_to_subscriber_pct": round(100.0 * subscribers / signups, 1)
                                        if signups else 0.0,
        },
        "series": series,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_payouts.py -v -k analytics`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): creator_analytics — referral funnel + 30-day series"
```

---

## Task 5: `commission_count_for_referred`, `get_creator`, payout-result emails

**Files:**
- Modify: `website/auth.py` (add two functions; extend `run_payouts` result dicts)
- Test: `tests/test_payouts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_payouts.py`:
```python
def test_commission_count_for_referred(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    uid = auth.create_user("cc@example.com", "pw123456")
    assert auth.commission_count_for_referred(creator["id"], uid) == 0
    auth.record_commission(creator["id"], uid, 200, "usd", "inv_1")
    assert auth.commission_count_for_referred(creator["id"], uid) == 1
    auth.record_commission(creator["id"], uid, 200, "usd", "inv_2")
    assert auth.commission_count_for_referred(creator["id"], uid) == 2


def test_get_creator_by_id(tmp_path):
    auth = _load_auth(tmp_path)
    creator = auth.approve_creator_application(_apply(auth))
    got = auth.get_creator(creator["id"])
    assert got is not None and got["referral_code"] == creator["referral_code"]
    assert auth.get_creator(999999) is None


def test_run_payouts_results_carry_creator_contact(tmp_path):
    auth = _load_auth(tmp_path)
    creator = _seed_creator_with_balance(auth, 3000, "rc@example.com")
    results = auth.run_payouts(2500, "2026-05")
    assert len(results) == 1
    assert results[0]["creator_email"] == "rc@example.com"
    assert "creator_name" in results[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_payouts.py -v -k "commission_count or get_creator_by_id or creator_contact"`
Expected: FAIL.

- [ ] **Step 3: Add `commission_count_for_referred` and `get_creator`**

Add to `website/auth.py` near the other creator/commission helpers:
```python
def commission_count_for_referred(creator_id: int,
                                  referred_user_id: int) -> int:
    """How many commissions a creator has earned from one referred user.
    A return of 1 means the just-recorded commission was that user's first."""
    with _connect() as db:
        return db.execute(
            "SELECT COUNT(*) AS n FROM commissions "
            "WHERE creator_id = ? AND referred_user_id = ?",
            (creator_id, referred_user_id),
        ).fetchone()["n"]


def get_creator(creator_id: int):
    """A creator row by id, or None."""
    with _connect() as db:
        return db.execute(
            "SELECT * FROM creators WHERE id = ?", (creator_id,)
        ).fetchone()
```

- [ ] **Step 4: Add creator contact to `run_payouts` result dicts**

In `run_payouts`, find the `results.append({...})` call. Add two keys so callers can email the creator without `auth.py` importing `emailer`:
```python
        results.append({
            "id": payout_id, "creator_id": creator["id"],
            "amount_cents": total, "method": method, "status": status,
            "stripe_transfer_id": transfer_id,
            "creator_email": creator["email"],
            "creator_name": creator["name"],
        })
```
(`creator` is the row in scope in that loop.)

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests/test_payouts.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add website/auth.py tests/test_payouts.py
git commit -m "feat(creators): commission_count_for_referred, get_creator, payout creator contact"
```

---

## Task 6: Notification emails in `emailer.py`

**Files:**
- Modify: `website/emailer.py` (add three functions)

No unit-test harness for SMTP; verify with a Python syntax check and by calling the functions with SMTP unconfigured (they must return `False`, not raise).

- [ ] **Step 1: Add the three notification functions**

Append to `website/emailer.py`, after `notify_creator_application`:
```python
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
```

- [ ] **Step 2: Verify**

Run:
```bash
python3 -c "import importlib.util, pathlib; \
spec=importlib.util.spec_from_file_location('em', pathlib.Path('website/emailer.py')); \
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); \
print(m.notify_creator_new_subscriber('x@y.com','Sam',200)); \
print(m.notify_creator_payout('x@y.com','Sam',2500,'manual')); \
print(m.notify_creator_monthly_summary('x@y.com','Sam',{'this_month_cents':600,'active_subscribers':3,'commission_cents':200,'pending_balance_cents':600}))"
```
Expected: three lines, each `False` (SMTP unconfigured in this environment) — no exceptions.

- [ ] **Step 3: Commit**

```bash
git add website/emailer.py
git commit -m "feat(creators): creator notification emails — new subscriber, payout, monthly summary"
```

---

## Task 7: Webhook tier rate + new-subscriber email; `/r/` click tracking

**Files:**
- Modify: `website/server.py` (`_credit_referral_commission` ~line 564; `/r/{code}` route ~line 1961)

Server endpoints aren't covered by the test harness — verify with a syntax check and review.

- [ ] **Step 1: Apply the tiered rate + new-subscriber email in `_credit_referral_commission`**

In `website/server.py`, in `_credit_referral_commission`, replace the commission computation and the `record_commission` block. Find:
```python
        # Flat $2 per verified payment, capped at the amount actually
        # collected so a discounted/zero payment never overpays.
        commission = min(auth.CREATOR_COMMISSION_CENTS, amount_cents)
        if commission <= 0:
            return
        if auth.record_commission(
            creator_id=creator["id"], referred_user_id=user_id,
            amount_cents=commission, currency=currency or "usd",
            stripe_ref=stripe_ref,
        ):
            print(f"[creator] credited code {creator['referral_code']} "
                  f"{commission}¢ for invoice {stripe_ref} (user {user_id})",
                  file=sys.stderr, flush=True)
```
Replace with:
```python
        # Tiered commission — the creator's current rate (rises with their
        # active-subscriber count), capped at the amount actually collected so
        # a discounted/zero payment never overpays.
        commission = min(auth.creator_commission_cents(creator), amount_cents)
        if commission <= 0:
            return
        if auth.record_commission(
            creator_id=creator["id"], referred_user_id=user_id,
            amount_cents=commission, currency=currency or "usd",
            stripe_ref=stripe_ref,
        ):
            print(f"[creator] credited code {creator['referral_code']} "
                  f"{commission}¢ for invoice {stripe_ref} (user {user_id})",
                  file=sys.stderr, flush=True)
            # First commission ever from this referred user → tell the
            # creator. Renewals don't re-notify (count would be > 1).
            if auth.commission_count_for_referred(creator["id"], user_id) == 1:
                emailer.notify_creator_new_subscriber(
                    creator["email"], creator["name"], commission)
```

- [ ] **Step 2: Add click tracking to the `/r/{code}` route**

In the `referral_link` route, find:
```python
    if auth.referral_code_is_valid(safe):
        resp.set_cookie(
            REFERRAL_COOKIE, safe,
            max_age=_REFERRAL_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
    return resp
```
Replace with:
```python
    if auth.referral_code_is_valid(safe):
        resp.set_cookie(
            REFERRAL_COOKIE, safe,
            max_age=_REFERRAL_COOKIE_MAX_AGE, httponly=True, samesite="lax",
        )
        # Count the click for the creator's analytics — best-effort, never
        # let a bookkeeping failure break the redirect.
        try:
            auth.record_referral_click(safe)
        except Exception as e:  # noqa: BLE001
            print(f"[referral] click count failed: {e}",
                  file=sys.stderr, flush=True)
    return resp
```

- [ ] **Step 3: Verify**

Run: `python3 -c "import ast; ast.parse(open('website/server.py').read()); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add website/server.py
git commit -m "feat(creators): tiered commission + new-subscriber email; /r/ click tracking"
```

---

## Task 8: Automated payout scheduler

**Files:**
- Modify: `website/server.py` (constants near line 73; a new `_run_monthly_payouts` helper + `_payout_scheduler` task + startup hook; rewire `admin_run_payouts` ~line 929; `admin_mark_payout_paid`)

- [ ] **Step 1: Add the two config constants**

In `website/server.py`, near `PAYOUT_MINIMUM_CENTS` / `STRIPE_CONNECT_ENABLED`, add:
```python
# Automated payouts. The in-process scheduler runs the monthly batch on/after
# PAYOUT_DAY if it hasn't run that month. Set BART_PAYOUT_CRON=0 to disable
# (e.g. when running multiple web instances and only one should schedule).
PAYOUT_DAY = max(1, min(28, int(os.environ.get("BART_PAYOUT_DAY", "1"))))
PAYOUT_CRON_ENABLED = os.environ.get("BART_PAYOUT_CRON", "1") == "1"
```

- [ ] **Step 2: Add the shared run helper and the scheduler**

In `website/server.py`, after `_stripe_transfer` (around line 611), add:
```python
def _run_monthly_payouts(period: str, trigger: str) -> dict:
    """Run the payout batch for `period`, record the run, and email creators
    (payout notices + a monthly summary to every creator). Shared by the cron
    and the admin endpoint. `trigger` is 'cron' or 'admin'."""
    transfer = _stripe_transfer if STRIPE_CONNECT_ENABLED else None
    results = auth.run_payouts(PAYOUT_MINIMUM_CENTS, period=period,
                               transfer_fn=transfer)
    total = sum(r["amount_cents"] for r in results)
    auth.record_payout_run(period, trigger, len(results), total)
    # Payout-sent notices for transfers that actually completed.
    for r in results:
        if r["status"] == "paid":
            emailer.notify_creator_payout(
                r.get("creator_email") or "", r.get("creator_name") or "",
                r["amount_cents"], r["method"])
    # Monthly summary to every creator.
    paid_creator_ids = {r["creator_id"] for r in results}
    for c in auth.list_creators():
        e = c.get("earnings") or {}
        emailer.notify_creator_monthly_summary(
            c.get("email") or "", c.get("name") or "", {
                "this_month_cents": e.get("this_month_cents", 0),
                "active_subscribers": e.get("active_subscribers", 0),
                "commission_cents": e.get("commission_cents", 0),
                "pending_balance_cents": e.get("pending_balance_cents", 0),
            })
    return {"results": results, "total_cents": total}


async def _payout_scheduler() -> None:
    """Background loop: once a day past PAYOUT_DAY, run the monthly payout
    batch if it hasn't run yet this month. Failures only log."""
    while True:
        try:
            now = datetime.now()
            period = now.strftime("%Y-%m")
            if now.day >= PAYOUT_DAY and not auth.payout_run_exists(period):
                print(f"[payout-cron] running batch for {period}",
                      file=sys.stderr, flush=True)
                out = _run_monthly_payouts(period, "cron")
                print(f"[payout-cron] {len(out['results'])} payouts, "
                      f"{out['total_cents']}¢", file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[payout-cron] error: {e}", file=sys.stderr, flush=True)
        await asyncio.sleep(6 * 3600)


@app.on_event("startup")
async def _start_payout_scheduler() -> None:
    if PAYOUT_CRON_ENABLED:
        asyncio.create_task(_payout_scheduler())
        print("[payout-cron] scheduler started", file=sys.stderr, flush=True)
```
Confirm `from datetime import datetime` is importable in `server.py` — if `datetime` is not already imported at module level, add `from datetime import datetime` to the imports. (Check with `grep -n "import datetime\|from datetime" website/server.py`; if absent, add it.)

- [ ] **Step 3: Rewire `admin_run_payouts` to use the shared helper**

Replace the body of `admin_run_payouts` (after `_require_admin(user)` and the Stripe-key check) so it routes through `_run_monthly_payouts`:
```python
@app.post("/api/admin/payouts/run")
async def admin_run_payouts(user=Depends(auth.current_user)):
    """Run the monthly payout batch — pays every creator at or above the
    minimum balance. Stripe Connect creators are transferred automatically;
    everyone else gets a pending payout for the manual queue. Records the run
    so the automated scheduler skips this month."""
    _require_admin(user)
    if STRIPE_CONNECT_ENABLED and not STRIPE_SECRET_KEY:
        raise HTTPException(409, "stripe connect is enabled but "
                                 "STRIPE_SECRET_KEY is not configured.")
    out = _run_monthly_payouts(auth._current_period(), "admin")
    return {"ok": True, "payouts": out["results"]}
```

- [ ] **Step 4: Send a payout email from `admin_mark_payout_paid`**

In `admin_mark_payout_paid`, after the successful `auth.mark_payout_paid(...)` check and before `return {"ok": True}`, add:
```python
    # Notify the creator their payout was sent — best-effort.
    try:
        p = next((x for x in auth.list_payouts() if x["id"] == payout_id), None)
        if p is not None:
            creator = auth.get_creator(p["creator_id"])
            if creator is not None:
                emailer.notify_creator_payout(
                    creator["email"], creator["name"],
                    p["amount_cents"], p["method"])
    except Exception as e:  # noqa: BLE001
        print(f"[payout] mark-paid email failed: {e}",
              file=sys.stderr, flush=True)
```

- [ ] **Step 5: Verify**

Run: `python3 -c "import ast; ast.parse(open('website/server.py').read()); print('ok')"`
Expected: `ok`.
Run: `python3 -m pytest -q` — expect all tests still pass (the auth-layer changes are covered; server.py changes don't affect tests).

- [ ] **Step 6: Commit**

```bash
git add website/server.py
git commit -m "feat(creators): automated monthly payout scheduler + payout/summary emails"
```

---

## Task 9: Analytics + payout-runs API endpoints

**Files:**
- Modify: `website/server.py` (add two endpoints)

- [ ] **Step 1: Add `GET /api/creator/analytics`**

In `website/server.py`, after the `creator_connect_refresh` endpoint (the last `/api/creator/*` route), add:
```python
@app.get("/api/creator/analytics")
async def creator_analytics(user=Depends(auth.current_user)):
    """Referral funnel + 30-day clicks/signups series for the signed-in
    creator's dashboard."""
    creator = auth.get_creator_for_user(user)
    if creator is None:
        raise HTTPException(403, "you're not a bart creator.")
    return auth.creator_analytics(creator)
```

- [ ] **Step 2: Add `GET /api/admin/payout-runs`**

After the `admin_mark_payout_paid` endpoint, add:
```python
@app.get("/api/admin/payout-runs")
async def admin_payout_runs(user=Depends(auth.current_user)):
    """Recent automated/manual payout batches — admin only."""
    _require_admin(user)
    return {"runs": auth.list_payout_runs()}
```

- [ ] **Step 3: Verify**

Run: `python3 -c "import ast; ast.parse(open('website/server.py').read()); print('ok')"`
Expected: `ok`.
Run: `grep -n "/api/creator/analytics\|/api/admin/payout-runs" website/server.py` — confirm both routes present exactly once.

- [ ] **Step 4: Commit**

```bash
git add website/server.py
git commit -m "feat(creators): GET /api/creator/analytics + GET /api/admin/payout-runs"
```

---

## Task 10: Creator dashboard — analytics section + tier display

**Files:**
- Modify: `website/creators.html`

Static HTML — verify by reading and an HTML well-formedness check.

- [ ] **Step 1: Read the file**

Read `website/creators.html` — the approved-creator dashboard (`renderDashboard` / the `is_creator` branch), its CSS classes/tokens, the `cents()` and `esc()` helpers, and the `loadCreatorData` fetch. Note where the "Performance" and "How it works" sections render.

- [ ] **Step 2: Add a tier display to the dashboard**

In the dashboard render code, add a "Your tier" line near the Performance section, driven by the `earnings` object the page already has (`/api/creator/me` → `earnings`):
- Current rate: `cents(e.commission_cents)` per subscriber / month.
- If `e.next_tier` is not null: a line "`N` more active subscribers to `$X.XX`/sub" where `N = e.next_tier.at - e.active_subscribers` and `$X.XX = cents(e.next_tier.cents)`.
- If `e.next_tier` is null: "You're on the top tier."

Use the page's existing card/stat classes. Update the "How it works" copy to mention that the rate rises with active subscribers.

- [ ] **Step 3: Add a Referral analytics section**

Add a new dashboard section "Referral analytics". When the dashboard loads for a creator, also `fetch('/api/creator/analytics')` and render:
- **Funnel:** three numbers — Clicks (`funnel.clicks`), Signups (`funnel.signups`), Subscribers (`funnel.subscribers`) — with the two conversion percentages between them (`funnel.click_to_signup_pct` + "%", `funnel.signup_to_subscriber_pct` + "%").
- **30-day chart:** a simple bar chart of `series` (30 entries, each `{day, clicks, signups}`) — render with plain CSS-height `<div>` bars or an inline `<svg>`, NO chart library. Two series per day (clicks and signups), or two small stacked rows. Scale bar heights to the max value in the series. Label a few axis dates (first / mid / last). Keep it consistent with the page's visual style.
- All server strings inserted via `innerHTML` must go through the existing `esc()` helper. Numbers from the API need no escaping. Handle the empty case (all zeros) gracefully — the chart should still render flat bars, not crash.

- [ ] **Step 4: Verify**

Re-read the rendered code; confirm the JSON keys match (`earnings.commission_cents`, `earnings.next_tier`, `analytics.funnel.*`, `analytics.series[]`). Check HTML well-formedness:
```bash
python3 -c "from html.parser import HTMLParser; \
p=HTMLParser(); p.feed(open('website/creators.html').read()); print('html ok')"
```
Expected: `html ok`.

- [ ] **Step 5: Commit**

```bash
git add website/creators.html
git commit -m "feat(creators): dashboard tier display + referral analytics section"
```

---

## Task 11: Admin panel — last payout run

**Files:**
- Modify: `website/admin-creators.html`

- [ ] **Step 1: Read the file**

Read `website/admin-creators.html` — the payouts panel IIFE, its fetch pattern, `esc()`/`fmt()` helpers, and the "Run monthly payouts" button area.

- [ ] **Step 2: Add a last-run line to the payouts panel**

In the payouts panel, after fetching the payouts list, also `fetch('/api/admin/payout-runs')` and render a line near the "Run monthly payouts" button summarizing the most recent run from `runs[0]`: e.g. "Last run: `period` · `trigger` · `n_payouts` payouts · $`total` · `ran_at` (date)". If `runs` is empty, show "No automated runs yet." Escape server strings via the existing `esc()` helper; format money with the existing `fmt()`.

- [ ] **Step 3: Verify**

```bash
python3 -c "from html.parser import HTMLParser; \
p=HTMLParser(); p.feed(open('website/admin-creators.html').read()); print('html ok')"
```
Expected: `html ok`.

- [ ] **Step 4: Commit**

```bash
git add website/admin-creators.html
git commit -m "feat(admin): show last automated/manual payout run in the payouts panel"
```

---

## Task 12: Full suite, docs, finish

**Files:**
- Modify: `DEPLOY.md`

- [ ] **Step 1: Document the new env vars**

In `DEPLOY.md`, near the existing `BART_*` env-var docs, add lines for:
- `BART_PAYOUT_DAY` — day of the month (1-28) on/after which the automated payout batch runs; default `1`.
- `BART_PAYOUT_CRON` — set to `0` to disable the in-process payout scheduler (e.g. when running multiple web instances); default `1` = enabled.

- [ ] **Step 2: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all tests pass — the prior 197 plus the new tests in `tests/test_payouts.py`. Fix anything that fails before continuing.

- [ ] **Step 3: Commit the docs**

```bash
git add DEPLOY.md
git commit -m "docs: document BART_PAYOUT_DAY and BART_PAYOUT_CRON"
```

- [ ] **Step 4: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill: merge `feat/creator-program-depth` into `main`, then push `main` to the `bartcopy` remote (`https://github.com/loctran0323/bart-copy`) to deploy — the same remote the previous creator work was deployed to.

---

## Self-review notes

- **Spec coverage:** tiered commission (T1, wired in T7), `payout_runs` + scheduler (T2, T8), referral analytics (T3 clicks, T4 analytics, T9 endpoint, T10 UI), notifications (T6 functions, T7 new-subscriber, T8 payout + summary), admin last-run (T9 endpoint, T11 UI), env vars (T12). All spec sections map to tasks.
- **Out-of-scope** items (per-subscriber identity, bot filtering, multi-instance leader election, in-app notifications, env-var tiers) are intentionally not tasked.
- **Type consistency:** `commission_cents_for(active)` / `next_tier_for(active)` / `creator_commission_cents(creator)` signatures are consistent across T1 (definition), T1 tests, and T7 (call site). `creator_earnings` keys `commission_cents` + `next_tier` (T1) are consumed in T10. `run_payouts` result dict keys `creator_email` + `creator_name` (T5) are consumed in T8. `creator_analytics` shape (`total_clicks`, `funnel{...}`, `series[]`) (T4) is consumed in T9/T10. `record_payout_run`/`payout_run_exists`/`list_payout_runs` (T2) are consumed in T8/T9. `get_creator(id)` (T5) consumed in T8. The three `emailer` function signatures (T6) match their call sites in T7/T8.
