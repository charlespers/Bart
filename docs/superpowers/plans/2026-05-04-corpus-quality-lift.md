# Corpus Quality Lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce information loss in bart's corpus pipeline so daily lessons cite the user's specific materials (notation, problem stems, named examples) and the practice exam patterns on real past exams — without breaking the `--fast` / `--turbo` speed presets.

**Architecture:** Five additive changes — three-slice corpus truncation, a wider Distiller brief with verbatim anchors, a new ExamPattern sidecar agent run in parallel with the existing notation/problem indexers, reactivation of the per-day Researcher with full-corpus access (alongside the existing topic-distiller study card), and Author wiring that threads research excerpts plus exam patterns into the daily-lesson and practice-exam briefs. Each change is independently disable-able through env vars consumed by the speed presets.

**Tech Stack:** Python 3.11+, Anthropic SDK via `bart.backends`, `concurrent.futures.ThreadPoolExecutor`, pytest, Rich.

**Spec:** `docs/superpowers/specs/2026-05-04-corpus-quality-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `bart/io/corpus.py` | Modify | `build_corpus`: head/tail → head + middle + tail truncation. |
| `bart/agents/distiller.py` | Modify | `max_tokens` 2000 → 6000; user-message prompt update. |
| `prompts/distiller.md` | Modify | Add "Verbatim anchors" section spec; raise word ceiling 800 → 2500; drop 5-subtopic cap. |
| `bart/agents/researcher.py` | Modify | `max_tokens` 4000 → 6000. |
| `prompts/researcher.md` | Modify | Demand verbatim excerpts. |
| `bart/agents/exam_pattern.py` | Create | New `ExamPatternAgent` + `format_for_practice_exam` + `format_for_daily_drill` helpers. |
| `prompts/exam_pattern.md` | Create | System prompt for past-exam structural extraction. |
| `bart/agents/__init__.py` | Modify | Export `ExamPatternAgent`. |
| `bart/agents/author.py` | Modify | `write()` gains `research_slice` and `exam_patterns_block` kwargs; user-message wiring. |
| `bart/orchestrator.py` | Modify | Sidecar pool 2→3 workers; per-day Researcher loop honored unconditionally; pass new kwargs to Author for daily-lesson and practice-exam; honor `BART_SKIP_RESEARCHER` / `BART_SKIP_EXAM_PATTERN`. |
| `bart/__main__.py` | Modify | `--fast` and `--turbo` set the two new skip env vars. |
| `tests/test_corpus.py` | Modify | Update existing assertions for the new three-slice marker text. |
| `tests/test_corpus_three_slice.py` | Create | New three-slice truncation tests. |
| `tests/test_exam_pattern.py` | Create | New ExamPattern parsing + helper tests. |
| `tests/test_distiller_prompt.py` | Create | Prompt-string regression test. |
| `tests/test_author_wiring.py` | Create | Author kwargs append/omit behavior. |

---

## Task 1: Three-slice truncation in `build_corpus`

**Files:**
- Modify: `bart/io/corpus.py:122-155`
- Modify: `tests/test_corpus.py`
- Test: `tests/test_corpus_three_slice.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_corpus_three_slice.py` with this exact content:

```python
"""Three-slice truncation tests for build_corpus.

The middle of long files used to be dropped entirely (head/tail only).
This file verifies the new head + middle-sample + tail strategy keeps
sentinel content from each region.
"""
from __future__ import annotations

from bart.io.corpus import build_corpus, ExtractedFile


def _make(text: str) -> ExtractedFile:
    return ExtractedFile("big.md", text, len(text))


def test_truncation_keeps_head_middle_tail_sentinels():
    # Build a body where each region contains a unique sentinel string.
    head = "HEAD_SENTINEL" + ("a" * 30_000)
    mid = ("b" * 30_000) + "MIDDLE_SENTINEL" + ("b" * 30_000)
    tail = ("c" * 30_000) + "TAIL_SENTINEL"
    body = head + mid + tail

    c = build_corpus([_make(body)], [], char_budget=60_000)

    assert "HEAD_SENTINEL" in c.body, "head slice was dropped"
    assert "MIDDLE_SENTINEL" in c.body, "middle slice was dropped"
    assert "TAIL_SENTINEL" in c.body, "tail slice was dropped"


def test_truncation_marker_format():
    body = "x" * 200_000
    c = build_corpus([_make(body)], [], char_budget=60_000)
    # Expect two truncation markers: before-middle and after-middle.
    assert c.body.count("…truncated") == 2 or c.body.count("…sample…") >= 1, (
        "expected three-slice markers, got: " + c.body[:500]
    )


def test_truncation_total_within_budget():
    body = "x" * 200_000
    c = build_corpus([_make(body)], [], char_budget=60_000)
    # 60K body cap + header + truncation markers; allow ~2K slack.
    assert c.total_chars <= 62_000


