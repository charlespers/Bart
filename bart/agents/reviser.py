"""ReviserAgent — applies critic notes to revise an artifact."""
from __future__ import annotations

from .base import Agent
from .critic import CritiqueResult


class ReviserAgent(Agent):
    name = "reviser"
    prompt_file = "reviser.md"

    def revise(
        self,
        artifact_kind: str,
        original: str,
        critique: CritiqueResult,
        brief: str,
        max_tokens: int = 16000,
    ) -> str:
        cfg = self.ctx.cfg
        must_fix = "\n- ".join(critique.must_fix)
        issues = "\n- ".join(critique.issues)
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject} · Artifact: {artifact_kind} · Critic score: {critique.score}/100\n\n"
                f"BRIEF\n{brief}\n\n"
                f"MUST-FIX\n- {must_fix}\n\n"
                f"OTHER ISSUES\n- {issues}\n\n"
                f"ORIGINAL\n---\n{original[:80000]}\n---"
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.primary_model,
            system=self.system_prompt,
            user=user,
            max_tokens=max_tokens,
            label=f"reviser:{artifact_kind}",
            temperature=0.6,
        )
