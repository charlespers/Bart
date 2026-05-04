"""NotationExtractorAgent — produce a per-corpus notation card once.

The card gets injected into every Author call so notation stays consistent
across the entire packet without each agent re-deriving it.
"""
from __future__ import annotations

from .base import Agent


class NotationExtractorAgent(Agent):
    name = "notation_extractor"
    prompt_file = "notation_extractor.md"

    def extract(self, corpus_brief: str) -> str:
        """One Haiku call. Returns ~200-token markdown card."""
        cfg = self.ctx.cfg
        user = [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                f"CORPUS BRIEF\n{corpus_brief}"
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=600,
            label="notation_extractor",
            temperature=0.1,
        )
