"""DistillerAgent — compress the full corpus to a structured ~10-15K-char brief.

Why this exists:

The original architecture sent the entire 100K+ char corpus to every agent
call. With prompt caching that's affordable; without (subscription mode),
it's the dominant cost in both tokens and latency.

The Distiller runs ONCE up-front using the fast model (Haiku). It produces
a corpus brief that downstream agents (Planner, Author, Critic) use instead
of the raw corpus. The brief includes a 'Verbatim anchors' section that
preserves exact notation, definitions, and problem stems so the brief is
not just a summary but also a quotable reference.

Researcher still sees the full corpus per-day for fresh excerpts.

Net effect: ~70% fewer input tokens across the run vs. raw-corpus calls,
while keeping verbatim source material available downstream.
"""
from __future__ import annotations

from .base import Agent


class DistillerAgent(Agent):
    name = "distiller"
    prompt_file = "distiller.md"

    def distill(self) -> str:
        """Single call. Returns a structured markdown corpus brief, ≤2500 words."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                "Produce a corpus brief, ≤2500 words, with exactly these sections:\n\n"
                "## Materials inventory — one bullet per file (filename + one-line description).\n\n"
                "## Topic outline — chapters in course-sequence order. Format strictly:\n"
                "  ### Ch N: Title\n"
                "  - subtopic\n"
                "  - subtopic\n"
                "Include every subtopic the corpus actually covers — do not cap. "
                "The downstream planner parses these headings.\n\n"
                "## Notation conventions — 3-6 bullets on distinctive notation.\n\n"
                "## Past-exam structure — 3-5 bullets describing any sample/practice exam in the corpus, "
                "or 'No past-exam material in corpus.'\n\n"
                "## Problems & examples index — 5-10 bullets naming the most distinctive problems "
                "using the corpus's own naming convention.\n\n"
                "## Verbatim anchors — 8–15 bullets quoting EXACT strings from the corpus: "
                "notation tokens, key definitions, and distinctive example-problem stems. "
                "Use the corpus's exact wording. Format strictly:\n"
                "  > \"<verbatim>\" — file.pdf\n\n"
                "Skip 'Gaps' unless something major is missing."
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=6000,
            label="distiller",
            temperature=0.2,
        )
