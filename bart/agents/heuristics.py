"""Cheap heuristics that decide whether an artifact warrants a (paid) review.

The reviewer call costs ~1/3 of an author call. If the author output is
clearly healthy by simple measurable criteria, we skip the reviewer entirely.
This typically saves ~30-50% of review calls in practice.

The heuristic is conservative — it errs toward running the reviewer. Only
artifacts that look unambiguously fine bypass review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class HealthCheck:
    healthy: bool
    score: int           # 0-100, our cheap heuristic
    reasons: list[str]   # human-readable

    def __bool__(self) -> bool:
        return self.healthy


# Per-artifact-kind length floor (chars). Below this, output is suspicious.
_MIN_LENGTH = {
    "daily_lesson":      6_000,
    "schematics":        3_000,
    "whimsical_notes":   3_000,
    "short_study_guide": 1_500,
    "practice_exam":     5_000,
}


def health_check(artifact_kind: str, text: str) -> HealthCheck:
    """Score the artifact on cheap structural metrics."""
    reasons: list[str] = []
    score = 100

    # 1. Length floor
    floor = _MIN_LENGTH.get(artifact_kind, 2_000)
    if len(text) < floor:
        score -= 30
        reasons.append(f"length {len(text)} below floor {floor}")

    # 2. Headings present (markdown ##)
    n_headings = len(re.findall(r"^#{1,4}\s", text, re.MULTILINE))
    if n_headings < 2:
        score -= 20
        reasons.append(f"only {n_headings} headings")

    # 3. Quick Check or details collapsibles (interactivity indicator)
    has_details = "<details" in text or "Quick Check" in text or "✏️" in text
    if artifact_kind == "daily_lesson" and not has_details:
        score -= 25
        reasons.append("no Quick Check / details blocks in a daily lesson")

    # 4. AI-tell preamble survival check (sanitizer didn't catch it)
    first_300 = text[:300].lower()
    if any(s in first_300 for s in ["sure, here", "of course!", "i've created", "here's your"]):
        score -= 30
        reasons.append("AI preamble pattern detected in opening")

    # 5. Excessive trailing apology / coda
    last_400 = text[-400:].lower()
    if any(s in last_400 for s in ["i hope this helps", "let me know if", "feel free to"]):
        score -= 15
        reasons.append("AI coda pattern detected near end")

    # 6. Code fence balance — every ``` should be paired
    n_fences = text.count("```")
    if n_fences % 2 != 0:
        score -= 30
        reasons.append(f"unbalanced code fences ({n_fences} total — odd count)")

    # 7. Math delimiter balance (after sanitizer would have run)
    n_open_inline = text.count("\\(")
    n_close_inline = text.count("\\)")
    if n_open_inline != n_close_inline:
        score -= 20
        reasons.append(f"unbalanced inline math: \\( {n_open_inline} vs \\) {n_close_inline}")

    n_open_block = text.count("\\[")
    n_close_block = text.count("\\]")
    if n_open_block != n_close_block:
        score -= 20
        reasons.append(f"unbalanced block math: \\[ {n_open_block} vs \\] {n_close_block}")

    # 8. Bare AI artifacts that should have been stripped
    if "as an AI" in text.lower() or "i'm an AI" in text.lower():
        score -= 40
        reasons.append("bare AI self-reference left in output")

    # Threshold: skip the reviewer if score >= 90.
    healthy = score >= 90 and len(reasons) == 0
    return HealthCheck(healthy=healthy, score=score, reasons=reasons)
