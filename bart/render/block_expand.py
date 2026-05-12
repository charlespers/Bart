"""Block expansion — turn ```bart-<name> JSON fences into design-library HTML.

This is the bridge between agent output (markdown) and the design system
(`lib_blocks.py`). The Author is instructed to emit blocks like:

    ```bart-formula-card
    {"tex": "E = mc^2", "title": "Mass-energy", "legend": [...]}
    ```

This module finds those fences, decodes the JSON payload, calls the
matching `lib_blocks` function, and substitutes the rendered HTML in
place of the fence — *before* markdown rendering. The HTML survives
the markdown pass because it's wrapped as a raw HTML block (preceded
and followed by blank lines so python-markdown leaves it alone).

If a block is malformed (bad JSON, unknown name, missing required
arg), we leave a visible inline warning rather than failing the build.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from . import lib_blocks
from . import lib_blocks_packs as lbp


# Map of fence-name -> (lib_blocks function, expected kwargs).
# Kebab-case fence names map to snake_case Python functions.
_BLOCK_REGISTRY: dict[str, Callable[..., str]] = {
    # Primitives
    "stamp":             lib_blocks.stamp,
    "tag":               lib_blocks.tag,
    "paper-rule":        lib_blocks.paper_rule,
    "marginalia":        lib_blocks.marginalia,
    "fig-caption":       lib_blocks.fig_caption,
    # Visuals (originals)
    "formula-card":      lib_blocks.formula_card,
    "worked-example":    lib_blocks.worked_example,
    "mnemonic-card":     lib_blocks.mnemonic_card,
    "trap-callout":      lib_blocks.trap_callout,
    "why-it-matters":    lib_blocks.why_it_matters,
    "flowchart":         lib_blocks.flowchart,
    "concept-build":     lib_blocks.concept_build,
    # Visuals (extras)
    "timeline":          lib_blocks.timeline,
    "concept-map":       lib_blocks.concept_map,
    "comparison-matrix": lib_blocks.comparison_matrix,
    "process-ribbon":    lib_blocks.process_ribbon,
    "anatomy-diagram":   lib_blocks.anatomy_diagram,
    "number-line":       lib_blocks.number_line,
    "proof-ladder":      lib_blocks.proof_ladder,
    "annotated-quote":   lib_blocks.annotated_quote,
    # Interactives (originals)
    "quick-check":       lib_blocks.quick_check,
    "multi-step":        lib_blocks.multi_step_problem,
    "multiple-choice":   lib_blocks.multiple_choice,
    "hotspots":          lib_blocks.hotspots,
    "checkpoint":        lib_blocks.checkpoint,
    # Interactives (extras)
    "drag-order":        lib_blocks.drag_order,
    "fill-in-blank":     lib_blocks.fill_in_blank,
    "match-pairs":       lib_blocks.match_pairs,
    "parameter-slider":  lib_blocks.parameter_slider,
    "build-equation":    lib_blocks.build_equation,
    "estimate-range":    lib_blocks.estimate_range,
    "confidence-poll":   lib_blocks.confidence_poll,
    # Chem visuals
    "molecule-diagram":  lib_blocks.molecule_diagram,
    "reaction-equation": lib_blocks.reaction_equation,
    "arrow-pushing":     lib_blocks.arrow_pushing,
    "energy-diagram":    lib_blocks.energy_diagram,
    "orbital-diagram":   lib_blocks.orbital_diagram,
    "ph-scale":          lib_blocks.ph_scale,
    "periodic-snippet":  lib_blocks.periodic_snippet,
    # Chem interactives
    "balance-equation":  lib_blocks.balance_equation,
    "isomer-spotter":    lib_blocks.isomer_spotter,
    "titration-curve":   lib_blocks.titration_curve,
    "electron-config":   lib_blocks.electron_config,
    # ML pack
    "confusion-matrix":  lbp.confusion_matrix,
    "loss-curve":        lbp.loss_curve,
    "neural-net-diagram": lbp.neural_net_diagram,
    "attention-matrix":  lbp.attention_matrix,
    "embedding-scatter": lbp.embedding_scatter,
    # CS pack
    "code-block":        lbp.code_block,
    "call-stack":        lbp.call_stack,
    "memory-layout":     lbp.memory_layout,
    "binary-tree":       lbp.binary_tree,
    "process-timeline":  lbp.process_timeline,
    # Phil pack
    "argument-map":      lbp.argument_map,
    "truth-table":       lbp.truth_table,
    "venn-logic":        lbp.venn_logic,
    "dialectic-tree":    lbp.dialectic_tree,
    "quote-pull":        lbp.quote_pull,
    # Math pack
    "proof-block":       lbp.proof_block,
    "matrix-view":       lbp.matrix_view,
    "graph-plot":        lbp.graph_plot,
    "integral-area":     lbp.integral_area,
    # Lit pack
    "passage-annotated": lbp.passage_annotated,
    "character-graph":   lbp.character_graph,
    "theme-weave":       lbp.theme_weave,
    "style-spectrum":    lbp.style_spectrum,
}


# Public alias-set; agents reference these names in fences.
BLOCK_NAMES = sorted(_BLOCK_REGISTRY.keys())


@dataclass
class ExpansionWarning:
    kind: str           # 'unknown_block' | 'bad_json' | 'render_error'
    detail: str
    block_name: str = ""


@dataclass
class ExpansionResult:
    text: str                           # markdown with fences replaced by HTML
    counts: dict[str, int]              # how many times each block was used
    warnings: list[ExpansionWarning]


# Match ```bart-<name>\n<body>``` fences. The body is anything up to the
# closing fence (which may sit immediately after a newline or after the
# language tag if the body is empty). Non-greedy + DOTALL.
_FENCE_RE = re.compile(
    r"(?ms)^```bart-([a-z0-9-]+)[^\n]*\n(.*?)```\s*$"
)


def expand_blocks(markdown_text: str) -> ExpansionResult:
    """Scan `markdown_text` for `bart-*` fences and replace them with HTML.

    The replacement HTML is wrapped with blank lines and a sentinel comment
    so python-markdown doesn't try to "tidy" it. Inline math inside the
    rendered HTML still goes through KaTeX at view time.
    """
    counts: dict[str, int] = {}
    warnings: list[ExpansionWarning] = []

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        body = m.group(2).strip()
        fn = _BLOCK_REGISTRY.get(name)
        if fn is None:
            warnings.append(ExpansionWarning(
                "unknown_block", f"no registered block named 'bart-{name}'", name,
            ))
            return _inline_warning(name, "unknown block")

        # Allow zero-arg blocks (e.g. paper-rule with empty body).
        if not body:
            payload: dict[str, Any] = {}
        else:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as e:
                warnings.append(ExpansionWarning(
                    "bad_json", f"bart-{name}: {e.msg} at line {e.lineno}", name,
                ))
                return _inline_warning(name, f"JSON parse error: {e.msg}")

        if not isinstance(payload, dict):
            warnings.append(ExpansionWarning(
                "bad_json", f"bart-{name}: payload must be a JSON object", name,
            ))
            return _inline_warning(name, "payload must be a JSON object")

        try:
            html = fn(**payload)
        except TypeError as e:
            # The author's JSON either has unknown kwargs OR is missing
            # required ones. We recover from both:
            #   1. Drop unknown kwargs and retry with only accepted keys.
            #   2. Synthesize placeholder defaults for missing required
            #      keyword-only args (empty string for str, [] for list,
            #      {} for dict, 0 for numeric). The block renders with a
            #      "missing field" stamp instead of a blocking yellow stub
            #      that hides the entire question.
            import inspect as _inspect
            try:
                sig = _inspect.signature(fn)
                params = sig.parameters
                accepts_var_kw = any(
                    p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
                err_str = str(e)
                if not accepts_var_kw and "unexpected keyword argument" in err_str:
                    accepted = {k: v for k, v in payload.items() if k in params}
                    dropped = sorted(set(payload) - set(accepted))
                    # After dropping unknowns, the call may still fail because
                    # required kwargs are missing. Fill those with placeholders
                    # so the block renders rather than collapsing to a stub.
                    missing: list[str] = []
                    for pname, p in params.items():
                        if pname in accepted:
                            continue
                        if p.kind is _inspect.Parameter.KEYWORD_ONLY \
                                and p.default is _inspect.Parameter.empty:
                            missing.append(pname)
                            accepted[pname] = _placeholder_for(pname, p.annotation)
                    html = fn(**accepted)
                    if dropped:
                        warnings.append(ExpansionWarning(
                            "unknown_kwargs",
                            f"bart-{name}: dropped unknown kwargs {dropped} "
                            f"(rendered with the rest)",
                            name,
                        ))
                    if missing:
                        html = (
                            html
                            + f'<div class="b-block-stub-note" style="margin:'
                              f'8px 0 0;padding:6px 10px;font-size:11px;'
                              f'color:#b48a3c;background:#fff3cd;'
                              f'border-radius:4px;font-family:monospace">'
                              f'⚠ bart-{name}: filled missing field(s) '
                              f'{missing} with placeholder defaults — edit '
                              f'the source markdown to provide real values.'
                              f'</div>'
                        )
                        warnings.append(ExpansionWarning(
                            "missing_required_kwargs",
                            f"bart-{name}: filled missing required kwargs "
                            f"{missing} with defaults",
                            name,
                        ))
                elif "missing" in err_str and "required keyword-only argument" in err_str:
                    # Determine which required kwargs the author skipped, then
                    # supply a typed default so `fn()` succeeds. Render the
                    # block with a small caveat note appended.
                    missing: list[str] = []
                    for pname, p in params.items():
                        if pname in payload:
                            continue
                        if p.kind is _inspect.Parameter.KEYWORD_ONLY and p.default is _inspect.Parameter.empty:
                            missing.append(pname)
                            payload[pname] = _placeholder_for(pname, p.annotation)
                    html = fn(**payload)
                    if missing:
                        html = (
                            html
                            + f'<div class="b-block-stub-note" style="margin:'
                              f'8px 0 0;padding:6px 10px;font-size:11px;'
                              f'color:#b48a3c;background:#fff3cd;'
                              f'border-radius:4px;font-family:monospace">'
                              f'⚠ bart-{name}: filled missing field(s) '
                              f'{missing} with placeholder defaults — edit '
                              f'the source markdown to provide real values.'
                              f'</div>'
                        )
                        warnings.append(ExpansionWarning(
                            "missing_required_kwargs",
                            f"bart-{name}: filled missing required kwargs "
                            f"{missing} with defaults",
                            name,
                        ))
                else:
                    raise
            except Exception as e2:  # noqa: BLE001
                # Recovery itself failed — report the SECONDARY error so the
                # author sees the real shape problem (e.g. items being a list
                # of strings when the renderer expected list-of-dicts), not
                # the misleading original 'unexpected keyword argument' echo.
                warnings.append(ExpansionWarning(
                    "render_error",
                    f"bart-{name}: recovery failed: {type(e2).__name__}: {e2}",
                    name,
                ))
                return _inline_warning(name, f"{type(e2).__name__}: {e2}")
        except Exception as e:  # noqa: BLE001
            warnings.append(ExpansionWarning(
                "render_error", f"bart-{name}: {type(e).__name__}: {e}", name,
            ))
            return _inline_warning(name, f"{type(e).__name__}: {e}")

        counts[name] = counts.get(name, 0) + 1
        # Wrap so python-markdown treats it as a raw HTML block. The trailing
        # blank lines are critical — without them surrounding paragraphs can
        # absorb the HTML.
        return f"\n\n<!-- bart-{name} -->\n{html}\n<!-- /bart-{name} -->\n\n"

    new_text = _FENCE_RE.sub(_sub, markdown_text)
    return ExpansionResult(text=new_text, counts=counts, warnings=warnings)


def _inline_warning(name: str, detail: str) -> str:
    """Render a visible warning in place of a failed block. Avoids silent loss."""
    from html import escape
    return (
        f'\n\n<div class="bart-block-error" style="background:#fff3cd;'
        f'border:1px dashed #b48a3c;color:#6e4f10;padding:8px 12px;'
        f'border-radius:6px;font-family:monospace;font-size:13px;margin:12px 0">'
        f'<strong>bart-{escape(name)}</strong> failed: {escape(detail)}</div>\n\n'
    )


def _placeholder_for(field_name: str, annotation: Any) -> Any:
    """Synthesize a typed placeholder for a missing required kwarg.

    Inspects the parameter's type annotation when available; falls back to
    name heuristics (anything ending in `s` or with `list` in the name
    becomes `[]`; otherwise empty string). The placeholder lets the block
    render so the rest of the page is intact; an inline `⚠` note tells the
    reader (and the author) that a real value is needed.
    """
    import typing as _typing
    name_lc = field_name.lower()
    if annotation is not None and annotation is not type(None):
        origin = getattr(annotation, "__origin__", None)
        if origin in (list, _typing.List):
            return []
        if origin in (dict, _typing.Dict):
            return {}
        if annotation is int or annotation is float:
            return 0
        if annotation is bool:
            return False
    if name_lc.endswith("s") or "list" in name_lc or "items" in name_lc \
            or "choices" in name_lc or "steps" in name_lc \
            or "hints" in name_lc or "rows" in name_lc:
        return []
    if "count" in name_lc or "num" in name_lc or "size" in name_lc:
        return 0
    fallback = {
        "solution": "(solution to be provided)",
        "answer": "(answer to be provided)",
        "question": "(question text)",
        "problem": "(problem statement)",
        "tex": "?",
        "title": "",
        "body": "",
    }
    return fallback.get(name_lc, "(missing)")


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
    from .block_schemas import BLOCK_SCHEMAS, catalog_entry

    names = [
        n for n in BLOCK_NAMES
        if n in BLOCK_SCHEMAS and (only is None or n in only)
    ]
    return "\n".join(catalog_entry(n) for n in names)


# Per-artifact whitelists — keep briefs short.
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
}


# Subject-keyword detection for chemistry — when matched, we swap to the
# chem-flavored catalog so the model sees molecule/reaction/etc as available.
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


def catalog_for(artifact_kind: str, subject: str = "") -> str:
    """Slim catalog appropriate for the given artifact_kind.

    When `subject` matches a domain keyword set (chem / ml / cs / phil /
    math / lit), daily-lesson catalogs include that domain's components
    in addition to the generic ones.
    """
    if artifact_kind == "daily_lesson":
        domain = _domain_kind(subject)
        key = f"{domain}_daily_lesson" if domain else "daily_lesson"
        only = _CATALOG_BY_ARTIFACT.get(key) or _CATALOG_BY_ARTIFACT["daily_lesson"]
    else:
        only = _CATALOG_BY_ARTIFACT.get(artifact_kind)
    return render_block_catalog(only=only)
