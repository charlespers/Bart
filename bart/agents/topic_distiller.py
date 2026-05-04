"""TopicDistillerAgent — produces a per-day "study card" in one Haiku call.

The original architecture had a Researcher fetch from the corpus on every
daily-lesson generation. With 36 days, that's 36 corpus reads of 100K+ chars.

This agent collapses all of that into ONE Haiku call that emits a JSON map
of {day_num: study_card_text}. Then daily Authors get the per-day study
card pre-baked, never touch the full corpus, and run dramatically faster.

Net: replaces 36 Sonnet corpus reads with 1 Haiku call. ~40% time reduction
on subscription mode.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import Agent


class TopicDistillerAgent(Agent):
    name = "topic_distiller"
    prompt_file = "topic_distiller.md"

    def distill_per_day(self, day_entries: list[dict[str, Any]]) -> dict[int, str]:
        """Single call. Returns {day_num: study_card_markdown}.

        The corpus brief is in self.ctx.corpus_block already; we add the
        per-day topic list. Output is a JSON object.
        """
        cfg = self.ctx.cfg
        # Compact representation of the day list.
        day_summaries = [
            {
                "day": d["day"],
                "topic": d.get("topic", ""),
                "chapters": d.get("chapters", []),
                "focus": d.get("focus", "learn"),
            }
            for d in day_entries
        ]
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                f"DAY LIST:\n{json.dumps(day_summaries, indent=1)}\n\n"
                f"Produce a JSON object mapping each day number to a study card "
                f"(150-300 words of markdown). Output ONLY a fenced ```json block "
                f"with escaped \\n inside strings:\n"
                f"```json\n"
                f"{{ \"1\": \"# Day 1 study card\\n\\n…\", \"2\": \"…\" }}\n"
                f"```\n"
                f"If a topic isn't in the corpus, write "
                f"'Topic not directly in corpus; consult standard references.' and move on."
            ),
        }]
        text = self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=12000,  # 36 days * ~300 words = ~10800 tokens budget
            label="topic_distiller",
            temperature=0.2,
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> dict[int, str]:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not m:
            return {}
        try:
            raw = json.loads(m.group(1))
        except json.JSONDecodeError:
            return {}
        out: dict[int, str] = {}
        for k, v in raw.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        return out
