"""ResearcherAgent — extracts topic-relevant snippets from the corpus.

Uses the fast model so per-day grounding is cheap.
"""
from __future__ import annotations

from .base import Agent


class ResearcherAgent(Agent):
    name = "researcher"
    prompt_file = "researcher.md"

    def research(self, topic: str, learning_objectives: list[str]) -> str:
        cfg = self.ctx.cfg
        objectives = "\n- ".join(learning_objectives)
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n"
                f"Topic: {topic}\n"
                f"Objectives:\n- {objectives}"
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=6000,
            label=f"researcher:{topic[:40]}",
            temperature=0.2,
        )
