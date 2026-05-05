"""Regression tests for the distiller prompt and agent config.

The Distiller is the dominant compression stage. These tests catch
accidental rollback of the verbatim-anchors expansion.
"""
from __future__ import annotations

from pathlib import Path

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "distiller.md"
SRC = Path(__file__).resolve().parent.parent / "bart" / "agents" / "distiller.py"


def test_distiller_max_tokens_is_expanded():
    text = SRC.read_text()
    assert "max_tokens=6000" in text, "Distiller max_tokens should be 6000"


def test_distiller_prompt_mentions_verbatim_anchors():
    text = PROMPT.read_text()
    assert "Verbatim" in text or "verbatim" in text, (
        "distiller.md must call out verbatim quotation"
    )


def test_distiller_user_message_has_verbatim_anchors_section():
    text = SRC.read_text()
    assert "Verbatim anchors" in text, (
        "distiller.py user message must include the '## Verbatim anchors' section spec"
    )
    assert "≤2500 words" in text or "<=2500 words" in text, (
        "distiller.py word ceiling should be raised to 2500"
    )
    assert "Cap subtopics at 5" not in text, (
        "5-subtopic-per-chapter cap should be removed"
    )
