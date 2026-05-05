# Corpus quality lift — design spec

**Date:** 2026-05-04
**Status:** Approved (awaiting implementation plan)
**Scope:** `bart/io/corpus.py`, `bart/agents/distiller.py`, `bart/agents/researcher.py`, `bart/agents/author.py`, `bart/orchestrator.py`, `prompts/distiller.md`, `prompts/researcher.md`, plus a new `bart/agents/exam_pattern.py` and prompt.

## Problem

The current pipeline compresses the source corpus too aggressively before any lesson is written, so daily lessons feel generic and don't work out exam-level problems. Symptom: lessons teach topics broadly without citing the user's specific notation, problem numbers, worked examples, or the quirks of their professor's notes; practice problems read like generic AI-written problems rather than ones patterned on the user's past exams.

The pipeline today has four compression stages:

1. **`build_corpus`** — caps total at 400K chars; per-file head/tail slice drops middles of long files; remaining files are dropped when budget is exhausted.
2. **`DistillerAgent`** — Haiku crushes the entire corpus into a ≤800-word brief with a 5-subtopic-per-chapter cap (`max_tokens=2000`). This is the dominant compression (~80×).
3. **`TopicDistillerAgent`** — Haiku turns the *brief* into per-day study cards of 150–300 words each.
4. **`ResearcherAgent`** exists with full-corpus access but, in current `orchestrator.py`, the per-day Researcher path is not invoked for daily-lesson generation — Authors write from brief + study card alone.

Net: by the time the Author writes a daily lesson, it has well under 1% of the original corpus to draw from, and zero verbatim source material.

## Goals

- Daily lessons cite the user's specific notation, problem numbers, and named examples.
- Daily lessons work out exam-level problems (not toy examples).
- Practice exam is patterned on the user's actual past exams (style, difficulty, point distribution).
- All quality lifts are additive — no regression to existing fast paths (`--fast`, `--turbo`).
- Wall-clock impact ≤ +25% on default mode, 0% on speed presets.

## Non-goals

- Changing the model used for daily-lesson Author calls (stays Opus / `primary_model`).
- Re-architecting the agent base, telemetry, render, or quality-harness layers.
- Modifying subscription-mode behavior beyond what falls out of the agent changes.
- Adding new top-level CLI subcommands.

## Design

Five additive changes to the pipeline.

### 1. `build_corpus` — three-slice truncation

**File:** `bart/io/corpus.py`

Replace the head/tail slice in `build_corpus` with **head + middle-sample + tail**, each ≈ `per_file_cap // 3`.

- Total budget stays at 400K chars (`char_budget` default unchanged).
- Per-file cap formula unchanged: `per_file_cap = max(20_000, char_budget // max(len(kept), 1))`.
- New truncation marker format:
  ```
  <head slice>

  […X chars before middle sample…]

  <middle slice>

  […Y chars after middle sample…]

  <tail slice>
  ```
- Middle slice is centered on `len(body) // 2`, ± `per_file_cap // 6`.
- No public-API change; existing callers unaffected.

### 2. `DistillerAgent` — wider brief with verbatim anchors

**Files:** `bart/agents/distiller.py`, `prompts/distiller.md`

- `max_tokens`: 2000 → 6000.
- Word ceiling in the user message: 800 → 2500.
- Drop the "Cap subtopics at 5 per chapter" line from the user message.
- Add a new section spec to both the user message and `prompts/distiller.md`:

  > `## Verbatim anchors — 8–15 bullets quoting EXACT strings from the corpus: notation tokens, key definitions, distinctive example-problem stems. Use the corpus's exact wording. Format: '> "<verbatim>" — file.pdf'`

- Keep the existing sections: Materials inventory, Topic outline, Notation conventions, Past-exam structure, Problems & examples index.
- Output is still consumed verbatim by downstream agents through `corpus_brief`; no parsing changes are required because the existing parser (`deterministic_planner.parse_topic_outline`) only reads the `## Topic outline` section.

### 3. `ExamPatternAgent` — new sidecar

**Files:** `bart/agents/exam_pattern.py` (new), `prompts/exam_pattern.md` (new)

A single Haiku call up front that scans the full corpus for past-exam-style problems and emits a JSON sidecar.

**Class shape** (mirrors `ProblemIndexerAgent`):

```python
class ExamPatternAgent(Agent):
    name = "exam_pattern"
    prompt_file = "exam_pattern.md"

    def extract(self) -> dict[str, Any]:
        # one call, returns parsed JSON or fallback shape on parse failure
```

