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
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}.\n\n"
                f"Topic to research: {topic}\n"
                f"Learning objectives:\n- " + "\n- ".join(learning_objectives) + "\n\n"
                f"TASK\n"
                f"Produce a CONDENSED research brief (~600-1200 words) covering this topic, drawn STRICTLY from the corpus. "
                f"Include:\n"
                f"1. Verbatim definitions / theorems from the materials (in quote blocks).\n"
                f"2. Worked examples mentioned in the materials, if present.\n"
                f"3. References to specific exam problems or homework problems in the corpus that drill this topic — "
                f"cite them using whatever naming convention the corpus uses (problem numbers, sections, slide references, etc.).\n"
                f"4. A short list of textbook-specific terminology / notation conventions to preserve.\n"
                f"5. Gaps: anything an exam-prep author would need that the corpus does NOT cover.\n\n"
                f"Be precise. Do NOT invent material that isn't in the corpus."
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=4000,
            label=f"researcher:{topic[:40]}",
            temperature=0.2,
        )
