"""WhimsyIndexerAgent — parse the whimsy artifact into per-topic hooks.

After the whimsy artifact is generated, this agent extracts one short hook
per topic so daily lessons can drop in the matching one rather than asking
the Author to invent fresh whimsy each day (which leads to drift and
duplicate analogies).
"""
from __future__ import annotations

import json
import re

from .base import Agent


class WhimsyIndexerAgent(Agent):
    name = "whimsy_indexer"
    prompt_file = "whimsy_indexer.md"

    def index(self, whimsy_text: str) -> dict[str, str]:
        cfg = self.ctx.cfg
        user = [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                f"WHIMSY ARTIFACT\n---\n{whimsy_text[:60000]}\n---"
            ),
        }]
        text = self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=3000,
            label="whimsy_indexer",
            temperature=0.2,
            response_format="json",
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> dict[str, str]:
        from .base import extract_json
        data = extract_json(text, expect="object")
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}


def lookup(index: dict[str, str], topic: str) -> str:
    """Best-match lookup — exact, then case-insensitive substring."""
    if not topic or not index:
        return ""
    if topic in index:
        return index[topic]
    needle = topic.lower()
    for k, v in index.items():
        kl = k.lower()
        if needle in kl or kl in needle:
            return v
    return ""
