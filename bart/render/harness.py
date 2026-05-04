"""Quality harness — coverage, fidelity, format.

Why this exists: bart's pre-existing harness ensures every artifact *renders*,
but never asks "did the packet actually review every chapter / lecture / topic
the user dropped in?" or "do the lessons cite real material from the corpus?"
This module closes that gap behind one entry point: `audit_run(run_dir)`.

Three layers, in order of cost:

  1. **Coverage** — for every "topic marker" (chapter heading, lecture title,
     section number) found in the original source files, verify it surfaces in
     at least one generated artifact's prose. Pure regex + substring match.
     Zero LLM calls. Sub-second on a 30-day plan.

  2. **Fidelity** — every `cite` payload in the rendered library blocks
     ('Ch 4 §2', 'Lecture 12', 'HW7 P3') is matched against a slug-set built
     from the corpus filenames + heading inventory. A cite that doesn't match
     anything is flagged as likely-hallucinated. Still pure regex.

  3. **Format** — delegates to the existing `format_audit.audit()` pipeline so
     a single command runs every check the project knows about.

Each layer returns its own findings; `audit_run` aggregates them into a
single `HarnessResult`. The driver (in commands.py) decides whether to
write JSON, fail-on-warn, or escalate to a Haiku judge for the slice of
findings that are ambiguous (the LLM gate is opt-in via a kwarg, off by
default to keep the harness free).

The harness is read-only — it does not modify any file on disk.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ─── Result types ─────────────────────────────────────────────────


@dataclass
class CoverageFinding:
    """A topic the source materials cover that no artifact mentions."""
    topic: str             # canonical text (e.g. "Chapter 4: Fourier Analysis")
    source: str            # rel-path of the file the topic was extracted from
    severity: str = "warn" # "warn" | "error"


@dataclass
class FidelityFinding:
    """A cite-payload in an artifact that doesn't match the corpus."""
    artifact: str          # rel-path of the lesson/schematic that emitted it
    cite: str              # the literal cite string
    severity: str = "warn"


@dataclass
class HarnessResult:
    coverage_findings: list[CoverageFinding] = field(default_factory=list)
    fidelity_findings: list[FidelityFinding] = field(default_factory=list)
    coverage_total: int = 0      # # of topic markers found in source
    coverage_hit: int = 0        # # that matched at least one artifact
    cites_total: int = 0
    cites_unmatched: int = 0
    format_summary: dict = field(default_factory=dict)

    @property
    def coverage_pct(self) -> float:
        if not self.coverage_total:
            return 100.0
        return 100.0 * self.coverage_hit / self.coverage_total

    @property
    def fidelity_pct(self) -> float:
        if not self.cites_total:
            return 100.0
        return 100.0 * (self.cites_total - self.cites_unmatched) / self.cites_total


# ─── Topic-marker extraction (source side) ────────────────────────

# Heading-style markers — Markdown headings, "Chapter N:", "Lecture N",
# "Lesson N", "Section N.M", "Module N", "Unit N", "Topic N" — anchored
# at start of line. We deliberately stay broad: false positives in the
# corpus are tolerable (the artifact-side match is stricter).
_TOPIC_PATTERNS = [
    re.compile(r"^\s*#{1,3}\s+(.{3,160}?)\s*$", re.MULTILINE),
    re.compile(r"^\s*(Chapter\s+\d+(?:\.\d+)?(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Lecture\s+\d+(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Lesson\s+\d+(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Module\s+\d+(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Unit\s+\d+(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Topic\s+\d+(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*(Section\s+\d+(?:\.\d+)?(?:\s*[:\-–—.]\s*[^\n]{1,140})?)",
               re.MULTILINE | re.IGNORECASE),
]

# Trim pure-numeric or one-word "topics" — they're too generic to track
# coverage on (a single "Introduction" appears everywhere).
_TOPIC_BLOCKLIST = {
    "introduction", "summary", "conclusion", "references", "preface",
    "appendix", "acknowledgments", "table of contents", "index",
    "bibliography", "abstract", "overview",
}
_MIN_TOPIC_LEN = 6


