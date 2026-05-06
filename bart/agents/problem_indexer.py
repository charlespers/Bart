"""ProblemIndexerAgent — extract a structured problem index from the corpus once.

Daily lessons later get the relevant problem subset by tag, instead of the
model re-discovering corpus problems each day.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import Agent


class ProblemIndexerAgent(Agent):
    name = "problem_indexer"
    prompt_file = "problem_indexer.md"

    def index(self) -> list[dict[str, Any]]:
        """One call against the full corpus. Returns the parsed problem list."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": f"Subject: {cfg.subject}",
        }]
        text = self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=4000,
            label="problem_indexer",
            temperature=0.1,
            response_format="json",
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[dict[str, Any]]:
        from .base import extract_json
        data = extract_json(text, expect="array")
        if data is None:
            return []
        out: list[dict[str, Any]] = []
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("id")
            statement = entry.get("statement", "")
            topics = entry.get("topics", [])
            if not pid:
                continue
            out.append({
                "id": str(pid),
                "statement": str(statement),
                "topics": [str(t) for t in topics if t] if isinstance(topics, list) else [],
            })
        return out


def filter_for_topics(index: list[dict[str, Any]], topics: list[str]) -> list[dict[str, Any]]:
    """Return entries whose topic tags overlap (case-insensitive substring match) the given topics."""
    if not topics:
        return []
    needles = [t.lower().strip() for t in topics if t]
    out: list[dict[str, Any]] = []
    for entry in index:
        tags = [t.lower() for t in entry.get("topics", [])]
        if any(any(n in tag or tag in n for tag in tags) for n in needles):
            out.append(entry)
    return out


def format_problem_block(entries: list[dict[str, Any]]) -> str:
    """Render filtered entries as a markdown block."""
    if not entries:
        return "(no specific corpus problems indexed for this day's topic)"
    return "\n".join(f"- **{e['id']}** — {e['statement']}" for e in entries)
