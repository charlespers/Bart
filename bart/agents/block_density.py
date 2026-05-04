"""Block-density validator — measures how well an Author output uses the
design library, and computes a targeted correction prompt when it doesn't.

The skeleton mandates specific block usage per artifact. Without a check,
the model can drift back to vanilla markdown. This module:

  1. Counts every `bart-<name>` fence in the output.
  2. Compares against per-artifact thresholds.
  3. Emits a *specific* correction prompt naming what's missing — so the
     retry call is short, targeted, and cheap.

Net effect: one cheap continuation upgrades a minimum-effort lesson into
a fully-instrumented one. Replaces the previous "length floor" continuation
with a *quality floor* continuation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..render.block_expand import BLOCK_NAMES


# Per-artifact-kind minimum block usage. These reflect what the skeleton
# asks for, with a small slack so a slightly-light-but-good lesson passes.
# Format: {artifact_kind: {block_name: minimum_count}}
THRESHOLDS: dict[str, dict[str, int]] = {
    "daily_lesson": {
        "why-it-matters":   1,
        "concept-build":    2,   # the teaching-arc requirement (threshold concepts)
        "formula-card":     2,
        "trap-callout":     2,   # one of which should be a misconception (kind=trap)
        "quick-check":      4,   # pre-quiz cold-start + post-section quizzes
        "worked-example":   2,
        "checkpoint":       1,
    },
    "schematics": {
        "formula-card":      3,
        "comparison-matrix": 1,
        "trap-callout":      3,
    },
    "whimsical_notes": {
        "mnemonic-card":    3,
    },
    "short_study_guide": {
        "trap-callout":     3,
        "quick-check":      3,
        "checkpoint":       1,
    },
    "practice_exam_part_a": {
        # Part A grades on diversity — we want a mix of interactive types.
        "multi-step":         1,
        "multiple-choice":    1,
    },
}

# Fast regex for counting fences without parsing JSON.
_FENCE_RE = re.compile(r"(?ms)^```bart-([a-z0-9-]+)[^\n]*\n.*?```\s*$")


@dataclass
class DensityReport:
    artifact_kind: str
    counts: dict[str, int]                  # all blocks seen
    missing: dict[str, int]                 # block -> deficit (>0 means need more)
    healthy: bool
    total_blocks: int
    score: float                            # 0-100 quality score

    def correction_prompt(self) -> str:
        """Return a focused prompt asking the model to add the missing blocks.

        Empty string when no correction is needed.
        """
        if not self.missing:
            return ""
        lines = [
            "QUALITY GATE — your previous output is missing required design blocks.",
            "Emit ONLY the missing blocks below as a continuation; do NOT rewrite "
            "the existing content. Each block goes in its natural section of the "
            "skeleton you already followed.",
            "",
            "MISSING BLOCKS:",
        ]
        for name, deficit in sorted(self.missing.items()):
            existing = self.counts.get(name, 0)
            lines.append(
                f"- `bart-{name}`: have {existing}, need at least "
                f"{existing + deficit} (add {deficit} more)"
            )
        lines.append("")
        lines.append(
            "Output: ONLY the additional fenced blocks, in the order they "
            "should appear in the document. No prose, no headings, no "
            "preamble — just the new fences."
        )
        return "\n".join(lines)


def count_blocks(text: str) -> dict[str, int]:
    """Cheap regex count of every bart-<name> fence."""
    counts: dict[str, int] = {}
    for m in _FENCE_RE.finditer(text):
        name = m.group(1)
        counts[name] = counts.get(name, 0) + 1
    return counts


def evaluate(artifact_kind: str, text: str) -> DensityReport:
    """Score `text` against the threshold for `artifact_kind`.

    Score formula: weight each missing block by 100 / (sum of thresholds).
    Healthy = no missing blocks. The threshold table is a *floor*, not a
    target — agents are free to emit more.
    """
    counts = count_blocks(text)
    thresholds = THRESHOLDS.get(artifact_kind, {})
    missing: dict[str, int] = {}
    for name, need in thresholds.items():
        have = counts.get(name, 0)
        if have < need:
            missing[name] = need - have

    total_required = sum(thresholds.values()) or 1
    deficit = sum(missing.values())
    score = max(0.0, 100.0 * (1 - deficit / total_required))
    total_blocks = sum(counts.values())

    return DensityReport(
        artifact_kind=artifact_kind,
        counts=counts,
        missing=missing,
        healthy=not missing,
        total_blocks=total_blocks,
        score=score,
    )


def thresholds_summary(artifact_kind: str) -> str:
    """Human-readable summary used in the brief. Tells the model what we'll grade on."""
    t = THRESHOLDS.get(artifact_kind, {})
    if not t:
        return ""
    parts = [f"`bart-{k}` ≥ {v}" for k, v in sorted(t.items())]
    return "QUALITY GATE — minimum block usage for this artifact: " + ", ".join(parts) + "."