def extract_topic_markers(text: str, source_label: str) -> list[CoverageFinding]:
    """Return a deduplicated list of topic markers found in `text`.

    `source_label` is the rel-path of the source file (used for reporting).
    Findings start at severity=warn; the driver may upgrade to error based
    on density (e.g. >25% of topics missing).
    """
    found: dict[str, CoverageFinding] = {}
    for pat in _TOPIC_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1) if m.lastindex else m.group(0)
            topic = _normalize_topic(raw)
            if not topic:
                continue
            key = topic.lower()
            if key in _TOPIC_BLOCKLIST:
                continue
            if len(topic) < _MIN_TOPIC_LEN:
                continue
            # Skip extracted formulas / equations — when the PDF extractor
            # pulls a math line as a heading-like span, the result is mostly
            # non-ASCII glyphs (𝑜𝑓 𝑏𝑜𝑛𝑑𝑖𝑛𝑔 …) or symbol-heavy. Topic markers
            # are prose; require at least 60% of chars in the ASCII letter
            # range so an equation can't masquerade as a chapter title.
            ascii_letters = sum(1 for ch in topic if "a" <= ch.lower() <= "z")
            if ascii_letters / max(len(topic), 1) < 0.6:
                continue
            # Keep the first occurrence per canonical key.
            found.setdefault(key, CoverageFinding(topic=topic, source=source_label))
    return list(found.values())


def _normalize_topic(s: str) -> str:
    """Strip trailing punctuation, collapse whitespace, drop trailing page
    numbers like '… 47' or '… 12-14'."""
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    # Drop trailing page-number-ish runs.
    s = re.sub(r"[\s.\-–—]*\d{1,4}(?:\s*[-–]\s*\d{1,4})?\s*$", "", s)
    # Strip enclosing brackets / quotes.
    s = s.strip("·•●◦*-—–.:;,/\\")
    return s.strip()


# ─── Artifact-side matching ───────────────────────────────────────


def _build_artifact_index(run_dir: Path) -> str:
    """Concatenate every generated markdown artifact into one searchable
    blob. We match against markdown (not HTML) so cite strings inside
    `bart-*` JSON fences are still searchable.
    """
    parts: list[str] = []
    for p in sorted(run_dir.rglob("*.md")):
        # Skip the markdown/ subdirectory copies — they're identical to the
        # top-level files and would double-count matches.
        if "markdown/" in str(p.relative_to(run_dir)):
            continue
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return "\n\n".join(parts).lower()


def _topic_matches(topic: str, blob_lower: str) -> bool:
    """Heuristic: a topic is 'covered' iff its strongest token-set overlaps
    a contiguous span of the artifact blob.

    We match on the topic *with* the lead identifier ('Chapter 4', 'Lecture
    12') first — that's the lowest-noise signal. If absent, we fall back
    to a 3+ significant-token substring match.
    """
    t = topic.lower().strip()
    if not t:
        return False
    if t in blob_lower:
        return True
    # Pull the leading identifier ('chapter 4', 'ch 4', 'lecture 12', etc.).
    lead = re.match(
        r"(chapter\s+\d+(?:\.\d+)?|lecture\s+\d+|lesson\s+\d+|"
        r"module\s+\d+|unit\s+\d+|topic\s+\d+|section\s+\d+(?:\.\d+)?)",
        t,
    )
    if lead:
        lead_text = lead.group(1)
        compact = re.sub(r"chapter\s+", "ch ", lead_text)
        if compact in blob_lower or lead_text in blob_lower:
            return True
        # Also accept "Ch. 4" / "Ch.4".
        compact2 = re.sub(r"ch\s+", "ch.", compact)
        if compact2 in blob_lower:
            return True
    # Fall back: significant-token overlap.
    tokens = [
        w for w in re.split(r"\W+", t)
        if w and len(w) >= 4 and w not in {"chapter", "lecture", "lesson",
        "module", "unit", "topic", "section", "and", "the", "for", "with"}
    ]
    if len(tokens) < 3:
        # Two-token topics: require both tokens present within 100 chars
        # of each other to count.
        if len(tokens) == 2:
            for m in re.finditer(re.escape(tokens[0]), blob_lower):
                window = blob_lower[m.start(): m.start() + 200]
                if tokens[1] in window:
                    return True
        return False
    # 3+ tokens — require any 3 to co-occur in a 400-char window. Cheap,
    # precise enough.
    hits = [m.start() for m in re.finditer(re.escape(tokens[0]), blob_lower)]
    for h in hits[:50]:
        window = blob_lower[h: h + 400]
        if sum(1 for tok in tokens if tok in window) >= 3:
            return True
    return False


