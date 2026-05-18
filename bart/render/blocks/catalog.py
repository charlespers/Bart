"""Agent-facing block catalog — generated from ``block_schemas`` (the single
source of truth) so what the Author sees can never drift from what
``validate_blocks`` checks or what the renderers accept.

``render_block_catalog(only=...)`` produces a compact markdown catalog of
block names + JSON schemas; ``catalog_for(artifact_kind, subject)`` returns
the slim per-artifact subset (with chem / ML / CS / phil / math / lit domain
variants picked off the subject keywords) so per-day briefs stay short.

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

from ._registry import BLOCK_NAMES


# ─── Documentation generator — used by prompts to advertise blocks ──


def render_block_catalog(only: list[str] | None = None) -> str:
    """Produce a compact markdown catalog of blocks + JSON schemas.

    The catalog is *generated* from `block_schemas.BLOCK_SCHEMAS` — the single
    source of truth — so the agent-facing text can never drift from what
    `validate_blocks()` checks or what the renderers accept.

    `only`: if provided, restrict the catalog to that set of fence names.
    Used to keep per-artifact briefs slim — sending all schemas to a
    daily-lesson Author is wasted tokens and wasted streaming time.
    """
    from ..block_schemas import BLOCK_SCHEMAS, catalog_entry

    names = [
        n for n in BLOCK_NAMES
        if n in BLOCK_SCHEMAS and (only is None or n in only)
    ]
    return "\n".join(catalog_entry(n) for n in names)


_CATALOG_BY_ARTIFACT = {
    "daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "multiple-choice",
        "mnemonic-card", "checkpoint", "fill-in-blank", "match-pairs",
    ],
    "schematics": [
        "concept-map", "concept-build", "formula-card", "comparison-matrix",
        "trap-callout", "process-ribbon", "flowchart", "anatomy-diagram",
        "fig-caption",
    ],
    "whimsical_notes": [
        "mnemonic-card", "trap-callout", "tag", "stamp",
    ],
    "short_study_guide": [
        "trap-callout", "quick-check", "checkpoint", "formula-card", "tag",
    ],
    "practice_exam_part_a": [
        "multi-step", "multiple-choice", "build-equation", "match-pairs",
        "drag-order", "estimate-range", "fill-in-blank", "tag",
    ],
    # Chemistry-flavored variants — used when subject keywords match chem
    # (orchestrator picks the right kind via _CHEM_KEYWORDS).
    "chem_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "molecule-diagram", "reaction-equation", "arrow-pushing",
        "energy-diagram", "orbital-diagram", "ph-scale", "periodic-snippet",
        "balance-equation", "isomer-spotter", "titration-curve",
        "electron-config",
    ],
    # Domain packs — keyed off subject keywords (see helpers below).
    "ml_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "confusion-matrix", "loss-curve", "neural-net-diagram",
        "attention-matrix", "embedding-scatter",
    ],
    "cs_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "code-block", "call-stack", "memory-layout", "binary-tree",
        "process-timeline",
    ],
    "phil_daily_lesson": [
        "why-it-matters", "concept-build", "trap-callout", "quick-check",
        "worked-example", "checkpoint",
        "argument-map", "truth-table", "venn-logic", "dialectic-tree",
        "quote-pull",
    ],
    "math_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "proof-block", "proof-ladder", "matrix-view", "graph-plot",
        "integral-area", "number-line",
    ],
    "lit_daily_lesson": [
        "why-it-matters", "concept-build", "trap-callout", "quick-check",
        "checkpoint", "annotated-quote",
        "passage-annotated", "character-graph", "theme-weave",
        "style-spectrum", "quote-pull", "timeline",
    ],
    "physics_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "graph-plot", "number-line", "integral-area", "comparison-matrix",
        "process-ribbon", "anatomy-diagram",
    ],
    "bio_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "checkpoint",
        "anatomy-diagram", "concept-map", "process-ribbon", "flowchart",
        "timeline", "comparison-matrix",
    ],
    "econ_daily_lesson": [
        "why-it-matters", "concept-build", "formula-card", "trap-callout",
        "quick-check", "worked-example", "multi-step", "checkpoint",
        "graph-plot", "comparison-matrix", "number-line", "process-ribbon",
    ],
}


_CHEM_KEYWORDS = (
    "chem", "organic", "inorganic", "biochem", "pchem", "kinetic",
    "thermodynamic",
)


_ML_KEYWORDS = (
    "machine learning", "ml", " ai", "deep learning", "neural", "nlp",
    "natural language", "computer vision", "cv ", "data science",
    "statistics learning",
)


_CS_KEYWORDS = (
    "operating system", "compilers", "systems", "computer architecture",
    "computer science", "data structures", "algorithms", "networking",
    "databases", "distributed", "concurrency",
)


_PHIL_KEYWORDS = (
    "philosophy", "ethics", "logic", "epistemology", "metaphysics",
    "phenomenology", "philos",
)


_MATH_KEYWORDS = (
    "calculus", "linear algebra", "real analysis", "topology", "geometry",
    "number theory", "discrete math", "abstract algebra", "differential",
    "math ", "mathematics",
)


_LIT_KEYWORDS = (
    "literature", "lit ", "comp lit", "comparative lit", "fiction",
    "poetry", "drama", "shakespeare", "novel",
)


_PHYSICS_KEYWORDS = (
    "physics", "mechanics", "kinematic", "dynamics", "thermodynamic",
    "electromagnet", "electricity", "magnetism", "optics", "quantum",
    "relativity", "astrophys", "statics",
)


_BIO_KEYWORDS = (
    "biology", "bio ", "molecular bio", "cell bio", "genetics", "genomic",
    "physiology", "anatomy", "ecology", "evolution", "microbio",
    "neuroscience", "botany", "zoology", "immunolog",
)


_ECON_KEYWORDS = (
    "economic", "econ ", "microeconom", "macroeconom", "econometric",
    "finance", "financial", "trade", "monetary", "fiscal",
)


def is_chem_subject(subject: str) -> bool:
    return _matches(subject, _CHEM_KEYWORDS)


def _matches(subject: str, kws: tuple[str, ...]) -> bool:
    s = " " + (subject or "").lower() + " "
    return any(k in s for k in kws)


def _domain_kind(subject: str) -> str | None:
    """Return the domain prefix of a daily_lesson catalog when the subject
    keywords match. Order matters — most specific first."""
    if _matches(subject, _CHEM_KEYWORDS):
        return "chem"
    if _matches(subject, _BIO_KEYWORDS):
        return "bio"
    if _matches(subject, _PHYSICS_KEYWORDS):
        return "physics"
    if _matches(subject, _ECON_KEYWORDS):
        return "econ"
    if _matches(subject, _ML_KEYWORDS):
        return "ml"
    if _matches(subject, _CS_KEYWORDS):
        return "cs"
    if _matches(subject, _PHIL_KEYWORDS):
        return "phil"
    if _matches(subject, _MATH_KEYWORDS):
        return "math"
    if _matches(subject, _LIT_KEYWORDS):
        return "lit"
    return None


# Artifact kinds that may carry generated figures. The `bart-figure` block
# is advertised to the Author for these — but only when image generation is
# actually enabled for the run, so a no-image run never tells the Author to
# request figures it can't produce.
_FIGURE_ARTIFACTS = frozenset({
    "daily_lesson", "schematics", "short_study_guide",
})


def catalog_for(artifact_kind: str, subject: str = "") -> str:
    """Slim catalog appropriate for the given artifact_kind.

    When `subject` matches a domain keyword set (chem / bio / physics / econ
    / ml / cs / phil / math / lit), daily-lesson catalogs include that
    domain's components in addition to the generic ones.

    The `bart-figure` block is appended for figure-friendly artifacts when
    image generation is enabled for this run (see ``bart.imagegen``).
    """
    if artifact_kind == "daily_lesson":
        domain = _domain_kind(subject)
        key = f"{domain}_daily_lesson" if domain else "daily_lesson"
        only = _CATALOG_BY_ARTIFACT.get(key) or _CATALOG_BY_ARTIFACT["daily_lesson"]
    else:
        only = _CATALOG_BY_ARTIFACT.get(artifact_kind)

    if only is not None and artifact_kind in _FIGURE_ARTIFACTS:
        try:
            from ...imagegen import image_generation_enabled
            if image_generation_enabled() and "figure" not in only:
                only = list(only) + ["figure"]
        except Exception:  # noqa: BLE001 — figures are optional; never block
            pass
    return render_block_catalog(only=only)

