"""ExamPatternAgent — extract a structured past-exam pattern sidecar.

The Practice Exam Author and per-day drill sections use this sidecar to
pattern problems on the user's REAL past exams (style, difficulty,
phrasing, point allocation) rather than emit AI-generic problems.

Runs once up-front, in parallel with the notation/problem-index sidecars.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import Agent


EMPTY_PATTERNS: dict[str, Any] = {"problems": [], "structure": {}, "style_notes": []}

_REQUIRED_KEYS = ("problems", "structure", "style_notes")


class ExamPatternAgent(Agent):
    name = "exam_pattern"
    prompt_file = "exam_pattern.md"

    def extract(self) -> dict[str, Any]:
        """One Haiku call against the full corpus. Returns parsed dict or EMPTY_PATTERNS."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": f"Subject: {cfg.subject}",
        }]
        text = self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=4000,
            label="exam_pattern",
            temperature=0.1,
            response_format="json",
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        from .base import extract_json
        data = extract_json(text, expect="object")
        if not isinstance(data, dict):
            return dict(EMPTY_PATTERNS)
        if not all(k in data for k in _REQUIRED_KEYS):
            return dict(EMPTY_PATTERNS)
        problems = data.get("problems", [])
        if not isinstance(problems, list):
            problems = []
        structure = data.get("structure", {})
        if not isinstance(structure, dict):
            structure = {}
        style_notes = data.get("style_notes", [])
        if not isinstance(style_notes, list):
            style_notes = []
        return {
            "problems": [_coerce_problem(p) for p in problems if isinstance(p, dict)][:40],
            "structure": structure,
            "style_notes": [str(s) for s in style_notes],
        }


def _coerce_problem(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": str(p.get("stem", "")),
        "source_file": str(p.get("source_file", "")),
        "type": str(p.get("type", "")),
        "points": p.get("points") if isinstance(p.get("points"), int) else None,
        "difficulty": str(p.get("difficulty", "")),
        "topics": [str(t) for t in p.get("topics", []) if t] if isinstance(p.get("topics"), list) else [],
    }


def format_for_practice_exam(patterns: dict[str, Any]) -> str:
    """Markdown block for the practice-exam Author brief."""
    problems = patterns.get("problems") or []
    structure = patterns.get("structure") or {}
    style_notes = patterns.get("style_notes") or []
    if not problems and not structure and not style_notes:
        return "(no past-exam patterns indexed)"
    out: list[str] = []
    if structure:
        total = structure.get("total_points")
        breakdown = structure.get("section_breakdown") or []
        common = structure.get("common_types") or []
        out.append("**Structure (from real past exams):**")
        if total is not None:
            out.append(f"- total points: {total}")
        for sec in breakdown:
            out.append(
                f"- {sec.get('section', '?')}: "
                f"{sec.get('count', '?')} problem(s), {sec.get('points', '?')} pts"
            )
        if common:
            out.append(f"- common types: {', '.join(common)}")
        out.append("")
    if style_notes:
        out.append("**Style notes:**")
        for s in style_notes:
            out.append(f"- {s}")
        out.append("")
    if problems:
        out.append("**Sample stems (verbatim — pattern your problems on these):**")
        for p in problems[:15]:
            stem = p.get("stem", "").strip().replace("\n", " ")
            src = p.get("source_file", "?")
            type_ = p.get("type", "?")
            out.append(f"- ({type_}) \"{stem}\" — {src}")
    return "\n".join(out)


def format_for_daily_drill(patterns: dict[str, Any], topics: list[str]) -> str:
    """Markdown block for a daily lesson's drill section, filtered by topic."""
    problems = patterns.get("problems") or []
    if not problems or not topics:
        return ""
    needles = [t.lower().strip() for t in topics if t]
    matched: list[dict[str, Any]] = []
    for p in problems:
        tags = [t.lower() for t in p.get("topics", [])]
        if any(any(n in tag or tag in n for tag in tags) for n in needles):
            matched.append(p)
    if not matched:
        return ""
    out = ["**Past-exam stems on today's topics (verbatim — pattern drill problems on these):**"]
    for p in matched[:6]:
        stem = p.get("stem", "").strip().replace("\n", " ")
        src = p.get("source_file", "?")
        out.append(f"- \"{stem}\" — {src}")
    return "\n".join(out)