# ─── Cite extraction (artifact side) ──────────────────────────────

# Match `"cite": "<...>"` payloads in bart-* fences AND inline parentheticals
# of the form "(Ch 4 §2)" / "(Lecture 12)".
_CITE_JSON = re.compile(r'"cite"\s*:\s*"([^"]{2,160})"')
_CITE_INLINE = re.compile(
    r"\(\s*(Ch\.?\s*\d+(?:\s*§\s*\d+(?:\.\d+)?)?|Chapter\s+\d+|"
    r"Lecture\s+\d+|HW\s*\d+\s*(?:P|Problem)\s*\d+)\s*\)",
    re.IGNORECASE,
)


def extract_cites(text: str) -> list[str]:
    cites = list(_CITE_JSON.findall(text))
    cites += [m.group(1) for m in _CITE_INLINE.finditer(text)]
    return cites


def _cite_matches_corpus(cite: str, corpus_topic_keys: set[str],
                         corpus_filename_keys: set[str]) -> bool:
    """A cite is 'real' iff its leading identifier appears in the corpus
    topic-marker set OR the cite mentions a corpus filename token.

    Match strategy, in order:

      1. Direct substring match against any topic key — picks up
         "Chapter 4: Fourier Analysis" cited as "Ch 4".
      2. Number-anchor match — `Ch 14 §3` resolves to any topic key that
         contains "chapter 14" / "ch 14" / "ch.14".
      3. Filename token match — `Practice Exam 2 Q3` resolves when a corpus
         file is named `Practice Exam 2.pdf`.

    A cite that fails all three is reported as unmatched. False positives
    (cite content that legitimately doesn't appear in source) are tolerable
    here because the user's eye on the report decides whether to act.
    """
    c = cite.lower().strip()
    c_norm = re.sub(r"^ch\.?\s+(\d+)", r"chapter \1", c)
    c_norm = re.sub(r"§\s*\d+(?:\.\d+)?", "", c_norm).strip()
    c_norm = re.sub(r"\s+", " ", c_norm)

    # 1. Direct substring.
    for key in corpus_topic_keys:
        if c_norm and (c_norm in key or key in c_norm):
            return True

    # 2. Number-anchor — extract leading "chapter N" / "ch N" / etc. and
    #    accept if any topic key contains that anchor.
    anchor = re.match(
        r"(?:chapter|ch|lecture|lesson|module|unit|topic|section)\s+\d+",
        c_norm,
    )
    if anchor:
        a = anchor.group(0)
        for key in corpus_topic_keys:
            if a in key:
                return True
        for fn in corpus_filename_keys:
            if a in fn:
                return True

    # 3. Filename token match. Filenames are dash/underscore-tokenized; we
    #    require at least one filename token of length ≥ 5 to appear in
    #    the cite (so `q1` doesn't match a stray `q1` inside a filename).
    cite_tokens = {t for t in re.split(r"\W+", c_norm) if len(t) >= 4}
    for fn in corpus_filename_keys:
        if len(fn) >= 5 and fn in c_norm:
            return True
        fn_tokens = set(re.split(r"[_\-\s]+", fn))
        if len(cite_tokens & fn_tokens) >= 2:
            return True
    return False


# ─── Top-level driver ─────────────────────────────────────────────


