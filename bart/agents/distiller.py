"""DistillerAgent — compress the full corpus to a tight ~5-10K-char brief.

Why this exists:

The original architecture sent the entire 100K+ char corpus to every agent
call. With prompt caching that's affordable; without (subscription mode),
it's the dominant cost in both tokens and latency.

The Distiller runs ONCE up-front using the fast model (Haiku). It produces
a compact "corpus brief" that downstream agents (Planner, Author, Critic)
use instead of the raw corpus. The Researcher is the only agent that still
sees the full corpus, and only on a per-topic basis.

Net effect: ~80% fewer input tokens across the run. ~3-5x faster runtime
in subscription mode.
"""
from __future__ import annotations

from .base import Agent


class DistillerAgent(Agent):
    name = "distiller"
    prompt_file = "distiller.md"

    def distill(self) -> str:
        """Single call. Returns a TIGHT markdown corpus brief, ≤800 words."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                "Produce a corpus brief, ≤800 words, with exactly these sections:\n\n"
                "## Materials inventory — one bullet per file (filename + one-line description).\n\n"
                "## Topic outline — chapters in course-sequence order. Format strictly:\n"
                "  ### Ch N: Title\n"
                "  - subtopic\n"
                "  - subtopic\n"
                "Cap subtopics at 5 per chapter. The downstream planner parses these headings.\n\n"
                "## Notation conventions — 3-6 bullets on distinctive notation.\n\n"
                "## Past-exam structure — 3-5 bullets describing any sample/practice exam in the corpus, "
                "or 'No past-exam material in corpus.'\n\n"
                "## Problems & examples index — 5-10 bullets naming the most distinctive problems "
                "using the corpus's own naming convention.\n\n"
                "Skip 'Gaps' unless something major is missing."
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=2000,  # halved — tight brief
            label="distiller",
            temperature=0.2,
        )
