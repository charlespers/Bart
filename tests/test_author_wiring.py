"""Tests for AuthorAgent's research_slice / exam_patterns_block plumbing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bart.agents.author import AuthorAgent
from bart.agents.base import AgentContext


class _FakeLLM:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return "x" * 2000


@dataclass
class _FakeCfg:
    subject: str = "Subject"
    student_level: str = "undergraduate"
    style: str = "academic-rigorous"
    daily_hours: float = 3.0
    guidance: str = "guidance"
    primary_model: str = "claude-opus-4-7"
    daily_model: str = "claude-sonnet-4-6"
    fast_model: str = "claude-haiku-4-5-20251001"


def _ctx() -> tuple[AgentContext, _FakeLLM]:
    llm = _FakeLLM()
    ctx = AgentContext(cfg=_FakeCfg(), llm=llm, corpus_block=[])
    return ctx, llm


def _user_text(call: dict[str, Any]) -> str:
    return "\n".join(b["text"] for b in call["user"] if b.get("type") == "text")


def test_author_omits_research_slice_when_empty(monkeypatch):
    monkeypatch.setenv("BART_SKIP_BLOCK_FIX", "1")
    ctx, llm = _ctx()
    a = AuthorAgent(ctx)
    a.write("daily_lesson", "BRIEF TEXT")
    assert llm.calls, "Author did not call llm.complete"
    text = _user_text(llm.calls[0])
    assert "RESEARCH SLICE" not in text
    assert "EXAM PATTERNS" not in text


def test_author_appends_research_slice_when_provided(monkeypatch):
    monkeypatch.setenv("BART_SKIP_BLOCK_FIX", "1")
    ctx, llm = _ctx()
    a = AuthorAgent(ctx)
    a.write(
        "daily_lesson",
        "BRIEF TEXT",
        research_slice="VERBATIM CORPUS EXCERPT",
    )
    text = _user_text(llm.calls[0])
    assert "RESEARCH SLICE" in text
    assert "VERBATIM CORPUS EXCERPT" in text
    assert text.index("BRIEF TEXT") < text.index("VERBATIM CORPUS EXCERPT")


def test_author_appends_exam_patterns_block_when_provided(monkeypatch):
    monkeypatch.setenv("BART_SKIP_BLOCK_FIX", "1")
    ctx, llm = _ctx()
    a = AuthorAgent(ctx)
    a.write(
        "practice_exam",
        "BRIEF TEXT",
        exam_patterns_block="STEM A FROM 2024",
    )
    text = _user_text(llm.calls[0])
    assert "EXAM PATTERNS" in text
    assert "STEM A FROM 2024" in text


def test_author_appends_both_when_both_provided(monkeypatch):
    monkeypatch.setenv("BART_SKIP_BLOCK_FIX", "1")
    ctx, llm = _ctx()
    a = AuthorAgent(ctx)
    a.write(
        "daily_lesson",
        "BRIEF TEXT",
        research_slice="RS",
        exam_patterns_block="EP",
    )
    text = _user_text(llm.calls[0])
    assert text.index("BRIEF TEXT") < text.index("RS") < text.index("EP")