def audit_run(
    run_dir: Path,
    *,
    materials: Iterable[Path] | None = None,
    include_format: bool = True,
) -> HarnessResult:
    """Run every harness layer over `run_dir` and return aggregate results.

    `materials`: paths of source files the corpus was built from. If None,
    we look for a `manifest.json` with `materials.files[].rel_path` entries
    and load them from `<repo_root>/materials/`. (The default path keeps
    callers simple: `audit_run(run_dir)` Just Works after a normal run.)

    `include_format`: when True, also runs the post-render format audit
    and merges its summary into the result.
    """
    result = HarnessResult()

    # ── Layer 1: coverage ─────────────────────────────────────
    source_topics: list[CoverageFinding] = []
    corpus_filename_keys: set[str] = set()

    sources = _resolve_source_paths(run_dir, materials)
    for src in sources:
        try:
            text = _read_textual(src)
        except Exception:
            continue
        if not text:
            continue
        source_topics.extend(extract_topic_markers(text, source_label=src.name))
        # Collect filename tokens for later cite matching.
        stem = src.stem.lower()
        corpus_filename_keys.add(stem)
        for tok in re.split(r"[_\-\s]+", stem):
            if len(tok) >= 4:
                corpus_filename_keys.add(tok)

    # Dedupe topics across sources by canonical key.
    by_key: dict[str, CoverageFinding] = {}
    for t in source_topics:
        by_key.setdefault(t.topic.lower(), t)
    topics = list(by_key.values())

    blob = _build_artifact_index(run_dir)
    for t in topics:
        if not _topic_matches(t.topic, blob):
            result.coverage_findings.append(t)
        else:
            result.coverage_hit += 1
    result.coverage_total = len(topics)

    # Promote severity to error if more than 25% of topics are uncovered.
    if result.coverage_total and len(result.coverage_findings) / result.coverage_total > 0.25:
        for f in result.coverage_findings:
            f.severity = "error"

    # ── Layer 2: cite fidelity ────────────────────────────────
    corpus_topic_keys = {t.topic.lower() for t in topics}
    for art in sorted(run_dir.glob("*.md")):
        if "markdown/" in str(art.relative_to(run_dir)):
            continue
        try:
            text = art.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(art.relative_to(run_dir))
        for cite in extract_cites(text):
            result.cites_total += 1
            if not _cite_matches_corpus(cite, corpus_topic_keys, corpus_filename_keys):
                result.fidelity_findings.append(
                    FidelityFinding(artifact=rel, cite=cite)
                )
                result.cites_unmatched += 1

    for d in (run_dir / "daily_lessons").glob("*.md") if (run_dir / "daily_lessons").exists() else []:
        try:
            text = d.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(d.relative_to(run_dir))
        for cite in extract_cites(text):
            result.cites_total += 1
            if not _cite_matches_corpus(cite, corpus_topic_keys, corpus_filename_keys):
                result.fidelity_findings.append(
                    FidelityFinding(artifact=rel, cite=cite)
                )
                result.cites_unmatched += 1

    # ── Layer 3: format ───────────────────────────────────────
    if include_format:
        try:
            from .format_audit import audit as format_audit
            fa = format_audit(run_dir, apply_fixes=False)
            result.format_summary = {
                "files_scanned": fa.files_scanned,
                "errors": len(fa.errors),
                "warnings": len(fa.warnings),
                "by_severity": fa.by_severity,
            }
        except Exception as e:  # noqa: BLE001
            result.format_summary = {"error": f"{type(e).__name__}: {e}"}

    return result


def _resolve_source_paths(run_dir: Path, materials: Iterable[Path] | None) -> list[Path]:
    if materials is not None:
        return list(materials)
    # Fall back to <project>/materials/ — same dir the run was built from.
    from ..paths import MATERIALS
    if not MATERIALS.exists():
        return []
    return sorted(
        p for p in MATERIALS.rglob("*")
        if p.is_file() and not p.name.startswith(".") and p.name != ".gitkeep"
    )


def _read_textual(path: Path) -> str:
    """Read text content from a source file using bart's extractor registry.

    For text-native files (.md, .txt) we read directly. For .pdf / .docx /
    .pptx we route through the same extractors the orchestrator used at
    run time. Failures yield empty string (the caller skips the file).
    """
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".rst", ".tex"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    try:
        from ..extractors import extract, supported
        if not supported(path):
            return ""
        text, _err = extract(path)
        return text or ""
    except Exception:
        return ""


# ─── Reporting ────────────────────────────────────────────────────


def write_report(run_dir: Path, result: HarnessResult) -> Path:
    """Persist the harness output to `<run_dir>/quality_audit.json`."""
    payload = {
        "run_id": run_dir.name,
        "coverage": {
            "total": result.coverage_total,
            "hit": result.coverage_hit,
            "pct": round(result.coverage_pct, 1),
            "missing": [
                {"topic": f.topic, "source": f.source, "severity": f.severity}
                for f in result.coverage_findings
            ],
        },
        "fidelity": {
            "cites_total": result.cites_total,
            "cites_unmatched": result.cites_unmatched,
            "pct": round(result.fidelity_pct, 1),
            "unmatched": [
                {"artifact": f.artifact, "cite": f.cite, "severity": f.severity}
                for f in result.fidelity_findings
            ],
        },
        "format": result.format_summary,
    }
    out = run_dir / "quality_audit.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
