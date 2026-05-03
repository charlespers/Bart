"""ReviewerAgent — fused critic + reviser in a single call.

Why fuse: the critic-reviser two-step issues two API calls per artifact even
when the artifact is fine. By combining into one call that returns *either*
"PASS" (artifact ships as-is) *or* a full revised artifact, we save:
- One full API call when the artifact passes (the common case).
- The corpus retransmission for the revision step.

The reviewer also does NOT need the corpus — it has the artifact and the
brief, which is sufficient. This drops input tokens by ~50% per review.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .base import Agent


@dataclass
class ReviewResult:
    passed: bool         # True -> ship original, False -> use 'revised_text'
    score: int           # 0-100
    must_fix: list[str]  # captured for telemetry / debugging
    revised_text: str    # only meaningful if passed=False; else ""


class ReviewerAgent(Agent):
    """Single-call critic+reviser. Skips the corpus entirely."""

    name = "reviewer"
    prompt_file = "reviewer.md"

    def review(self, artifact_kind: str, artifact_text: str, brief: str, max_tokens: int = 16000) -> ReviewResult:
        cfg = self.ctx.cfg
        # Corpus-free user message — saves ~25K input tokens per call vs author.
        user = [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}.  Level: {cfg.student_level}.  Style: {cfg.style}.\n"
                f"Artifact kind: {artifact_kind}\n\n"
                f"BRIEF:\n{brief}\n\n"
                f"ARTIFACT:\n---\n{artifact_text}\n---\n\n"
                f"REVIEW PROTOCOL\n"
                f"Score the artifact 0–100 against this rubric:\n"
                f"  • mathematical correctness (25): formulas, signs, units, derivations\n"
                f"  • source-grounding (20): cites the actual brief content, no fabrication\n"
                f"  • pedagogical depth (20): explains WHY, not just WHAT\n"
                f"  • practice density (15): worked examples + drill problems are concrete\n"
                f"  • interactivity (10): Quick Check blocks present and useful\n"
                f"  • polish (10): clean markdown, consistent notation, well-organized\n\n"
                f"Then decide:\n"
                f"  - score >= 80 AND no critical issues  →  PASS (ship original)\n"
                f"  - score < 80 OR critical issues       →  REVISE (rewrite the full artifact)\n\n"
                f"OUTPUT FORMAT (this is strict)\n"
                f"Begin with a single line of JSON, fenced as ```json … ```, like:\n"
                f"```json\n"
                f"{{\"score\": <int>, \"verdict\": \"PASS\" | \"REVISE\", \"must_fix\": [\"…\", \"…\"]}}\n"
                f"```\n"
                f"Then:\n"
                f"  - if verdict == PASS, stop. Output nothing else.\n"
                f"  - if verdict == REVISE, emit the full revised markdown artifact below the JSON,\n"
                f"    starting with a line `===REVISED===` and continuing to the end. The revision\n"
                f"    must address every must_fix item, preserve everything that was already good,\n"
                f"    and use the same structural sections / headings as the original."
            ),
        }]
        text = self.ctx.llm.complete(
            model=cfg.primary_model,
            system=self.system_prompt,
            user=user,
            max_tokens=max_tokens,
            label=f"reviewer:{artifact_kind}",
            temperature=0.4,
        )
        return self._parse(text, original=artifact_text)

    @staticmethod
    def _parse(text: str, original: str) -> ReviewResult:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not m:
            return ReviewResult(passed=True, score=85, must_fix=[], revised_text="")
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return ReviewResult(passed=True, score=85, must_fix=[], revised_text="")

        score = int(data.get("score", 85))
        verdict = str(data.get("verdict", "PASS")).upper()
        must_fix = list(data.get("must_fix", []))

        if verdict == "PASS":
            return ReviewResult(passed=True, score=score, must_fix=must_fix, revised_text="")

        # REVISE path — extract revised text after the ===REVISED=== marker.
        rev_match = re.search(r"===REVISED===\s*\n(.*)$", text, re.DOTALL)
        if rev_match:
            revised = rev_match.group(1).strip()
            if len(revised) >= 200:  # sanity floor — revisions shorter than this are suspect
                return ReviewResult(passed=False, score=score, must_fix=must_fix, revised_text=revised)
        # Couldn't extract usable revision — fall back to original.
        return ReviewResult(passed=True, score=score, must_fix=must_fix, revised_text="")
