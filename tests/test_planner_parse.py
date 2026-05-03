"""Test the planner's JSON-extraction fallback."""
from __future__ import annotations

from bart.agents.planner import PlannerAgent


def test_extract_json_basic():
    text = """# Master plan
some text here

```json
{"days": [{"day": 1, "topic": "intro"}]}
```
"""
    out = PlannerAgent._extract_json(text)
    assert out == {"days": [{"day": 1, "topic": "intro"}]}


def test_extract_json_missing():
    out = PlannerAgent._extract_json("no json here")
    assert out == {"days": []}


def test_extract_json_malformed():
    text = "```json\n{not valid}\n```"
    out = PlannerAgent._extract_json(text)
    assert out == {"days": []}
