"""Maintain `outreach/strategy.md` — a living engagement playbook.

The file mixes three things every time it is rewritten:
  1. A static brand stance lifted from `GROWTH-KIT.md`.
  2. A rolling window of the most-recent queue items (drafts + published).
  3. If Graph API credentials are configured, performance metrics for the
     last published Reels and a recommended next-week posting plan.

`generate` calls `update_strategy()` after producing each draft, so the
playbook always reflects the freshest data without a separate command.
Manual refresh is also available via `outreach strategy`.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .config import OutreachConfig
from .insights import MediaInsight, gather_insights
from .paths import PKG_DIR, QUEUE, REPO_ROOT
from .review import Status, list_items
from .script import ReelScript

STRATEGY_PATH = PKG_DIR / "strategy.md"

# How many recent items to surface in the rolling-window sections.
_RECENT_WINDOW = 7

# Stable hashtag pool we cycle through. The script generator picks 5-8 per
# Reel; this list is the brand-approved superset.
_HASHTAG_POOL = [
    "studytips", "examprep", "studygram", "studyhacks", "collegestudent",
    "studymotivation", "studypacket", "lawschool", "premed", "mcatprep",
    "barexam", "lsatprep", "mathstudy", "stemstudent", "studywithme",
    "finalsweek", "studytok", "academicvalidation", "studyplanner",
]


@dataclass
class StrategyContext:
    """Materialised state used to build the file."""
    queue: List[Status]
    recent_scripts: List[tuple[str, ReelScript]]    # (date, script)
    insights: List[MediaInsight]
    insights_error: Optional[str]


def _load_recent_scripts() -> List[tuple[str, ReelScript]]:
    out: List[tuple[str, ReelScript]] = []
    if not QUEUE.exists():
        return out
    for d in sorted(QUEUE.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        script_path = d / "script.json"
        if not script_path.exists():
            continue
        try:
            data = json.loads(script_path.read_text())
            out.append((d.name, ReelScript(**data)))
        except (json.JSONDecodeError, ValueError):
            continue
        if len(out) >= _RECENT_WINDOW:
            break
    return out


def _hashtag_frequency(scripts: Iterable[tuple[str, ReelScript]]) -> Counter:
    c: Counter = Counter()
    for _, s in scripts:
        for tag in s.hashtags:
            c[tag] += 1
    return c


def _next_week_angles(recent: List[tuple[str, ReelScript]]) -> List[str]:
    """Pick 7 fresh angles that don't repeat the last week's hooks."""
    used = {s.title.lower().strip() for _, s in recent}
    bank = [
        "one-day-before-the-exam panic",
        "the 60-minute review sheet",
        "what a bart packet actually contains",
        "before/after: notes vs. a study plan",
        "why a chatbot can't replace a packet",
        "how to use past exams without spoiling them",
        "the case for mnemonics that cite your notes",
        "lecture pdfs in, schematic out",
        "exam-week energy: from highlighter to plan",
        "a packet vs. a flashcard deck",
        "what to do the night before",
        "the cost of overstudying the wrong chapter",
        "why $10/mo beats a $200 tutor for finals",
    ]
    picks: List[str] = []
    for angle in bank:
        if angle.lower() in used:
            continue
        picks.append(angle)
        if len(picks) == 7:
            break
    return picks


def _build_context(cfg: OutreachConfig) -> StrategyContext:
    queue = list_items()
    recent = _load_recent_scripts()
    insights: List[MediaInsight] = []
    insights_error: Optional[str] = None
    if cfg.meta_access_token and cfg.ig_user_id:
        try:
            insights = gather_insights(cfg)
        except Exception as e:  # noqa: BLE001 - any failure becomes a note
            insights_error = str(e)
    return StrategyContext(queue=queue, recent_scripts=recent,
                           insights=insights, insights_error=insights_error)


def _format_insights(insights: List[MediaInsight]) -> str:
    if not insights:
        return ("_no published Reels with insights yet — publish a few "
                "and the metrics table will populate here._")
    lines = ["| date | reach | plays | likes | saved | shares |",
             "|------|-------|-------|-------|-------|--------|"]
    for r in sorted(insights, key=lambda i: i.date, reverse=True):
        if r.error:
            lines.append(f"| {r.date} | error: {r.error[:40]} | | | | |")
            continue
        m = r.metrics
        lines.append(
            f"| {r.date} | {m.get('reach', '—')} | {m.get('plays', '—')} | "
            f"{m.get('likes', '—')} | {m.get('saved', '—')} | "
            f"{m.get('shares', '—')} |"
        )
    return "\n".join(lines)


def _top_hashtags(insights: List[MediaInsight], scripts: List[tuple[str, ReelScript]]) -> List[str]:
    """Rank hashtags by reach of the Reels that used them."""
    by_date = {date: s for date, s in scripts}
    scored: Counter = Counter()
    for r in insights:
        if r.error or not r.metrics.get("reach"):
            continue
        script = by_date.get(r.date)
        if not script:
            continue
        reach = r.metrics["reach"] or 0
        for tag in script.hashtags:
            scored[tag] += reach
    return [t for t, _ in scored.most_common(8)]


def _hashtag_rotation(ctx: StrategyContext) -> List[str]:
    """Build next week's hashtag rotation: top by reach + fresh from pool."""
    top = _top_hashtags(ctx.insights, ctx.recent_scripts)
    used = _hashtag_frequency(ctx.recent_scripts)
    # Backfill with the least-used pool entries until we have 12 candidates.
    fresh = sorted(_HASHTAG_POOL, key=lambda t: used.get(t, 0))
    out: List[str] = []
    for tag in top + fresh:
        if tag not in out:
            out.append(tag)
        if len(out) >= 12:
            break
    return out


