"""ResearcherAgent — extracts topic-relevant snippets from the corpus.

Uses the fast model so per-day grounding is cheap.

Carries an optional `fallback_corpus_block` for the context-too-long path:
when the full corpus overflows the fast-model window (e.g. on subscription
plans with tighter per-request limits), we retry against the brief. The
result is less rich (no fresh verbatim excerpts beyond what the brief
preserved) but the daily lesson still gets grounded text rather than
crashing the run.
"""
from __future__ import annotations

from ..backends import LLMContextTooLongError
from .base import Agent, AgentContext


class ResearcherAgent(Agent):
    name = "researcher"
    prompt_file = "researcher.md"

    def __init__(self, ctx: AgentContext, fallback_corpus_block: list[dict] | None = None):
        super().__init__(ctx)
        self._fallback_block = fallback_corpus_block

    def research(self, topic: str, learning_objectives: list[str]) -> str:
        cfg = self.ctx.cfg
        objectives = "\n- ".join(learning_objectives)
        instructions = {
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n"
                f"Topic: {topic}\n"
                f"Objectives:\n- {objectives}"
            ),
        }
        try:
            return self.ctx.llm.complete(
                model=cfg.fast_model,
                system=self.system_prompt,
                user=self.ctx.corpus_block + [instructions],
                max_tokens=6000,
                label=f"researcher:{topic[:40]}",
                temperature=0.2,
            )
        except LLMContextTooLongError:
            if not self._fallback_block:
                raise
            return self.ctx.llm.complete(
                model=cfg.fast_model,
                system=self.system_prompt,
                user=self._fallback_block + [instructions],
                max_tokens=6000,
                label=f"researcher:{topic[:40]}:brief-fallback",
                temperature=0.2,
            )
