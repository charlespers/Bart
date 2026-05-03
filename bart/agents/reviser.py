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
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"ARTIFACT KIND: {artifact_kind}\n"
                f"Subject: {cfg.subject}.\n\n"
                f"ORIGINAL BRIEF:\n{brief}\n\n"
                f"CRITIC SCORE: {critique.score}/100\n"
                f"MUST-FIX ITEMS:\n- " + "\n- ".join(critique.must_fix) + "\n\n"
                f"OTHER ISSUES:\n- " + "\n- ".join(critique.issues) + "\n\n"
                f"ORIGINAL ARTIFACT:\n---\n{original[:80000]}\n---\n\n"
                f"TASK\n"
                f"Produce a REVISED version of the artifact. Address EVERY must-fix item explicitly. "
                f"Preserve the strengths of the original. Do not regress on passages that were already good. "
                f"Output the full revised artifact in markdown, no preamble, no commentary about your changes."
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