def _recent_hooks_block(scripts: List[tuple[str, ReelScript]]) -> str:
    if not scripts:
        return "_no scripts yet — run `outreach generate`._"
    lines = []
    for date, s in scripts:
        lines.append(f'- **{date}** — "{s.hook}"  ({s.title})')
    return "\n".join(lines)


def _queue_state_block(queue: List[Status]) -> str:
    if not queue:
        return "_queue is empty — run `outreach generate`._"
    by_state: Counter = Counter(q.state for q in queue)
    parts = [f"{by_state.get(s, 0)} {s}"
             for s in ("draft", "approved", "published", "rejected")]
    return "**" + "  ·  ".join(parts) + "**"


def _best_posting_note(insights: List[MediaInsight]) -> str:
    scored = [i for i in insights if not i.error and i.metrics.get("reach")]
    if len(scored) < 3:
        return ("Not enough published Reels yet to commit to a posting time. "
                "Default to **7:00 PM local** on weekdays (peak student "
                "scroll window) and let the metrics table above settle.")
    best = max(scored, key=lambda i: i.metrics.get("reach", 0) or 0)
    return (f"Highest-reach Reel so far: **{best.date}** "
            f"({best.metrics.get('reach')} accounts reached). "
            "Keep posting at the same time of day and compare next week's row.")


def render_strategy(ctx: StrategyContext) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    angles = _next_week_angles(ctx.recent_scripts)
    angles_block = "\n".join(f"{i + 1}. {a}" for i, a in enumerate(angles)) or \
        "_no fresh angle bank — extend strategy.py:_next_week_angles._"
    rotation = " · ".join(f"`#{t}`" for t in _hashtag_rotation(ctx))
    insights_table = _format_insights(ctx.insights)
    insights_note = (f"\n\n_Insights API call failed: {ctx.insights_error}_"
                     if ctx.insights_error else "")

    return f"""# Bart Instagram Engagement Strategy

_Last updated: {timestamp}._
_This file is regenerated by `./run-outreach generate` (and `./run-outreach
strategy`). It is the working source of truth for what to post next and
why — edit it freely between runs; non-managed sections are preserved._

## Stance (don't drift)

- **One honest idea per Reel.** A student physically gets _x_; that's the post.
- Lead with what the packet **contains** (lessons, schematics, mnemonics,
  practice exam, 60-minute review) — never with technology.
- Banned words: _AI-powered_, _revolutionary_, _game-changer_, _unleash_,
  _supercharge_. Students smell marketing.
- Every Reel ends on the URL: **studywithbart.com**.
- No engagement automation. We touch our own account, our own content. Full
  stop. (See `GROWTH-KIT.md` § ethics.)

## Queue state right now

{_queue_state_block(ctx.queue)}

## What we just shipped (last {_RECENT_WINDOW} drafts)

{_recent_hooks_block(ctx.recent_scripts)}

## Performance — published Reels

{insights_table}{insights_note}

### Posting time

{_best_posting_note(ctx.insights)}

## Angle bank for next week

Use these as `--angle` arguments to `./run-outreach generate`:

{angles_block}

## Hashtag rotation

Top-of-pool for the coming week (ranked by reach, then backfilled with the
least-used brand pool tags):

{rotation}

## How this file gets refreshed

- `./run-outreach generate` rewrites the managed sections every morning.
- `./run-outreach strategy` rewrites them on demand.
- Anything below the `<!-- notes -->` marker is preserved across rewrites,
  so jot down ideas / experiments there.

<!-- notes -->
"""


def _preserve_notes(existing: Optional[str]) -> str:
    if not existing:
        return ""
    marker = "<!-- notes -->"
    if marker not in existing:
        return ""
    tail = existing.split(marker, 1)[1]
    return tail.lstrip("\n")


def update_strategy(cfg: OutreachConfig, *, log=print) -> Path:
    """Rewrite `outreach/strategy.md`, preserving the notes section."""
    ctx = _build_context(cfg)
    log("• refreshing strategy.md")
    existing = STRATEGY_PATH.read_text() if STRATEGY_PATH.exists() else None
    notes = _preserve_notes(existing)
    body = render_strategy(ctx)
    if notes.strip():
        body = body + notes
    STRATEGY_PATH.write_text(body)
    log(f"  wrote {STRATEGY_PATH}")
    return STRATEGY_PATH
