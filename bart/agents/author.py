"""AuthorAgent — generates a long-form artifact from a brief."""
from __future__ import annotations

from typing import Any

from .base import Agent


class AuthorAgent(Agent):
    name = "author"
    prompt_file = "author.md"

    def write(
        self,
        artifact_kind: str,
        brief: str,
        max_tokens: int = 16000,
        temperature: float = 0.7,
        label_suffix: str = "",
    ) -> str:
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"ARTIFACT KIND: {artifact_kind}\n"
                f"Subject: {cfg.subject}. Level: {cfg.student_level}. Style: {cfg.style}.\n"
                f"Daily hours available to the student: {cfg.daily_hours}.\n"
                f"User guidance:\n{cfg.guidance}\n\n"
                f"BRIEF\n{brief}\n\n"
                f"OUTPUT\n"
                f"Produce the artifact as a single, long-form markdown document. "
                f"Adhere strictly to the structural requirements in the brief. "
                f"Use LaTeX for math ($...$ inline, $$...$$ display). "
                f"When you cite the corpus, use exact phrasing from the materials. "
                f"Do not invent past exam problems and present them as the user's actual exams — "
                f"label generated practice problems as 'Practice' or 'Drill'."
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.primary_model,
            system=self.system_prompt,
            user=user,
            max_tokens=max_tokens,
            label=f"author:{artifact_kind}{':' + label_suffix if label_suffix else ''}",
            temperature=temperature,
        )
