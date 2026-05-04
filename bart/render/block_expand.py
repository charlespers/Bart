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
            # Likely an unknown-kwarg from the author agent. Re-attempt with
            # only the kwargs that the function's signature actually accepts;
            # log the unknowns as info, not as a render failure. This keeps
            # the page rendering when an author hallucinates a key like
            # `title=`/`prompt=`/`label=` that the renderer doesn't model yet.
            import inspect as _inspect
            try:
                sig = _inspect.signature(fn)
                params = sig.parameters
                accepts_var_kw = any(
                    p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
                if not accepts_var_kw and "unexpected keyword argument" in str(e):
                    accepted = {k: v for k, v in payload.items() if k in params}
                    dropped = sorted(set(payload) - set(accepted))
                    html = fn(**accepted)
                    if dropped:
                        warnings.append(ExpansionWarning(
                            "unknown_kwargs",
                            f"bart-{name}: dropped unknown kwargs {dropped} "
                            f"(rendered with the rest)",
                            name,
                        ))
                else:
                    raise
            except Exception:
                warnings.append(ExpansionWarning(
                    "render_error", f"bart-{name}: {e}", name,
                ))
                return _inline_warning(name, str(e))
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


# ─── Documentation generator — used by prompts to advertise blocks ──


def render_block_catalog(only: list[str] | None = None) -> str:
    """Produce a compact markdown catalog of blocks + JSON schemas.

    `only`: if provided, restrict the catalog to that set of fence names.
    Used to keep per-artifact briefs slim — sending all 31 schemas to a
    daily-lesson Author is wasted tokens and wasted streaming time.
    """
    # Hand-curated minimal schemas — keeps the prompt small. Each entry lists
    # required fields first; optional fields shown in parens.
    schemas = {
        "concept-build":     '{"name": "<concept name>", "motivate?": "<why this needs to exist>", "define?": "<formal statement>", "define_tex?": "<latex>", "example?": "<concrete numerical example prose>", "example_tex?": "<latex>", "connect?": "<tie to prior concept>", "contrast?": "<vs the closest neighbor concept students confuse it with>", "apply?": "<use it on a corpus problem>", "threshold?": true}',
        "formula-card":      '{"tex": "<latex>", "title?": "...", "legend?": [{"symbol":"x","meaning":"..."}], "note?": "...", "stamp_label?": "KEY", "cite?": "<corpus location, e.g. \'Lecture 4 §2\' or \'HW7 P3\'>"}',
        "worked-example":    '{"problem": "...", "steps": [{"action":"...", "math?":"<latex>", "reasoning?":"..."}], "answer?": "...", "tag_label?": "Worked example", "cite?": "<corpus location>"}',
        "quick-check":       '{"question": "...", "answer": "...", "hint?": "...", "label?": "Quick check"}',
        "multi-step":        '{"problem": "...", "hints": ["...", "..."], "solution": "...", "label?": "Practice problem"}',
        "multiple-choice":   '{"question": "...", "choices": [{"text":"...","correct":true,"explanation?":"..."}], "label?": "Multiple choice"}',
        "hotspots":          '{"image_html": "<svg>...</svg>", "spots": [{"x":50,"y":50,"label":"...","body?":"..."}], "caption?": "..."}',
        "checkpoint":        '{"cards": [{"front":"...","back":"..."}], "title?": "Checkpoint"}',
        "mnemonic-card":     '{"acronym?": "...", "expansion?": [{"letter":"X","text":"..."}], "story?": "..."}',
        "trap-callout":      '{"kind?": "trap|warn|note|ok", "title?": "...", "body": "..."}',
        "why-it-matters":    '{"body": "..."}',
        "flowchart":         '{"steps": [{"label":"...","body?":"...","tone?":"paper|accent"}], "title?": "..."}',
        "timeline":          '{"events": [{"date":"...","label":"...","body?":"...","tone?":"ink|accent"}], "title?": "...", "unit?": "year"}',
        "concept-map":       '{"nodes": [{"id":"a","x":50,"y":50,"label":"...","kind?":"hub"}], "edges": [{"from":"a","to":"b","label?":"...","curve?":0}], "title?": "...", "aspect_ratio?": "3/2"}',
        "comparison-matrix": '{"cols": [{"id":"x","label":"X"}], "rows": [{"label":"...","cells":{"x":"yes|no|partial|star|<freeform>"}}], "title?": "...", "legend?": true}',
        "process-ribbon":    '{"phases": [{"label":"...","beats?":["..."],"color?":"accent|warn|info|ok"}], "title?": "...", "outcome?": "..."}',
        "anatomy-diagram":   '{"image_html": "<svg>...</svg>", "left_labels?": [{"y":50,"label":"...","body?":"..."}], "right_labels?": [...], "title?": "...", "caption?": "...", "height?": 360}',
        "number-line":       '{"min": -2, "max": 4, "ticks?": [-2,0,2,4], "points?": [{"x":1.5,"label?":"x","color?":"..."}], "intervals?": [{"from":0,"to":3,"label?":"...","openLeft?":true,"openRight?":false}], "title?": "..."}',
        "proof-ladder":      '{"steps": [{"statement":"...","justification":"...","kind?":"qed"}], "title?": "Proof", "given?": "...", "prove?": "..."}',
        "annotated-quote":   '{"quote": "...", "annotations?": [{"phrase":"<exact substring>","note":"..."}], "source?": "..."}',
        "drag-order":        '{"prompt": "...", "items": [{"id":"1","label":"..."}], "correct_order": ["1","2","3"]}',
        "fill-in-blank":     '{"template": "...{{0}}... {{1}}...", "blanks": [{"accept":["x","X"],"placeholder?":"..."}], "hint?": "..."}',
        "match-pairs":       '{"prompt": "...", "pairs": [{"id":"a","left":"...","right":"..."}]}',
        "parameter-slider":  '{"label": "...", "min": 0, "max": 10, "step?": 0.1, "default?": 5, "formula_tex?": "<latex>", "expr?": "<JS expr in v>"}',
        "build-equation":    '{"prompt": "...", "tokens": [{"id":"a","label":"a"},{"id":"b","math":"<latex>"}], "correct": ["a","b"]}',
        "estimate-range":    '{"question": "...", "actual": 42, "min?": 0, "max?": 100, "unit?": "", "generous_width?": 10}',
        "confidence-poll":   '{"question": "...", "options": [{"label":"guess","commentary?":"..."}], "commentary?": "..."}',
        "stamp":             '{"label": "KEY", "tone?": "accent|warn|ok|info"}',
        "tag":               '{"label": "...", "tone?": "mute|accent|warn|ok|info"}',
        "paper-rule":        '{"glyph?": "§"}',
        "marginalia":        '{"text": "..."}',
        "fig-caption":       '{"text": "...", "number?": 3}',
        # Chem visuals
        "molecule-diagram":  '{"atoms": [{"id":"a1","x":0,"y":0,"el":"C","charge?":0,"lonePairs?":0}], "bonds?": [{"a":"a1","b":"a2","order?":1,"kind?":"wedge|dash"}], "highlights?": ["a1", 0], "arrows?": [{"from":"a1","to":"a2","kind?":"half","curve?":0.4}], "label?": "...", "caption?": "..."}',
        "reaction-equation": '{"reactants": ["..."], "products": ["..."], "reagents?": "...", "conditions?": "...", "equilibrium?": false, "label?": "...", "caption?": "..."}',
        "arrow-pushing":     '{"steps": [{"label":"...","body?":"...","diagram?":"<pre-rendered HTML>"}], "title?": "..."}',
        "energy-diagram":    '{"nodes": [{"label":"reactants","energy":0,"kind?":"min|ts"}], "title?": "...", "delta_g?": "−3 kcal/mol", "delta_g_dagger?": "+5 kcal/mol", "caption?": "..."}',
        "orbital-diagram":   '{"levels": [{"label":"1s","orbitals":[{"electrons":2}]}], "title?": "...", "caption?": "..."}',
        "ph-scale":          '{"points?": [{"ph":1,"label":"Stomach"}], "title?": "..."}',
        "periodic-snippet":  '{"highlight?": ["C","H","N","O","P","S"], "notes?": {"C":"4"}, "title?": "..."}',
        # Chem interactives
        "balance-equation":  '{"reactants": ["CH4","O2"], "products": ["CO2","H2O"], "correct?": [1,2,1,2]}',
        "isomer-spotter":    '{"prompt": "...", "target?": "<HTML>", "candidates": [{"id":"a","label":"A","diagram":"<HTML>","isMatch":true,"reason?":"..."}]}',
        "titration-curve":   '{"label?": "...", "acid_vol?": 25, "acid_conc?": 0.1, "base_conc?": 0.1, "pka?": 4.76, "max_base?": 50}',
        "electron-config":   '{"element": "Carbon", "atomic_number": 6, "correct_config": [{"label":"1s","slots":1},{"label":"2s","slots":1},{"label":"2p","slots":3}]}',
    }
    names = [n for n in BLOCK_NAMES if (only is None or n in only)]
    lines = []
    for name in names:
        schema = schemas.get(name, "{}")
        lines.append(f"- `bart-{name}`: {schema}")
    return "\n".join(lines)


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
}


# Subject-keyword detection for chemistry — when matched, we swap to the
# chem-flavored catalog so the model sees molecule/reaction/etc as available.
_CHEM_KEYWORDS = (
    "chem", "organic", "inorganic", "biochem", "pchem", "kinetic",
    "thermodynamic",
)


def is_chem_subject(subject: str) -> bool:
    s = (subject or "").lower()
    return any(k in s for k in _CHEM_KEYWORDS)


def catalog_for(artifact_kind: str, subject: str = "") -> str:
    """Slim catalog appropriate for the given artifact_kind.

    When `subject` is provided and matches chem keywords, daily-lesson
    catalogs include the chem-specific blocks (molecule-diagram,
    reaction-equation, etc.).
    """
    if artifact_kind == "daily_lesson" and is_chem_subject(subject):
        only = _CATALOG_BY_ARTIFACT["chem_daily_lesson"]
    else:
        only = _CATALOG_BY_ARTIFACT.get(artifact_kind)
    return render_block_catalog(only=only)
