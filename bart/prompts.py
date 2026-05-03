"""Prompt loader — reads from prompts/*.md so they're easy to edit & version."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .paths import PROMPTS


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    if not name:
        return _DEFAULT
    path = PROMPTS / name
    if not path.exists():
        return _DEFAULT
    return path.read_text(encoding="utf-8").strip()


_DEFAULT = (
    "You are bart — an elite study-packet author for top-tier students. "
    "Produce dense, mathematically rigorous, source-grounded, and pedagogically excellent material. "
    "Embed Quick-Check questions with collapsible answers. Use LaTeX for math. "
    "Be specific to the user's corpus; do not produce generic content."
)
