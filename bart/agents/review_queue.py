"""Deterministic spaced-review queue.

For each day N, lists prior days whose cheat-sheet candidates should be
re-surfaced today. The schedule follows a 1/3/7-day cadence (the model's
"flashback to prior days" no longer needs to be re-decided per call).

This is library-level: no LLM, no corpus-specific logic. Same policy for
every run; the only per-run input is the day list.
"""
from __future__ import annotations

from typing import Any

# Days-back offsets to re-surface. 1 = yesterday, 3 = three days ago, etc.
# Empirically: 1/3/7 covers same-week recency without overweighting old material.
_LOOKBACKS = (1, 3, 7)


def build_review_queue(day_entries: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Return {day_num: [prior_day_entry, ...]} for each day with non-empty review.

    `day_entries` is the list emitted by the deterministic planner.
    The result is purely structural — references to prior days, not their
    content. The Author (or sidecar fetcher) reads cheat-sheet candidates
    from those prior days at lesson-render time.
    """
    by_num = {d["day"]: d for d in day_entries}
    out: dict[int, list[dict[str, Any]]] = {}
    for day_num in sorted(by_num.keys()):
        prior: list[dict[str, Any]] = []
        for back in _LOOKBACKS:
            ref = day_num - back
            if ref in by_num:
                e = by_num[ref]
                prior.append({
                    "day": ref,
                    "topic": e.get("topic", ""),
                    "focus": e.get("focus", ""),
                    "lookback": back,
                })
        if prior:
            out[day_num] = prior
    return out


def format_review_block(prior: list[dict[str, Any]]) -> str:
    """Render a review queue entry as a markdown block for inclusion in a brief."""
    if not prior:
        return "(no prior days to review yet — this is an early day in the plan)"
    lines = []
    for p in prior:
        lines.append(f"- Day {p['day']:02d} ({p['lookback']}d ago): {p['topic']} [{p['focus']}]")
    return "\n".join(lines)
