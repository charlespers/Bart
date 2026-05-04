"""CriticAgent — grades an artifact and produces actionable revision notes."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .base import Agent


@dataclass
class CritiqueResult:
    score: int  # 0-100
    strengths: list[str]
    issues: list[str]
    must_fix: list[str]
    should_revise: bool


class CriticAgent(Agent):
    name = "critic"
    prompt_file = "critic.md"

    def critique(self, artifact_kind: str, artifact_text: str, brief: str) -> CritiqueResult:
        cfg = self.ctx.cfg
        # Critic does NOT need full corpus — just the artifact + brief.
        user = [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject} · Level: {cfg.student_level} · Artifact: {artifact_kind}\n\n"
                f"BRIEF\n{brief}\n\n"
                f"ARTIFACT\n---\n{artifact_text[:60000]}\n---\n\n"
                f"RUBRIC (sum to 100): correctness 25 · source-grounding 20 · pedagogical depth 20 · "
                f"practice density 15 · interactivity 10 · polish 10\n\n"
                f"OUTPUT JSON only:\n"
                f"```json\n"
                f"{{\"score\": <0-100>, \"strengths\": [\"…\"], \"issues\": [\"…\"], \"must_fix\": [\"…\"]}}\n"
                f"```"
            ),
        }]
        text = self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=2500,
            label=f"critic:{artifact_kind}",
            temperature=0.2,
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> CritiqueResult:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not m:
            return CritiqueResult(score=85, strengths=[], issues=[], must_fix=[], should_revise=False)
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return CritiqueResult(score=85, strengths=[], issues=[], must_fix=[], should_revise=False)
        score = int(data.get("score", 85))
        return CritiqueResult(
            score=score,
            strengths=list(data.get("strengths", [])),
            issues=list(data.get("issues", [])),
            must_fix=list(data.get("must_fix", [])),
            should_revise=score < 80 or bool(data.get("must_fix")),
        )