**Sidecar file:** `output/<run_id>/checkpoints/exam_patterns.json`

**Schema:**

```json
{
  "problems": [
    {
      "stem": "<verbatim problem statement, ≤400 chars>",
      "source_file": "past_exam_2024.pdf",
      "type": "mechanism | synthesis | proof | computation | short-answer | …",
      "points": null,
      "difficulty": "low | medium | high",
      "topics": ["<topic from corpus brief outline, when identifiable>"]
    }
  ],
  "structure": {
    "total_points": 100,
    "section_breakdown": [{"section": "Part A", "count": 5, "points": 30}],
    "common_types": ["mechanism", "short-answer"]
  },
  "style_notes": ["<3–6 bullets on phrasing, conventions, point allocations>"]
}
```

**Fallback shape** on any parse failure: `{"problems": [], "structure": {}, "style_notes": []}`. A warning is written to `run.log`. The practice exam still generates without the boost.

**Module-level helpers** (parallel to `bart/agents/problem_indexer.py`'s `format_problem_block`):

- `format_for_practice_exam(patterns: dict) -> str` — markdown block of structure + sample stems for the practice-exam Author.
- `format_for_daily_drill(patterns: dict, topics: list[str]) -> str` — filtered subset matching today's topics, used in daily lessons' drill sections.

### 4. `ResearcherAgent` — per-day, parallel, cached

**Files:** `bart/agents/researcher.py`, `prompts/researcher.md`, `bart/orchestrator.py`

- `max_tokens`: 4000 → 6000.
- Update `prompts/researcher.md` to demand verbatim excerpts (not summaries) wherever the corpus has them; keep the topic-targeting language.
- Per-day disk cache at `output/<run_id>/checkpoints/research/day_NN.md`. Reuse on `--resume` if the file exists and is >200 chars.
- Orchestrator wires up a `ThreadPoolExecutor(max_workers=self.max_parallel)` block before the daily-lesson loop that calls `researcher.research(topic, learning_objectives)` for every day, builds `research_by_day_full: dict[int, str]`.
- This block runs **after** the existing `topic_distiller.distill_per_day` call and **before** the daily-lesson generation loop.

### 5. Author wiring — daily lessons + practice exam

**Files:** `bart/agents/author.py`, `bart/orchestrator.py`

- `AuthorAgent.write` gains two optional kwargs:
  - `research_slice: str = ""` — appended to the user message after the brief, labeled `RESEARCH SLICE — verbatim corpus excerpts for today's topic`.
  - `exam_patterns_block: str = ""` — appended after research slice, labeled `EXAM PATTERNS — patterned on your past exams`.
- For `artifact_kind == "daily_lesson"`, orchestrator passes both:
  - `research_slice = research_by_day_full[day_num]`
  - `exam_patterns_block = exam_pattern.format_for_daily_drill(patterns, today_topics)`
- For `artifact_kind == "practice_exam"`, orchestrator passes:
  - `exam_patterns_block = exam_pattern.format_for_practice_exam(patterns)`
- Other artifact kinds (`schematics`, `whimsical_notes`, `short_study_guide`) are unaffected — they don't pass these kwargs.
- The existing `ctx.corpus_block` (the brief) and `BRIEF` payload remain in place; the new blocks are additive.

### Sidecar parallelism

`bart/orchestrator.py` already runs `_extract_notation_card` and `_extract_problem_index` in a `ThreadPoolExecutor(max_workers=2)`. Grow to **3 workers** to add `_extract_exam_patterns`. All three are independent corpus reads.

### Updated data flow

```
Materials → extract → Corpus(400K, 3-slice trunc)
                          ↓
      ┌───────────────────┼───────────────────┬─────────────────┐
      ↓                   ↓                   ↓                 ↓
  Distiller        NotationExtractor    ExamPattern      ProblemIndexer
  (≤2500 words,    (existing)           (NEW, Haiku)     (existing)
   verbatim anchors,
   max_tokens 6000)
      ↓
  corpus_brief
      ↓
  TopicDistiller → study_cards (per day)
      ↓
  Researcher (per day, parallel,
              full corpus, max_tokens 6000, cached)
      ↓ research_by_day_full
      ↓
  Author (Opus) ← brief + study_card + research_slice + exam_patterns
      ↓
  Reviewer / Output
```

## Error handling

- Every new agent call (Researcher per-day, ExamPattern) wrapped in try/except. On failure, log to `run.log` and pass an empty string downstream. **No new failure modes for daily lessons** — Author falls back to the existing brief + study-card path.
- Per-day Researcher cache makes `--resume` skip already-fetched research. Cache invalidation: file exists and >200 chars → reuse.
- `exam_patterns.json` parse failures: fall back to empty shape `{"problems": [], "structure": {}, "style_notes": []}`, warn in `run.log`. Practice exam still generates without the boost.
- Schema validation on `ExamPatternAgent` output: if any required top-level key is missing, treat as parse failure (same fallback).

## Cost / time guardrails

**New per-run cost:**

- **+1 Haiku call** (ExamPattern), ~5–10K input + ~3K output tokens.
- **+N Haiku calls** (Researcher per-day, parallel), ~5–10K input + ~6K output each. For a 14-day plan: ~14 extra Haiku calls.

With prompt caching on the corpus block, marginal input cost after the first call is small.

**Speed-preset compatibility:**

- `BART_SKIP_RESEARCHER=1` env var disables the per-day Researcher path (graceful degrade to brief-only). `--turbo` and `--fast` set this automatically.
- `BART_SKIP_EXAM_PATTERN=1` env var disables ExamPattern. Set by `--turbo` only.
- `--no-critic` works unchanged.

**Wall-clock impact:**

- Default mode: **+15–25%**. Per-day Researcher calls run in parallel (`max_parallel`), so the additional latency is dominated by the slowest Haiku call, not the sum.
- `--fast`: **0%** (skipped).
- `--turbo`: **0%** (skipped).

## Testing

New tests under `tests/`:

- **`test_corpus_three_slice.py`** — build a 60K-char fake file with a 400K total budget across 4 files, assert head + middle + tail markers all present and total length ≤ `per_file_cap`. Verify the marker text format.
- **`test_exam_pattern.py`** — fixture corpus with a fake past exam, mock the LLM to return canned JSON, assert `format_for_practice_exam` and `format_for_daily_drill(topics)` produce expected markdown blocks. Verify schema-failure fallback returns the empty shape and logs a warning.
- **`test_distiller_prompt.py`** — string-level test: verify `prompts/distiller.md` contains the literal "Verbatim anchors" section spec (catches accidental prompt regressions).
- **`test_author_wiring.py`** — unit-test that `AuthorAgent.write` correctly appends `research_slice` and `exam_patterns_block` to the user message when provided, and omits them when empty.

Existing smoke tests stay green — no signature changes to public APIs (only added optional kwargs).

## File-by-file checklist

| File | Change |
|---|---|
| `bart/io/corpus.py` | `build_corpus`: head/tail → head+middle+tail; truncation marker text. |
| `bart/agents/distiller.py` | `max_tokens` 2000→6000; user-message text update. |
| `prompts/distiller.md` | Add "Verbatim anchors" section spec; raise word ceiling 800→2500; drop 5-subtopic cap. |
| `bart/agents/researcher.py` | `max_tokens` 4000→6000. |
| `prompts/researcher.md` | Demand verbatim excerpts. |
| `bart/agents/exam_pattern.py` | NEW. `ExamPatternAgent` + `format_for_practice_exam` + `format_for_daily_drill`. |
| `prompts/exam_pattern.md` | NEW. Prompt for past-exam structural extraction. |
| `bart/agents/__init__.py` | Export `ExamPatternAgent`. |
| `bart/agents/author.py` | `write()` gains `research_slice` and `exam_patterns_block` kwargs; user message wiring. |
| `bart/orchestrator.py` | Sidecar pool 2→3 workers (add ExamPattern); per-day Researcher loop with cache; pass new kwargs to Author for daily-lesson and practice-exam. Honor `BART_SKIP_RESEARCHER` / `BART_SKIP_EXAM_PATTERN`. |
| `bart/commands.py` (or wherever `--fast` / `--turbo` set env) | Set the two new skip env vars under those presets. |
| `tests/test_corpus_three_slice.py` | NEW. |
| `tests/test_exam_pattern.py` | NEW. |
| `tests/test_distiller_prompt.py` | NEW. |
| `tests/test_author_wiring.py` | NEW. |

## Open questions

None — all decisions resolved during brainstorming:

- Slicing strategy: head + middle + tail, 400K total budget retained.
- Daily-lesson Author model: Opus (`primary_model`) unchanged.
- Distiller ceiling: 2500 words, no subtopic cap, with verbatim-anchors section.
- ExamPattern is a sidecar (not folded into Distiller) so it's parallelizable and independently disable-able.
- Researcher reactivated per-day, parallel, with disk cache.
