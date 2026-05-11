"""Local-mode chunked variants of the four full-corpus agents.

Why this module exists
----------------------

The default DistillerAgent / ProblemIndexerAgent / ExamPatternAgent /
ResearcherAgent each pack the entire corpus (up to 400K chars / ~100K
tokens) into a single LLM call. That works on Anthropic Opus / Sonnet
where the context window is huge and prompt caching makes repeats cheap.
It is fatal on Gemma 3 1B–4B running through Ollama on a laptop:

  - The KV cache for a 100K-token context is several GB on top of the
    model weights, and the kernel OOM-kills the Ollama daemon mid-call.
  - Even when memory holds, attention is O(n²); the call hangs for tens
    of minutes with no useful output.

The fix is structural, not a knob. In local mode we run each of the four
agents as a per-file map-reduce: one small LLM call per source file (or
per chunk of a large file), with each call sized to fit comfortably
inside an 8K-token window. Results are merged in plain Python — no
second LLM merge call is needed because the outputs all have stable
schemas (markdown sections / JSON arrays / JSON dicts).

Net effect for local mode:
  - No call ever sees more than one file's worth of text (~6K chars
    typical, capped at ~16K).
  - num_ctx stays at 8192, comfortable on a 6GB-available machine.
  - Total token volume across the run is comparable to (often less than)
    the single-call version, because each chunk is small.
  - Per-file calls are independent → trivially parallelizable later.

This module is deliberately stateless free functions, not Agent classes.
Agent classes presume a single `corpus_block` in their context — the
exact shape we're trying to avoid here. The orchestrator branches on
`cfg.auth_mode == "local"` and calls these helpers instead of
constructing the corresponding Agent instances.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from ..config import Config
from ..io.corpus import ExtractedFile
from ..llm import LLMClient
from ..prompts import load_prompt
from .base import extract_json


# Per-call input cap (chars). 6000 chars ≈ 1500 tokens — fits inside an
# 8K-token num_ctx with comfortable headroom for system prompt + output.
_CHUNK_CHARS = 6000

# Soft upper bound on number of chunks we'll process per agent. Chemistry
# corpora can exceed 100 chunks if every PS gets sliced; processing all
# of them serially on a 1B model is a 30-minute affair. The cap keeps
# wall-time bounded; the orchestrator can warn when we hit it.
_MAX_CHUNKS = 60


def _chunk_file(ef: ExtractedFile, chars: int = _CHUNK_CHARS) -> list[tuple[str, str]]:
    """Split a single file's text into bounded chunks.

    Returns a list of (label, text) where label is "rel_path" for a
    single-chunk file and "rel_path[i/N]" for a multi-chunk file. The
    label flows into the LLM prompt so the model can cite the source.
    """
    text = ef.text or ""
    if not text.strip():
        return []
    if len(text) <= chars:
        return [(ef.rel_path, text)]
    out: list[tuple[str, str]] = []
    n = (len(text) + chars - 1) // chars
    for i in range(n):
        start = i * chars
        end = min(start + chars, len(text))
        out.append((f"{ef.rel_path}[{i+1}/{n}]", text[start:end]))
    return out


def _all_chunks(files: Iterable[ExtractedFile]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ef in files:
        if ef.skipped:
            continue
        out.extend(_chunk_file(ef))
        if len(out) >= _MAX_CHUNKS:
            break
    return out[:_MAX_CHUNKS]


# ─────────────────────────────────────────────────────────────────────
# Distiller — per-file mini-brief, then string-merge
# ─────────────────────────────────────────────────────────────────────

_DISTILL_SYSTEM = (
    "You produce a tight, structured per-file digest the downstream "
    "planner uses in place of the raw corpus. Be precise on structure "
    "(topics, notation, problems). Brutally concise on prose. Quote "
    "verbatim only the strings the downstream agents need to copy "
    "(notation, definitions, problem stems). Cite the source filename "
    "for every verbatim quote."
)

_DISTILL_USER_TEMPLATE = (
    "Source file: {label}\n\n"
    "FILE TEXT\n---\n{body}\n---\n\n"
    "Produce a digest with EXACTLY these markdown sections, omitting any "
    "section that does not apply to this file:\n\n"
    "### Inventory\n"
    "One bullet: filename + a one-line description of what this file is.\n\n"
    "### Topics\n"
    "Bulleted list of subtopics this file covers. Use the file's own "
    "chapter / section labels when present.\n\n"
    "### Notation\n"
    "1-3 bullets on distinctive notation introduced or used in this file.\n\n"
    "### Problems\n"
    "Bulleted list of problems / worked examples in this file, using "
    "the file's own naming (e.g. \"PS5 P3\", \"Wk7 Q2\"). One line each.\n\n"
    "### Verbatim anchors\n"
    "2-5 bullets, each in this format:\n"
    "  > \"<verbatim string>\" — {label}\n"
    "Quote the strings downstream agents will want to copy: a "
    "definition, a notation legend, an instruction stem.\n"
)


def chunked_distill(
    llm: LLMClient,
    cfg: Config,
    files: list[ExtractedFile],
    on_progress=None,
) -> str:
    """Per-file map → string-concat reduce. Returns the same shape of
    brief that DistillerAgent.distill() returns, so downstream code is
    unchanged."""
    chunks = _all_chunks(files)
    parts: list[str] = []
    parts.append(f"# Corpus brief — {cfg.subject}\n")
    parts.append(
        f"Generated by per-file digest ({len(chunks)} chunk(s) across "
        f"{len(files)} file(s)). Sections concatenated below.\n"
    )
    for i, (label, body) in enumerate(chunks, 1):
        if on_progress:
            on_progress(i, len(chunks), label)
        user_text = _DISTILL_USER_TEMPLATE.format(label=label, body=body)
        try:
            piece = llm.complete(
                model=cfg.fast_model,
                system=_DISTILL_SYSTEM,
                user=user_text,
                max_tokens=1200,
                label=f"distiller_local:{label}",
                temperature=0.2,
            )
        except Exception as e:  # noqa: BLE001
            piece = f"_(distill failed for {label}: {type(e).__name__})_"
        parts.append(f"\n## {label}\n\n{piece.strip()}\n")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Problem index — per-file partial JSON arrays, concatenated
# ─────────────────────────────────────────────────────────────────────

_PROBLEM_INDEX_SYSTEM = (
    "You produce a JSON array indexing every distinctive problem and "
    "worked example in a single corpus file. Use the file's own naming "
    "for ids (\"PS5 P3\", \"Wk7 Q2\", \"Mock §III.5\"). Never invent "
    "problems not in the file."
)

_PROBLEM_INDEX_USER_TEMPLATE = (
    "Source file: {label}\n\n"
    "FILE TEXT\n---\n{body}\n---\n\n"
    "Output ONE fenced ```json block, nothing else. Schema:\n"
    "[\n"
    "  {{\"id\": \"<corpus naming>\", \"statement\": \"<one-line>\", "
    "\"topics\": [\"<chapter or subtopic>\"]}}\n"
    "]\n"
    "Cap at 12 entries for this single file."
)


def chunked_problem_index(
    llm: LLMClient,
    cfg: Config,
    files: list[ExtractedFile],
    on_progress=None,
) -> list[dict[str, Any]]:
    """Per-file map → array-concat reduce."""
    chunks = _all_chunks(files)
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, (label, body) in enumerate(chunks, 1):
        if on_progress:
            on_progress(i, len(chunks), label)
        user_text = _PROBLEM_INDEX_USER_TEMPLATE.format(label=label, body=body)
        try:
            text = llm.complete(
                model=cfg.fast_model,
                system=_PROBLEM_INDEX_SYSTEM,
                user=user_text,
                max_tokens=1500,
                label=f"problem_indexer_local:{label}",
                temperature=0.1,
                response_format="json",
            )
        except Exception:  # noqa: BLE001
            continue
        data = extract_json(text, expect="array")
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("id", "")).strip()
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            out.append({
                "id": pid,
                "statement": str(entry.get("statement", "")),
                "topics": [
                    str(t) for t in entry.get("topics", []) if t
                ] if isinstance(entry.get("topics"), list) else [],
            })
            if len(out) >= 80:  # mirror the original cap
                return out
    return out


# ─────────────────────────────────────────────────────────────────────
# Exam pattern — per-file partial dicts, merged
# ─────────────────────────────────────────────────────────────────────

_EXAM_PATTERN_SYSTEM = (
    "You scan ONE corpus file for past-exam, midterm, or sample-test "
    "material and emit a partial pattern record. If the file has none, "
    "emit empty arrays. Never invent problems."
)

_EXAM_PATTERN_USER_TEMPLATE = (
    "Source file: {label}\n\n"
    "FILE TEXT\n---\n{body}\n---\n\n"
    "Output ONE fenced ```json block, nothing else. Schema:\n"
    "{{\n"
    "  \"problems\": [\n"
    "    {{\"stem\": \"<verbatim, ≤300 chars>\", \"source_file\": "
    "\"{label}\", \"type\": \"mechanism|synthesis|proof|derivation|"
    "computation|short-answer|multiple-choice|free-response\", "
    "\"points\": <int or null>, \"difficulty\": \"low|medium|high\", "
    "\"topics\": [\"<topic>\"]}}\n"
    "  ],\n"
    "  \"structure\": {{\"total_points\": <int or null>, "
    "\"section_breakdown\": [{{\"section\": \"<label>\", \"count\": <int>, "
    "\"points\": <int>}}], \"common_types\": [\"<types>\"]}},\n"
    "  \"style_notes\": [\"<≤3 bullets observed in this file>\"]\n"
    "}}\n"
    "Cap problems at 8 for this single file. Empty arrays if no exam material."
)


def chunked_exam_pattern(
    llm: LLMClient,
    cfg: Config,
    files: list[ExtractedFile],
    on_progress=None,
) -> dict[str, Any]:
    """Per-file map → dict-merge reduce."""
    chunks = _all_chunks(files)
    merged: dict[str, Any] = {"problems": [], "structure": {}, "style_notes": []}
    section_breakdown: list[dict[str, Any]] = []
    common_types: set[str] = set()
    total_points_seen: list[int] = []
    style_notes_seen: set[str] = set()

    for i, (label, body) in enumerate(chunks, 1):
        if on_progress:
            on_progress(i, len(chunks), label)
        user_text = _EXAM_PATTERN_USER_TEMPLATE.format(label=label, body=body)
        try:
            text = llm.complete(
                model=cfg.fast_model,
                system=_EXAM_PATTERN_SYSTEM,
                user=user_text,
                max_tokens=1500,
                label=f"exam_pattern_local:{label}",
                temperature=0.1,
                response_format="json",
            )
        except Exception:  # noqa: BLE001
            continue
        data = extract_json(text, expect="object")
        if not isinstance(data, dict):
            continue
        for p in (data.get("problems") or []):
            if not isinstance(p, dict):
                continue
            stem = str(p.get("stem", "")).strip()
            if not stem:
                continue
            merged["problems"].append({
                "stem": stem,
                "source_file": str(p.get("source_file", label)),
                "type": str(p.get("type", "")),
                "points": p.get("points") if isinstance(p.get("points"), int) else None,
                "difficulty": str(p.get("difficulty", "")),
                "topics": [str(t) for t in p.get("topics", []) if t]
                          if isinstance(p.get("topics"), list) else [],
            })
            if len(merged["problems"]) >= 40:
                break
        s = data.get("structure") or {}
        if isinstance(s, dict):
            tp = s.get("total_points")
            if isinstance(tp, int):
                total_points_seen.append(tp)
            for sec in (s.get("section_breakdown") or []):
                if isinstance(sec, dict):
                    section_breakdown.append(sec)
            for ct in (s.get("common_types") or []):
                if ct:
                    common_types.add(str(ct))
        for note in (data.get("style_notes") or []):
            if note and str(note) not in style_notes_seen:
                style_notes_seen.add(str(note))
                merged["style_notes"].append(str(note))
        if len(merged["problems"]) >= 40:
            break

    if total_points_seen or section_breakdown or common_types:
        merged["structure"] = {
            "total_points": max(total_points_seen) if total_points_seen else None,
            "section_breakdown": section_breakdown,
            "common_types": sorted(common_types),
        }
    if not merged["style_notes"]:
        merged["style_notes"] = ["No past-exam material in corpus."]
    return merged


# ─────────────────────────────────────────────────────────────────────
# Researcher — per-(relevant)-file mini-brief, concatenated
# ─────────────────────────────────────────────────────────────────────

_RESEARCH_SYSTEM = (
    "You extract verbatim passages from one corpus file that are "
    "relevant to a given topic. Quote definitions and theorems exactly. "
    "Do not paraphrase. Skip silently if the file has nothing relevant."
)

_RESEARCH_USER_TEMPLATE = (
    "Subject: {subject}\n"
    "Topic: {topic}\n"
    "Objectives:\n- {objectives}\n\n"
    "Source file: {label}\n"
    "FILE TEXT\n---\n{body}\n---\n\n"
    "If this file has nothing on the topic, output exactly: NO MATCH\n\n"
    "Otherwise output a tight markdown excerpt from this file ONLY:\n"
    "1. **Definitions** (blockquoted, verbatim, cite {label}).\n"
    "2. **Theorems / formulas** (blockquoted, verbatim, cite {label}).\n"
    "3. **Example stems** (blockquoted, verbatim, cite {label}).\n"
    "4. **Notation observed** (1-2 bullets).\n"
    "≤500 words. No commentary."
)


def chunked_research(
    llm: LLMClient,
    cfg: Config,
    files: list[ExtractedFile],
    topic: str,
    learning_objectives: list[str],
    on_progress=None,
) -> str:
    """Per-(relevant)-file map → markdown-concat reduce.

    All files are tried; each call returns "NO MATCH" cheaply when the
    file is irrelevant, so we don't need a separate retrieval step. On a
    1B model a NO-MATCH call is ~5 tokens out, ~1500 in — negligible.
    """
    objectives = "\n- ".join(learning_objectives or ["(none specified)"])
    chunks = _all_chunks(files)
    parts: list[str] = []
    for i, (label, body) in enumerate(chunks, 1):
        if on_progress:
            on_progress(i, len(chunks), label)
        user_text = _RESEARCH_USER_TEMPLATE.format(
            subject=cfg.subject, topic=topic, objectives=objectives,
            label=label, body=body,
        )
        try:
            piece = llm.complete(
                model=cfg.fast_model,
                system=_RESEARCH_SYSTEM,
                user=user_text,
                max_tokens=900,
                label=f"researcher_local:{topic[:24]}:{label}",
                temperature=0.2,
            )
        except Exception:  # noqa: BLE001
            continue
        if not piece or "NO MATCH" in piece.upper()[:32]:
            continue
        parts.append(f"### From {label}\n\n{piece.strip()}\n")
    if not parts:
        return f"_(no corpus content matched topic “{topic}”)_"
    return f"# Research brief — {topic}\n\n" + "\n".join(parts)
