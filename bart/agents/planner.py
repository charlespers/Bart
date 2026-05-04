"""PlannerAgent — produces the master plan and a structured per-day topic list."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from .base import Agent


class PlannerAgent(Agent):
    name = "planner"
    prompt_file = "planner.md"

    def plan(self, days: int, today_iso: str, corpus_summary: str) -> tuple[str, list[dict[str, Any]]]:
        """Returns (markdown_master_plan, structured_day_list).

        Uses the COMPACT corpus brief (passed via ctx.corpus_block) instead of
        the raw corpus. The brief is ~5-10K chars vs the corpus's 100K+,
        which dramatically reduces input tokens and runtime per call.
        """
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Today: {today_iso}. Exam: {cfg.exam_date} ({days} days from today).\n"
                f"Subject: {cfg.subject}. Level: {cfg.student_level}. "
                f"Daily hours available: {cfg.daily_hours}. Style: {cfg.style}.\n\n"
                f"User guidance:\n{cfg.guidance}\n\n"
                f"Materials index:\n{corpus_summary}\n\n"
                f"TASK\n"
                f"Produce a master plan. Be CONCISE — markdown is for orientation, not depth.\n\n"
                f"PART 1 — markdown master plan (header `# Master Study Plan`).\n"
                f"Sections, each 2-4 paragraphs maximum:\n"
                f"  (a) Exam scope (what the materials suggest the exam covers)\n"
                f"  (b) Topic weights (rough percentages)\n"
                f"  (c) Pacing strategy (1-2 paragraphs)\n"
                f"  (d) Cheat-sheet build plan\n\n"
                f"PART 2 — JSON block, fenced as ```json … ```, with one entry per day:\n"
                f"```json\n"
                f"{{\n"
                f"  \"days\": [\n"
                f"    {{\n"
                f"      \"day\": 1,\n"
                f"      \"topic\": \"<short topic name, ~5 words>\",\n"
                f"      \"chapters\": [\"<exact chapter/section refs from the brief>\"],\n"
                f"      \"focus\": \"learn|practice|review|mock-exam\",\n"
                f"      \"learning_objectives\": [\"<3-5 short bullets>\"],\n"
                f"      \"key_problems\": [\"<problem refs from the brief>\"]\n"
                f"    }}\n"
                f"  ]\n"
                f"}}\n"
                f"```\n"
                f"Cover days 1 through {days}. Final day(s) should be mock-exam + light review.\n"
                f"Bart computes dates and hours fields — omit those from the JSON."
            ),
        }]

        text = self.ctx.llm.complete(
            model=cfg.primary_model,
            system=self.system_prompt,
            user=user,
            max_tokens=4000,
            label="planner",
            temperature=0.3,
        )

        days_struct = self._extract_json(text)
        # Ensure dates are present and walk forward correctly
        today = date.fromisoformat(today_iso)
        for entry in days_struct.get("days", []):
            d = entry.get("day", 0)
            if d:
                entry["date"] = (today + timedelta(days=d - 1)).isoformat()
        return text, days_struct.get("days", [])

    @staticmethod
    def _extract_json(text: str) -> dict:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not m:
            return {"days": []}
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return {"days": []}
