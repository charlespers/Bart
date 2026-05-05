"""Tests for ExamPatternAgent parsing + helper formatters."""
from __future__ import annotations

import json

from bart.agents.exam_pattern import (
    ExamPatternAgent,
    format_for_practice_exam,
    format_for_daily_drill,
    EMPTY_PATTERNS,
)


def test_parse_well_formed_json():
    raw = (
        "Here is the result:\n\n"
        "```json\n"
        + json.dumps({
            "problems": [
                {
                    "stem": "Compute the molar mass of glucose.",
                    "source_file": "exam_2024.pdf",
                    "type": "computation",
                    "points": 10,
                    "difficulty": "low",
                    "topics": ["stoichiometry"],
                }
            ],
            "structure": {"total_points": 100, "section_breakdown": [], "common_types": ["computation"]},
            "style_notes": ["Multiple-choice rare; show-work expected."],
        })
        + "\n```\n"
    )
    parsed = ExamPatternAgent._parse(raw)
    assert len(parsed["problems"]) == 1
    assert parsed["problems"][0]["stem"].startswith("Compute the molar mass")
    assert parsed["structure"]["total_points"] == 100
    assert parsed["style_notes"][0].startswith("Multiple-choice")


def test_parse_malformed_json_returns_empty_shape():
    parsed = ExamPatternAgent._parse("not even close to JSON")
    assert parsed == EMPTY_PATTERNS


def test_parse_missing_keys_returns_empty_shape():
    raw = "```json\n{\"problems\": []}\n```"
    parsed = ExamPatternAgent._parse(raw)
    assert parsed == EMPTY_PATTERNS


def test_format_for_practice_exam_renders_structure_and_stems():
    patterns = {
        "problems": [
            {"stem": "Stem A", "source_file": "exam_2024.pdf", "type": "mechanism",
             "points": 10, "difficulty": "medium", "topics": ["t1"]},
            {"stem": "Stem B", "source_file": "exam_2024.pdf", "type": "computation",
             "points": 5, "difficulty": "low", "topics": ["t2"]},
        ],
        "structure": {"total_points": 100, "section_breakdown": [], "common_types": ["mechanism"]},
        "style_notes": ["Show work."],
    }
    out = format_for_practice_exam(patterns)
    assert "Stem A" in out
    assert "Stem B" in out
    assert "mechanism" in out
    assert "Show work" in out


def test_format_for_practice_exam_empty_patterns_is_safe():
    out = format_for_practice_exam(EMPTY_PATTERNS)
    assert "no past-exam patterns" in out.lower() or out.strip() == ""


def test_format_for_daily_drill_filters_by_topic():
    patterns = {
        "problems": [
            {"stem": "About t1", "source_file": "x.pdf", "type": "x", "points": None,
             "difficulty": "low", "topics": ["t1"]},
            {"stem": "About t2", "source_file": "x.pdf", "type": "x", "points": None,
             "difficulty": "low", "topics": ["t2"]},
        ],
        "structure": {}, "style_notes": [],
    }
    out = format_for_daily_drill(patterns, ["t1"])
    assert "About t1" in out
    assert "About t2" not in out


def test_format_for_daily_drill_empty_topics_returns_empty_block():
    patterns = {
        "problems": [
            {"stem": "any", "source_file": "x.pdf", "type": "x", "points": None,
             "difficulty": "low", "topics": ["t1"]}
        ],
        "structure": {}, "style_notes": [],
    }
    out = format_for_daily_drill(patterns, [])
    assert out.strip() == "" or "no past-exam" in out.lower()
