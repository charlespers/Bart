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
        # Critic does NOT need full corpus — just the artifact + brief + lightweight context
        user = [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}. Level: {cfg.student_level}.\n"
                f"Artifact kind: {artifact_kind}\n\n"
                f"ORIGINAL BRIEF:\n{brief}\n\n"
                f"ARTIFACT TO REVIEW:\n---\n{artifact_text[:60000]}\n---\n\n"
                f"TASK\n"
                f"Critique against an Ivy-undergraduate exam-prep rubric. Output JSON, fenced as ```json … ```:\n"
                f"```json\n"
                f"{{\n"
                f"  \"score\": <0-100>,\n"
                f"  \"strengths\": [\"…\"],\n"
                f"  \"issues\": [\"…\"],\n"
                f"  \"must_fix\": [\"specific, actionable revision instructions\"]\n"
                f"}}\n"
                f"```\n"
                f"RUBRIC (out of 100):\n"
                f"- Mathematical correctness (25): formulas, derivations, units\n"
                f"- Source-grounding (20): cites the actual corpus, no fabrication\n"
                f"- Pedagogical depth (20): goes beyond stating facts; explains WHY\n"
                f"- Practice density (15): worked examples + drill problems are concrete and useful\n"
                f"- Interactivity (10): Quick-Check boxes / collapsibles are present and useful\n"
                f"- Polish (10): clean markdown, consistent notation, well-organized\n\n"
                f"Threshold: artifacts scoring < 80 SHOULD be revised. Be strict but specific — "
                f"vague feedback ('add more examples') is useless; demand exact additions naming the topic, "
                f"the specific concept, and the form of the missing piece (e.g. 'Add a worked example "
                f"of <topic mechanic from the corpus> showing each step', 'Replace the loose definition "
                f"of <term> on §X with the verbatim definition from the materials.')."
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