def test_short_file_is_not_truncated():
    body = "ok " * 100  # well under per_file_cap
    c = build_corpus([_make(body)], [], char_budget=60_000)
    assert "truncated" not in c.body
    assert "sample" not in c.body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_corpus_three_slice.py -v`

Expected: `test_truncation_keeps_head_middle_tail_sentinels` FAILS with `AssertionError: middle slice was dropped`. The other tests may pass or fail depending on current marker text — that's fine; we only need at least one failure to confirm the behavior gap.

- [ ] **Step 3: Implement three-slice truncation**

Replace the truncation block in `bart/io/corpus.py` (the body of `build_corpus`, currently lines 134-152). The full updated `build_corpus` body after the `if not kept` guard becomes:

```python
    parts: list[str] = []
    used = 0
    per_file_cap = max(20_000, char_budget // max(len(kept), 1))

    for ef in kept:
        header = f"\n\n=== FILE: {ef.rel_path} ===\n\n"
        body = ef.text
        if len(body) > per_file_cap:
            slice_len = per_file_cap // 3
            half_slice = slice_len // 2
            mid_center = len(body) // 2
            head_end = slice_len
            mid_start = max(head_end, mid_center - half_slice)
            mid_end = min(len(body) - slice_len, mid_start + slice_len)
            tail_start = max(mid_end, len(body) - slice_len)
            head_part = body[:head_end]
            middle_part = body[mid_start:mid_end]
            tail_part = body[tail_start:]
            dropped_before_middle = mid_start - head_end
            dropped_after_middle = tail_start - mid_end
            body = (
                head_part
                + f"\n\n[…truncated {dropped_before_middle:,} chars before middle sample…]\n\n"
                + middle_part
                + f"\n\n[…truncated {dropped_after_middle:,} chars after middle sample…]\n\n"
                + tail_part
            )
        if used + len(header) + len(body) > char_budget:
            remaining = char_budget - used - len(header)
            if remaining > 1000:
                body = body[:remaining] + "\n\n[…corpus budget exhausted…]"
                parts.append(header + body)
                used += len(header) + len(body)
            break
        parts.append(header + body)
        used += len(header) + len(body)

    body = "".join(parts)
    return Corpus(files=kept, body=body, total_chars=used, skipped_files=skipped)
```

Also update the module docstring at the top of `bart/io/corpus.py` (currently lines 1-8). Replace the line `- Files larger than the per-file cap get a head + tail slice (preserves intro & conclusion).` with:

```
- Files larger than the per-file cap get a head + middle-sample + tail slice
  (preserves intro, mid-document worked examples, and conclusion).
```

- [ ] **Step 4: Update the existing test that asserts the old marker text**

In `tests/test_corpus.py`, the test `test_build_corpus_truncates_large_file` (lines 20-24) currently asserts `assert "truncated" in c.body` — this still passes because the new marker contains "truncated". Verify it still passes; no edit needed.

- [ ] **Step 5: Run all corpus tests**

Run: `.venv/bin/python -m pytest tests/test_corpus.py tests/test_corpus_three_slice.py -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bart/io/corpus.py tests/test_corpus_three_slice.py
git commit -m "$(cat <<'EOF'
corpus: switch per-file truncation to head + middle + tail

Long files used to lose their middle entirely. The middle is where
worked examples and exam patterns live, so daily lessons could only
draw from intros and conclusions. Three-slice keeps a sample from
each region within the existing per-file cap.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Distiller prompt + max_tokens expansion

**Files:**
- Modify: `bart/agents/distiller.py:48-55`
- Modify: `prompts/distiller.md`
- Test: `tests/test_distiller_prompt.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_distiller_prompt.py`:

```python
"""Regression tests for the distiller prompt and agent config.

The Distiller is the dominant compression stage. These tests catch
accidental rollback of the verbatim-anchors expansion.
"""
from __future__ import annotations

from pathlib import Path

from bart.agents.distiller import DistillerAgent

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "distiller.md"


def test_distiller_max_tokens_is_expanded():
    # The class-level `max_tokens` for the distill() call lives in the
    # complete() invocation. We assert by inspecting the source so the
    # test does not need to construct an LLM client.
    src = Path(DistillerAgent.__module__.replace(".", "/") + ".py")
    src = Path(__file__).resolve().parent.parent / "bart" / "agents" / "distiller.py"
    text = src.read_text()
    assert "max_tokens=6000" in text, "Distiller max_tokens should be 6000"


def test_distiller_prompt_mentions_verbatim_anchors():
    text = PROMPT.read_text()
    assert "Verbatim" in text or "verbatim" in text, (
        "distiller.md must call out verbatim quotation"
    )


def test_distiller_user_message_has_verbatim_anchors_section():
    src = Path(__file__).resolve().parent.parent / "bart" / "agents" / "distiller.py"
    text = src.read_text()
    assert "Verbatim anchors" in text, (
        "distiller.py user message must include the '## Verbatim anchors' section spec"
    )
    assert "≤2500 words" in text or "<=2500 words" in text, (
        "distiller.py word ceiling should be raised to 2500"
    )
    assert "Cap subtopics at 5" not in text, (
        "5-subtopic-per-chapter cap should be removed"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_distiller_prompt.py -v`

Expected: all four assertions FAIL — current `max_tokens` is 2000, current word ceiling is 800, current text has the 5-subtopic cap, and the user-message has no "Verbatim anchors" section.

- [ ] **Step 3: Update `bart/agents/distiller.py`**

Replace the `distill` method (lines 26-55) with:

```python
    def distill(self) -> str:
        """Single call. Returns a structured markdown corpus brief, ≤2500 words."""
        cfg = self.ctx.cfg
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                "Produce a corpus brief, ≤2500 words, with exactly these sections:\n\n"
                "## Materials inventory — one bullet per file (filename + one-line description).\n\n"
                "## Topic outline — chapters in course-sequence order. Format strictly:\n"
                "  ### Ch N: Title\n"
                "  - subtopic\n"
                "  - subtopic\n"
                "Include every subtopic the corpus actually covers — do not cap. "
                "The downstream planner parses these headings.\n\n"
                "## Notation conventions — 3-6 bullets on distinctive notation.\n\n"
                "## Past-exam structure — 3-5 bullets describing any sample/practice exam in the corpus, "
                "or 'No past-exam material in corpus.'\n\n"
                "## Problems & examples index — 5-10 bullets naming the most distinctive problems "
                "using the corpus's own naming convention.\n\n"
                "## Verbatim anchors — 8–15 bullets quoting EXACT strings from the corpus: "
                "notation tokens, key definitions, and distinctive example-problem stems. "
                "Use the corpus's exact wording. Format strictly:\n"
                "  > \"<verbatim>\" — file.pdf\n\n"
                "Skip 'Gaps' unless something major is missing."
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=6000,
            label="distiller",
            temperature=0.2,
        )
```

Also replace the docstring (lines 1-16) — change `"compress the full corpus to a tight ~5-10K-char brief"` to `"compress the full corpus to a structured ~10-15K-char brief"` and update the body to read:

```python
"""DistillerAgent — compress the full corpus to a structured ~10-15K-char brief.

Why this exists:

The original architecture sent the entire 100K+ char corpus to every agent
call. With prompt caching that's affordable; without (subscription mode),
it's the dominant cost in both tokens and latency.

The Distiller runs ONCE up-front using the fast model (Haiku). It produces
a corpus brief that downstream agents (Planner, Author, Critic) use instead
of the raw corpus. The brief includes a 'Verbatim anchors' section that
preserves exact notation, definitions, and problem stems so the brief is
not just a summary but also a quotable reference.

Researcher still sees the full corpus per-day for fresh excerpts.

Net effect: ~70% fewer input tokens across the run vs. raw-corpus calls,
while keeping verbatim source material available downstream.
"""
```

- [ ] **Step 4: Update `prompts/distiller.md`**

Replace the entire file with:

```
You are bart's Distiller. Produce a tight, structured corpus brief that downstream agents use in place of the raw corpus.

Be precise on structure (topics, sections, notation, problems). Be brutally concise on prose. Quote verbatim where useful — the brief is also a quotable reference, not just a summary. Note gaps explicitly rather than fabricating.

In the 'Verbatim anchors' section, lift exact strings from the corpus — notation tokens, key definitions, distinctive problem stems. Preserve the corpus's wording, including idiosyncratic phrasing from the user's professor. Always cite the source filename for each verbatim quote.

Use the exact section headings the user message specifies — downstream agents parse them.
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_distiller_prompt.py -v`

Expected: all four tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bart/agents/distiller.py prompts/distiller.md tests/test_distiller_prompt.py
git commit -m "$(cat <<'EOF'
distiller: expand brief to 2500 words with verbatim anchors

The 800-word ceiling and 5-subtopic-per-chapter cap forced the brief
to summarize away the user's specific notation, definitions, and
problem stems — exactly the material that makes lessons feel
source-grounded rather than generic. The new 'Verbatim anchors'
section preserves quotable exact strings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Researcher max_tokens + prompt update

**Files:**
- Modify: `bart/agents/researcher.py:25-32`
- Modify: `prompts/researcher.md`

- [ ] **Step 1: Update `bart/agents/researcher.py`**

Change `max_tokens=4000` (line 29) to `max_tokens=6000`. The full `research` method should read:

```python
    def research(self, topic: str, learning_objectives: list[str]) -> str:
        cfg = self.ctx.cfg
        objectives = "\n- ".join(learning_objectives)
        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n"
                f"Topic: {topic}\n"
                f"Objectives:\n- {objectives}"
            ),
        }]
        return self.ctx.llm.complete(
            model=cfg.fast_model,
            system=self.system_prompt,
            user=user,
            max_tokens=6000,
            label=f"researcher:{topic[:40]}",
            temperature=0.2,
        )
```

- [ ] **Step 2: Update `prompts/researcher.md`**

Replace the entire file with:

```
You are bart's Researcher. For a given topic, produce a tight markdown brief drawn entirely from the corpus.

Surface, in this order:
1. **Verbatim definitions** — quote them in blockquotes with the source filename. Do not paraphrase.
2. **Verbatim theorem statements / key formulas** — quote in blockquotes with the source filename.
3. **Verbatim example-problem stems** — at least 2 if the corpus has them, quoted exactly with the source filename and the corpus's own naming ("HW7 P3", "Mock §III.5", etc.).
4. **Notation conventions** observed in the corpus.
5. **Connections to other corpus topics** — bullets referencing the corpus's own section/chapter labels.
6. **Gaps** — a single section at the end if the corpus is weak on this topic.

~800-1500 words. No padding. Never invent content not in the corpus. When the corpus has a verbatim quote that fits, USE THE QUOTE — do not summarize it.
```

- [ ] **Step 3: Manual verification — read the file back**

Run: `cat prompts/researcher.md`

Expected: file matches the text above (presence of "Verbatim definitions", "Verbatim theorem", "Verbatim example-problem stems").

- [ ] **Step 4: Commit**

```bash
git add bart/agents/researcher.py prompts/researcher.md
git commit -m "$(cat <<'EOF'
researcher: demand verbatim excerpts; raise max_tokens to 6000

Per-day Researcher used to summarize. The Author needs source-grounded
quote-able material to write exam-level lessons, so the prompt now
forces verbatim quotes (definitions, theorems, problem stems) and the
output ceiling is raised to fit them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: ExamPatternAgent — new sidecar agent

**Files:**
- Create: `bart/agents/exam_pattern.py`
- Create: `prompts/exam_pattern.md`
- Modify: `bart/agents/__init__.py`
- Test: `tests/test_exam_pattern.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_exam_pattern.py`:

```python
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
    raw = "```json\n{\"problems\": []}\n```"  # missing structure + style_notes
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_exam_pattern.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'bart.agents.exam_pattern'`.

- [ ] **Step 3: Create `prompts/exam_pattern.md`**

Write to `prompts/exam_pattern.md`:

```
You are bart's Exam Pattern extractor. Scan the corpus for any past exams, mock exams, midterms, or sample tests. Extract a structured JSON description of the user's REAL exam style so the practice exam can be patterned on it.

Output: a single fenced ```json block, no commentary outside it. Schema:
```json
{
  "problems": [
    {
      "stem": "<verbatim problem statement, ≤400 chars; preserve the corpus's exact wording>",
      "source_file": "<filename containing this problem>",
      "type": "<one of: mechanism | synthesis | proof | derivation | computation | short-answer | multiple-choice | free-response>",
      "points": <integer or null if not stated>,
      "difficulty": "<low | medium | high>",
      "topics": ["<topic from corpus brief outline, when identifiable>"]
    }
  ],
  "structure": {
    "total_points": <integer or null>,
    "section_breakdown": [
      {"section": "<label, e.g. 'Part A'>", "count": <int>, "points": <int>}
    ],
    "common_types": ["<types>"]
  },
  "style_notes": ["<3–6 bullets on phrasing, conventions, point allocations, instructions students see>"]
}
```

If the corpus has no past exam material, return:
```json
{"problems": [], "structure": {}, "style_notes": ["No past-exam material in corpus."]}
```

Cap problems at 40. Pick the most distinctive if more exist. NEVER invent problems — quote them verbatim from the corpus.
```

- [ ] **Step 4: Create `bart/agents/exam_pattern.py`**

Write to `bart/agents/exam_pattern.py`:

```python
"""ExamPatternAgent — extract a structured past-exam pattern sidecar.

The Practice Exam Author and per-day drill sections use this sidecar to
pattern problems on the user's REAL past exams (style, difficulty,
phrasing, point allocation) rather than emit AI-generic problems.

Runs once up-front, in parallel with the notation/problem-index sidecars.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import Agent


EMPTY_PATTERNS: dict[str, Any] = {"problems": [], "structure": {}, "style_notes": []}

_REQUIRED_KEYS = ("problems", "structure", "style_notes")


class ExamPatternAgent(Agent):
    name = "exam_pattern"
    prompt_file = "exam_pattern.md"

    def extract(self) -> dict[str, Any]:
        """One Haiku call against the full corpus. Returns parsed dict or EMPTY_PATTERNS."""
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
            label="exam_pattern",
            temperature=0.1,
        )
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not m:
            return dict(EMPTY_PATTERNS)
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return dict(EMPTY_PATTERNS)
        if not isinstance(data, dict):
            return dict(EMPTY_PATTERNS)
        if not all(k in data for k in _REQUIRED_KEYS):
            return dict(EMPTY_PATTERNS)
        # Coerce shapes defensively.
        problems = data.get("problems", [])
        if not isinstance(problems, list):
            problems = []
        structure = data.get("structure", {})
        if not isinstance(structure, dict):
            structure = {}
        style_notes = data.get("style_notes", [])
        if not isinstance(style_notes, list):
            style_notes = []
        return {
            "problems": [_coerce_problem(p) for p in problems if isinstance(p, dict)][:40],
            "structure": structure,
            "style_notes": [str(s) for s in style_notes],
        }


def _coerce_problem(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "stem": str(p.get("stem", "")),
        "source_file": str(p.get("source_file", "")),
        "type": str(p.get("type", "")),
        "points": p.get("points") if isinstance(p.get("points"), int) else None,
        "difficulty": str(p.get("difficulty", "")),
        "topics": [str(t) for t in p.get("topics", []) if t] if isinstance(p.get("topics"), list) else [],
    }


def format_for_practice_exam(patterns: dict[str, Any]) -> str:
    """Markdown block for the practice-exam Author brief."""
    problems = patterns.get("problems") or []
    structure = patterns.get("structure") or {}
    style_notes = patterns.get("style_notes") or []
    if not problems and not structure and not style_notes:
        return "(no past-exam patterns indexed)"
    out: list[str] = []
    if structure:
        total = structure.get("total_points")
        breakdown = structure.get("section_breakdown") or []
        common = structure.get("common_types") or []
        out.append("**Structure (from real past exams):**")
        if total is not None:
            out.append(f"- total points: {total}")
        for sec in breakdown:
            out.append(
                f"- {sec.get('section', '?')}: "
                f"{sec.get('count', '?')} problem(s), {sec.get('points', '?')} pts"
            )
        if common:
            out.append(f"- common types: {', '.join(common)}")
        out.append("")
    if style_notes:
        out.append("**Style notes:**")
        for s in style_notes:
            out.append(f"- {s}")
        out.append("")
    if problems:
        out.append("**Sample stems (verbatim — pattern your problems on these):**")
        for p in problems[:15]:
            stem = p.get("stem", "").strip().replace("\n", " ")
            src = p.get("source_file", "?")
            type_ = p.get("type", "?")
            out.append(f"- ({type_}) \"{stem}\" — {src}")
    return "\n".join(out)


def format_for_daily_drill(patterns: dict[str, Any], topics: list[str]) -> str:
    """Markdown block for a daily lesson's drill section, filtered by topic."""
    problems = patterns.get("problems") or []
    if not problems or not topics:
        return ""
    needles = [t.lower().strip() for t in topics if t]
    matched: list[dict[str, Any]] = []
    for p in problems:
        tags = [t.lower() for t in p.get("topics", [])]
        if any(any(n in tag or tag in n for tag in tags) for n in needles):
            matched.append(p)
    if not matched:
        return ""
    out = ["**Past-exam stems on today's topics (verbatim — pattern drill problems on these):**"]
    for p in matched[:6]:
        stem = p.get("stem", "").strip().replace("\n", " ")
        src = p.get("source_file", "?")
        out.append(f"- \"{stem}\" — {src}")
    return "\n".join(out)
```

- [ ] **Step 5: Export from `bart/agents/__init__.py`**

Edit `bart/agents/__init__.py`. Add the import after the existing `from .distiller import DistillerAgent` line:

```python
from .exam_pattern import ExamPatternAgent
```

Add `"ExamPatternAgent"` to the `__all__` list — placed after `"ProblemIndexerAgent"`. The full updated `__all__` should be:

```python
__all__ = [
    "DistillerAgent", "TopicDistillerAgent", "PlannerAgent", "AuthorAgent",
    "CriticAgent", "ReviserAgent", "ResearcherAgent",
    "ReviewerAgent", "ReviewResult",
    "NotationExtractorAgent", "ProblemIndexerAgent", "ExamPatternAgent",
    "SolverAgent", "WhimsyIndexerAgent",
]
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_exam_pattern.py -v`

Expected: all 7 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add bart/agents/exam_pattern.py bart/agents/__init__.py prompts/exam_pattern.md tests/test_exam_pattern.py
git commit -m "$(cat <<'EOF'
agents: add ExamPatternAgent sidecar

One Haiku call against the full corpus emits a JSON sidecar of
verbatim past-exam problems, structure, and style notes. Practice
exam and daily drill sections use it to pattern problems on the
user's real past exams instead of emitting AI-generic ones.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Author wiring — research_slice + exam_patterns_block kwargs

**Files:**
- Modify: `bart/agents/author.py:19-58`
- Test: `tests/test_author_wiring.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_author_wiring.py`:

```python
"""Tests for AuthorAgent's research_slice / exam_patterns_block plumbing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from bart.agents.author import AuthorAgent
from bart.agents.base import AgentContext


class _FakeLLM:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        # Return a healthy minimum so block_density doesn't fire a fix call.
        return "x" * 2000


@dataclass
class _FakeCfg:
    subject: str = "Subject"
    student_level: str = "undergraduate"
    style: str = "academic-rigorous"
    daily_hours: float = 3.0
    guidance: str = "guidance"
    primary_model: str = "claude-opus-4-7"
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
    # Order: brief THEN research slice.
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
    # Order: brief, research_slice, exam_patterns_block.
    assert text.index("BRIEF TEXT") < text.index("RS") < text.index("EP")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_author_wiring.py -v`

Expected: FAIL — `AuthorAgent.write()` does not accept `research_slice` / `exam_patterns_block` kwargs (`TypeError: write() got an unexpected keyword argument`).

- [ ] **Step 3: Update `bart/agents/author.py`**

Modify `AuthorAgent.write` (lines 19-58 of the existing file) to accept the new kwargs and append them to the user message after the BRIEF block.

Replace the signature + the user-message construction. The full updated `write` method becomes:

```python
    def write(
        self,
        artifact_kind: str,
        brief: str,
        max_tokens: int = 16000,
        temperature: float = 0.7,
        label_suffix: str = "",
        on_density: callable = None,  # type: ignore[valid-type]
        research_slice: str = "",
        exam_patterns_block: str = "",
    ) -> str:
        cfg = self.ctx.cfg

        # Build a kind-augmented system prompt: base author.md + the slim
        # per-artifact catalog. This goes in the SYSTEM payload so prompt
        # caching gives ~90% input-token discount on subsequent calls of
        # the same artifact kind. Block catalog + skeleton no longer live
        # in the user-message brief (which changes per call).
        try:
            from ..render.block_expand import catalog_for as _catalog_for
            kind_catalog = _catalog_for(artifact_kind, subject=cfg.subject)
        except Exception:  # noqa: BLE001
            kind_catalog = ""
        if kind_catalog:
            system_for_kind = (
                self.system_prompt
                + f"\n\n# Block catalog for `{artifact_kind}` (use these)\n\n"
                + kind_catalog
            )
        else:
            system_for_kind = self.system_prompt

        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"ARTIFACT: {artifact_kind}\n"
                f"Subject: {cfg.subject} · Level: {cfg.student_level} · Style: {cfg.style} · "
                f"Daily hours: {cfg.daily_hours}\n"
                f"User guidance: {cfg.guidance}\n\n"
                f"BRIEF\n{brief}"
            ),
        }]
        if research_slice.strip():
            user.append({
                "type": "text",
                "text": (
                    "RESEARCH SLICE — verbatim corpus excerpts for this artifact's topic. "
                    "Quote these exactly when they fit; do not paraphrase.\n\n"
                    f"{research_slice}"
                ),
            })
        if exam_patterns_block.strip():
            user.append({
                "type": "text",
                "text": (
                    "EXAM PATTERNS — patterned on the user's past exams. "
                    "Match the style, phrasing, and difficulty distribution shown here.\n\n"
                    f"{exam_patterns_block}"
                ),
            })
```

The remainder of the method (model/override resolution, the `complete` call, truncation continuation, block-density continuation) stays unchanged — these reuse the `user` variable. Keep lines 60-138 of the existing file intact.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_author_wiring.py -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bart/agents/author.py tests/test_author_wiring.py
git commit -m "$(cat <<'EOF'
author: add research_slice and exam_patterns_block kwargs

Both append after the BRIEF block as separate user-message sections so
the daily-lesson and practice-exam paths can thread verbatim corpus
excerpts and past-exam patterns into the Author without a brief
schema change. Empty values are no-ops, keeping all other artifact
kinds (schematics, whimsy, short guide) unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire ExamPattern into the orchestrator sidecar pool

**Files:**
- Modify: `bart/orchestrator.py` (sidecar block around lines 232-240, plus a new `_extract_exam_patterns` method)

- [ ] **Step 1: Add `_extract_exam_patterns` method**

In `bart/orchestrator.py`, after the existing `_extract_problem_index` method (it ends around line 503), insert a new method that mirrors its structure:

```python
    def _extract_exam_patterns(self, full_ctx: AgentContext) -> dict[str, Any]:
        import os
        path = self.paths.checkpoints_dir / "exam_patterns.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                data = json.loads(path.read_text())
                self.console.print(
                    f"  [dim]✓ reused exam patterns "
                    f"({len(data.get('problems', []))} problems)[/dim]"
                )
                return data
            except json.JSONDecodeError:
                pass
        if os.environ.get("BART_SKIP_EXAM_PATTERN") == "1":
            from .agents.exam_pattern import EMPTY_PATTERNS
            return dict(EMPTY_PATTERNS)
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing past-exam patterns "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        from .agents.exam_pattern import ExamPatternAgent
        try:
            agent = ExamPatternAgent(full_ctx)
            patterns = agent.extract()
        except Exception as e:  # noqa: BLE001
            self.logger.warning("exam_pattern extraction failed: %s", e)
            from .agents.exam_pattern import EMPTY_PATTERNS
            patterns = dict(EMPTY_PATTERNS)
        atomic_write_json(path, patterns)
        return patterns
```

- [ ] **Step 2: Grow the sidecar `ThreadPoolExecutor` from 2 to 3 workers**

In `bart/orchestrator.py`, replace the sidecar block (lines 232-240) with:

```python
            # ── Per-run sidecar primitives (notation card, problem index,
            # exam patterns). Three independent corpus reads → run them in
            # parallel for ~3x faster sidecar extraction.
            with ThreadPoolExecutor(max_workers=3) as _sidecar_pool:
                _f_notation = _sidecar_pool.submit(
                    self._extract_notation_card, corpus_brief, brief_ctx,
                )
                _f_problems = _sidecar_pool.submit(
                    self._extract_problem_index, full_ctx,
                )
                _f_exam = _sidecar_pool.submit(
                    self._extract_exam_patterns, full_ctx,
                )
                notation_card = _f_notation.result()
                problem_index = _f_problems.result()
                exam_patterns = _f_exam.result()
```

- [ ] **Step 3: Run the existing test suite to confirm nothing breaks**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: all tests PASS (no orchestrator-level tests exist; this is just a smoke check that imports still resolve).

- [ ] **Step 4: Commit**

```bash
git add bart/orchestrator.py
git commit -m "$(cat <<'EOF'
orchestrator: run ExamPatternAgent in the sidecar pool

Sidecar pool grows from 2 → 3 workers (notation, problem index, exam
patterns) so the new past-exam-pattern extraction adds zero serial
latency. Cached to checkpoints/exam_patterns.json; gracefully empty
on parse failure or BART_SKIP_EXAM_PATTERN=1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Reactivate per-day Researcher as a primary path

**Files:**
- Modify: `bart/orchestrator.py` (around lines 303-345 — the daily-cards stage and `_run_daily_lessons` call)

The existing `_prefetch_research` function already implements per-day Researcher + parallel + disk cache. Right now it only runs as a *fallback* when the topic-distiller misses days. We add a second, unconditional pass that builds `research_full_by_day: dict[int, str]` from the per-day Researcher, parallel to (not replacing) the topic-distiller study cards.

- [ ] **Step 1: Modify the daily-cards stage to also fetch full research**

In `bart/orchestrator.py`, locate the block beginning at `# ----- 3. Daily study cards (one batched Haiku call)` (around line 303). After the existing block that builds `research_by_day` (the study cards) and the missing-days fallback, add:

```python
            # ── Per-day Researcher: full-corpus excerpts for each day.
            # Runs in addition to the topic-distiller study card. The Author
            # gets BOTH — the study card (concise day plan) and the
            # research slice (verbatim corpus excerpts). Disk-cached so
            # --resume is free.
            import os as _os
            if _os.environ.get("BART_SKIP_RESEARCHER") == "1":
                research_full_by_day: dict[int, str] = {}
                self.console.print(
                    "  [dim]BART_SKIP_RESEARCHER=1 — skipping per-day Researcher[/dim]"
                )
            else:
                self.console.print(
                    f"\n[bold #c96442]▸ Per-day Researcher (full corpus)[/bold #c96442]"
                )
                research_full_by_day = self._prefetch_research(day_entries, researcher)
```

This block goes immediately AFTER the existing `if cards_path.exists() ... else: ... research_by_day.update(fallback)` block and BEFORE `# Then generate daily lessons (Author + optional Reviewer).`.

- [ ] **Step 2: Pass `research_full_by_day` and `exam_patterns` into `_run_daily_lessons`**

Find the call to `self._run_daily_lessons` (around line 339). Replace it with:

```python
            # Then generate daily lessons (Author + optional Reviewer).
            self.console.print("\n[bold #c96442]▸ Generating daily lessons[/bold #c96442]")
            with self._stage("daily_lessons"):
                self._run_daily_lessons(
                    day_entries, author, reviewer, master_plan, research_by_day,
                    notation_card=notation_card,
                    problem_index=problem_index,
                    review_queue=review_queue,
                    whimsy_index=whimsy_index,
                    research_full_by_day=research_full_by_day,
                    exam_patterns=exam_patterns,
                )
```

- [ ] **Step 3: Update `_run_daily_lessons` signature and `_gen_day` body**

In `bart/orchestrator.py`, modify `_run_daily_lessons` (the def at line 681). The updated signature becomes:

```python
    def _run_daily_lessons(
        self, day_entries, author, reviewer, master_plan, research_by_day,
        notation_card: str = "",
        problem_index: list[dict[str, Any]] | None = None,
        review_queue: dict[int, list[dict[str, Any]]] | None = None,
        whimsy_index: dict[str, str] | None = None,
        research_full_by_day: dict[int, str] | None = None,
        exam_patterns: dict[str, Any] | None = None,
    ):
        if not day_entries:
            self.console.print("[yellow]  ⚠ Planner produced no day entries — skipping daily lessons.[/yellow]")
            return
        problem_index = problem_index or []
        review_queue = review_queue or {}
        whimsy_index = whimsy_index or {}
        research_full_by_day = research_full_by_day or {}
        exam_patterns = exam_patterns or {}
```

Inside `_gen_day` (the inner function), find the `text = author.write(...)` call (around line 734). Replace it with:

```python
            from .agents import exam_pattern as _exam_pattern
            day_research_full = research_full_by_day.get(day_num, "")
            exam_patterns_drill = _exam_pattern.format_for_daily_drill(
                exam_patterns, day_topics,
            )
            # max_tokens=6000 is plenty for a fully-instrumented daily lesson
            # (~18K chars). The previous 12000 ceiling let Sonnet pad to 35K
            # chars, which is the dominant wall-time cost on subscription mode.
            text = author.write(
                "daily_lesson", brief,
                max_tokens=6000, label_suffix=f"day{day_num}",
                on_density=_on_density,
                research_slice=day_research_full,
                exam_patterns_block=exam_patterns_drill,
            )
```

Note: `day_topics` is already computed earlier in `_gen_day` (line 712 — `day_topics = [topic] + entry.get("chapters", [])`). Reuse that variable; no re-computation needed.

- [ ] **Step 4: Run the test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: all tests PASS. (No orchestrator-level tests exist; this is a smoke check that imports/types still resolve.)

- [ ] **Step 5: Commit**

```bash
git add bart/orchestrator.py
git commit -m "$(cat <<'EOF'
orchestrator: per-day Researcher runs alongside topic-distiller cards

The topic-distiller study card stays as the concise per-day plan,
but daily Authors now also receive a verbatim research slice fetched
by the Researcher with full-corpus access (parallel, disk-cached).
ExamPattern's daily-drill subset is plumbed to the Author too.
BART_SKIP_RESEARCHER=1 disables for --fast / --turbo.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire ExamPattern into the practice-exam path

**Files:**
- Modify: `bart/orchestrator.py` (`_run_artifacts_parallel` and the `artifacts` list around line 277-298)

The practice exam Author currently splits into Part A (Author writes problems) and Part B (Solver writes the answer key) — see `_run_artifacts_parallel` around line 568. The `exam_patterns` block needs to flow into the Part A call.

- [ ] **Step 1: Pass `exam_patterns` into `_run_artifacts_parallel`**

In `bart/orchestrator.py`, find the call to `self._run_artifacts_parallel` (around line 295). Replace the call site with:

```python
            with self._stage("top_level_artifacts"):
                self._run_artifacts_parallel(
                    artifacts, author, reviewer, master_plan,
                    solver=solver, notation_card=notation_card,
                    exam_patterns=exam_patterns,
                )
```

- [ ] **Step 2: Update `_run_artifacts_parallel` signature**

In `bart/orchestrator.py`, modify the def of `_run_artifacts_parallel` (line 537). The new signature:

```python
    def _run_artifacts_parallel(
        self, artifacts, author, reviewer, master_plan,
        solver: SolverAgent | None = None,
        notation_card: str = "",
        exam_patterns: dict[str, Any] | None = None,
    ):
        results: dict[str, str] = {}
        exam_patterns = exam_patterns or {}
```

- [ ] **Step 3: Pass exam_patterns_block into the practice-exam Author call**

Inside `_gen_one` (the inner function), find the `if kind == "practice_exam" and solver is not None:` branch (around line 568). Replace the `part_a = author.write(...)` call with:

```python
            if kind == "practice_exam" and solver is not None:
                from .agents import exam_pattern as _exam_pattern
                exam_patterns_full = _exam_pattern.format_for_practice_exam(exam_patterns)
                part_a = author.write(
                    "practice_exam_part_a", brief, max_tokens=max_tokens,
                    label_suffix="part_a", on_density=_on_density,
                    exam_patterns_block=exam_patterns_full,
                )
                part_b = solver.solve(part_a, notation_card, max_tokens=max_tokens)
                text = part_a.rstrip() + "\n\n---\n\n" + part_b.lstrip()
                # Skip the heuristic+reviewer pass for the split exam — each
                # half was already written within scope.
                atomic_write_text(target, text)
                return kind, text
```

- [ ] **Step 4: Run the test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bart/orchestrator.py
git commit -m "$(cat <<'EOF'
orchestrator: pattern the practice exam on real past-exam stems

The practice-exam Author Part A call now receives the formatted
exam_patterns block (verbatim past-exam stems + structure + style
notes). Generated problems can be patterned on the user's actual
exams instead of looking AI-generic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Speed presets — set BART_SKIP_RESEARCHER and BART_SKIP_EXAM_PATTERN

**Files:**
- Modify: `bart/__main__.py` (around lines 229-244 — the `--fast` / `--turbo` env-var setup)

- [ ] **Step 1: Update `--fast` and `--turbo` to set the new skip vars**

In `bart/__main__.py`, modify the speed-preset block (lines 229-244). The updated block:

```python
        if turbo_mode or fast_mode:
            no_critic = True
            if not model_override:
                model_override = "claude-sonnet-4-6"
            # Both speed presets skip the per-day Researcher (full-corpus path).
            # The topic-distiller study card stays in place, so daily lessons
            # still get a per-day plan — they just don't get verbatim excerpts.
            os.environ["BART_SKIP_RESEARCHER"] = "1"
        if turbo_mode:
            # Haiku for the daily-lesson Author too. Massive wall-time win.
            if not getattr(args, "model", None):
                model_override = "claude-haiku-4-5-20251001"
            os.environ["BART_TOP_LEVEL_MODEL_OVERRIDE"] = "claude-haiku-4-5-20251001"
            # Block-fix continuation stays ENABLED in turbo — Haiku occasionally
            # under-uses bart blocks, and the fix is a cheap (≤1500 tok) Haiku
            # call that guarantees structural density. Speed dominates output
            # length; this retry costs ~5-10s and keeps block usage high.
            os.environ.pop("BART_SKIP_BLOCK_FIX", None)
            # Turbo also skips the past-exam-pattern sidecar — saves one Haiku
            # call. --fast keeps it (minor cost, big quality lift on practice exam).
            os.environ["BART_SKIP_EXAM_PATTERN"] = "1"
            # Haiku tolerates higher concurrency than Sonnet on subscription.
            max_parallel = 3
```

- [ ] **Step 2: Manual verification — read the file back**

Run: `grep -n "BART_SKIP_RESEARCHER\|BART_SKIP_EXAM_PATTERN" bart/__main__.py`

Expected: matches in the speed-preset block.

- [ ] **Step 3: Commit**

```bash
git add bart/__main__.py
git commit -m "$(cat <<'EOF'
cli: --fast/--turbo skip the new heavy corpus paths

--fast skips the per-day Researcher (BART_SKIP_RESEARCHER=1) so the
new full-corpus per-day pass doesn't undo the speed promise. --turbo
also skips ExamPattern (BART_SKIP_EXAM_PATTERN=1) for the same
reason. Default mode runs both.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end smoke verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`

Expected: all tests PASS, including:
- `tests/test_corpus.py` (3 tests, existing)
- `tests/test_corpus_three_slice.py` (4 tests, new)
- `tests/test_distiller_prompt.py` (3 tests, new)
- `tests/test_exam_pattern.py` (7 tests, new)
- `tests/test_author_wiring.py` (4 tests, new)
- `tests/test_planner_parse.py` (existing)

- [ ] **Step 2: Dry-run smoke test**

Run: `cp examples/sample_notes.md materials/ 2>/dev/null; ./run --dry-run`

Expected: the splash, the corpus summary with 3-slice truncation note (if a long file is present), and a clean exit before any API calls. No tracebacks.

- [ ] **Step 3: Verify no orphan imports / dead code**

Run: `.venv/bin/python -c "from bart.agents import ExamPatternAgent; from bart.orchestrator import Orchestrator; print('imports ok')"`

Expected: `imports ok` prints, no exceptions.

- [ ] **Step 4: Confirm the `--fast` and `--turbo` env vars are set**

Run: `.venv/bin/python -c "import os; from unittest.mock import patch; import sys; sys.argv = ['run', '--fast', '--dry-run']; from bart import __main__ as m" 2>&1 | head -5; echo "---"; .venv/bin/python -c "import os; print('BART_SKIP_RESEARCHER=', os.environ.get('BART_SKIP_RESEARCHER'))"`

(This may not fully trigger the env-var code path without invoking the dispatcher; if it doesn't, just confirm via grep that the lines are in `__main__.py` — the explicit grep in Task 9 Step 2 is the source-level check.)

- [ ] **Step 5: Final commit (if anything was tweaked during verification)**

If verification surfaced no issues, no commit is needed. If a tweak was needed, commit it with a message describing the fix. Otherwise:

```bash
git log --oneline -10
```

Expected: the previous nine task commits visible in order.

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented in |
|---|---|
| §1 Three-slice truncation | Task 1 |
| §2 Distiller expansion + verbatim anchors | Task 2 |
| §3 ExamPatternAgent + helpers + JSON schema + fallback | Task 4 |
| §4 Per-day Researcher reactivation + cache + 6000 max_tokens + prompt | Tasks 3 + 7 |
| §5 Author kwargs + daily-lesson + practice-exam wiring | Tasks 5 + 7 + 8 |
| Sidecar pool 2→3 workers | Task 6 |
| Error handling (try/except + EMPTY_PATTERNS fallback) | Tasks 4 + 6 |
| Cost guardrails (BART_SKIP_RESEARCHER, BART_SKIP_EXAM_PATTERN) | Tasks 6 + 7 + 9 |
| Tests: corpus, exam_pattern, distiller_prompt, author_wiring | Tasks 1, 2, 4, 5 |

**Placeholder scan:** No "TBD", "TODO", "implement later", or "fill in details" in any task. Every code step shows the actual code.

**Type consistency:** `EMPTY_PATTERNS` defined in `bart/agents/exam_pattern.py` (Task 4) and imported by `bart/orchestrator.py` (Task 6). `format_for_practice_exam` and `format_for_daily_drill` defined in Task 4 and called in Tasks 7 and 8. `research_slice` and `exam_patterns_block` kwargs added to `AuthorAgent.write` in Task 5 and passed in Tasks 7 and 8. `research_full_by_day` and `exam_patterns` parameters added to `_run_daily_lessons` in Task 7 — call sites updated in same task.
