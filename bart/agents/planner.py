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
        """Returns (markdown_master_plan, structured_day_list)."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Today: {today_iso}. Exam: {cfg.exam_date} ({days} days from today). "
                f"Subject: {cfg.subject}. Level: {cfg.student_level}. "
                f"Daily hours available: {cfg.daily_hours}. Style: {cfg.style}.\n\n"
                f"User guidance:\n{cfg.guidance}\n\n"
                f"Corpus index (filenames + sizes):\n{corpus_summary}\n\n"
                f"TASK\n"
                f"Produce a master study plan in two parts.\n\n"
                f"PART 1 — markdown master plan (header `# Master Study Plan`)\n"
                f"Required sections: (a) Exam scope analysis grounded in the actual materials, "
                f"(b) Topic weighting estimate, (c) {days}-day calendar, (d) pacing strategy "
                f"(theory vs practice vs review), (e) cheat-sheet build plan, (f) materials inventory.\n\n"
                f"PART 2 — JSON block, fenced as ```json … ```, with one entry per day:\n"
                f"```\n"
                f"{{\n"
                f"  \"days\": [\n"
                f"    {{\n"
                f"      \"day\": 1,\n"
                f"      \"date\": \"YYYY-MM-DD\",\n"
                f"      \"topic\": \"…\",\n"
                f"      \"chapters\": [\"Ch 3\", \"Ch 4.1\"],\n"
                f"      \"focus\": \"learn|practice|review|mock-exam\",\n"
                f"      \"learning_objectives\": [\"…\", \"…\"],\n"
                f"      \"key_problems\": [\"reference to specific materials in the corpus (e.g. a past-exam problem number, a textbook example, a homework question)\"],\n"
                f"      \"hours\": <float>\n"
                f"    }}\n"
                f"  ]\n"
                f"}}\n"
                f"```\n"
                f"Cover days 1 through {days}. The final day(s) should typically be mock-exam + final review."
            ),
        }]

        text = self.ctx.llm.complete(
            model=cfg.primary_model,
            system=self.system_prompt,
            user=user,
            max_tokens=8000,
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
