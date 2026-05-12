"""Single source of truth for `bart-<name>` block schemas.

Today the per-block schema lived in three places that drift:

  * the hand-written catalog text in ``block_expand.render_block_catalog`` —
    what the Author sees;
  * the implicit signatures of the renderer functions in ``lib_blocks.py`` /
    ``lib_blocks_packs.py`` — what actually gets called;
  * ad-hoc re-validation scattered around.

This module consolidates all of that into one table, ``BLOCK_SCHEMAS``:
``name -> BlockSchema(required, optional, field_types, summary, example)``.

  * ``block_expand.render_block_catalog`` generates the agent-facing catalog
    *from* this table.
  * ``blocks_validate.validate_blocks`` validates block JSON *against* this
    table.

Field types are deliberately coarse — ``str`` / ``list`` / ``dict`` / ``bool``
/ ``int`` / ``(int, float)`` / a tuple of those. JSON has only those shapes;
finer structure (a list-of-dicts vs a list-of-strings) is the renderer's
problem, not the validator's. ``object`` means "any JSON value is fine".

The ``required``/``optional`` split is derived from the renderer signatures
(a positional or required keyword-only param → required; a param with a
default → optional), cross-checked against the hand-written examples; the
``test_block_schemas`` suite asserts the table stays consistent with the
registry and the signatures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple, Union


# A coarse "JSON type" — a Python type, a tuple of types, or `object` for
# "anything goes". `bool` must be listed before `int` where both are accepted
# (a JSON `true` is `bool`, never `int`, in practice).
FieldType = Union[type, Tuple[type, ...]]

# Numeric: JSON numbers decode to int or float.
NUM: FieldType = (int, float)
# Numeric-or-string (e.g. a figure number "3" or 3).
NUM_OR_STR: FieldType = (int, float, str)


@dataclass(frozen=True)
class BlockSchema:
    """Schema for one ``bart-<name>`` block.

    ``required`` / ``optional``  — field names; required ones must be present
    in the block's JSON object, optional ones may be.

    ``field_types``  — for fields whose top-level JSON type we want to check
    (a subset of ``required ∪ optional``). Coarse: ``str`` / ``list`` /
    ``dict`` / ``bool`` / ``int`` / ``(int, float)`` / ``object``.

    ``summary``  — one-line human description (shown in the catalog).

    ``example``  — a JSON example string (the body of the fence), shown in
    the catalog so the Author has a concrete shape to copy.
    """

    required: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    field_types: dict = field(default_factory=dict)
    summary: str = ""
    example: str = "{}"


# ─────────────────────────────────────────────────────────────────
# The table. One entry per name in ``block_expand._BLOCK_REGISTRY``.
# Keep alphabetical-ish within each family for readability; the catalog
# generator re-sorts globally anyway.
# ─────────────────────────────────────────────────────────────────

# Common type maps reused below.
_STR = str
_LIST = list
_DICT = dict
_BOOL = bool
_INT = int


BLOCK_SCHEMAS: dict[str, BlockSchema] = {
    # ── Primitives ────────────────────────────────────────────────
    "stamp": BlockSchema(
        required=("label",),
        optional=("tone",),
        field_types={"label": _STR, "tone": _STR},
        summary="A small rubber-stamp badge (KEY, NB, …).",
        example='{"label": "KEY", "tone?": "accent|warn|ok|info"}',
    ),
    "tag": BlockSchema(
        required=("label",),
        optional=("tone",),
        field_types={"label": _STR, "tone": _STR},
        summary="A pill-shaped inline label.",
        example='{"label": "...", "tone?": "mute|accent|warn|ok|info"}',
    ),
    "paper-rule": BlockSchema(
        optional=("glyph",),
        field_types={"glyph": _STR},
        summary="A decorative section divider rule.",
        example='{"glyph?": "§"}',
    ),
    "marginalia": BlockSchema(
        required=("text",),
        field_types={"text": _STR},
        summary="A handwritten-style note in the margin.",
        example='{"text": "..."}',
    ),
    "fig-caption": BlockSchema(
        required=("text",),
        optional=("number",),
        field_types={"text": _STR, "number": NUM_OR_STR},
        summary="A figure caption, optionally numbered.",
        example='{"text": "...", "number?": 3}',
    ),
    # ── Visuals (originals) ───────────────────────────────────────
    "formula-card": BlockSchema(
        required=("tex",),
        optional=("title", "legend", "note", "stamp_label", "cite"),
        field_types={
            "tex": _STR, "title": _STR, "legend": _LIST, "note": _STR,
            "stamp_label": _STR, "cite": _STR,
        },
        summary="A boxed equation with optional symbol legend + note + citation.",
        example='{"tex": "<latex>", "title?": "...", "legend?": [{"symbol":"x","meaning":"..."}], "note?": "...", "stamp_label?": "KEY", "cite?": "<corpus location, e.g. \'Lecture 4 §2\' or \'HW7 P3\'>"}',
    ),
    "worked-example": BlockSchema(
        required=("problem", "steps"),
        optional=("answer", "tag_label", "cite"),
        field_types={
            "problem": _STR, "steps": _LIST, "answer": _STR,
            "tag_label": _STR, "cite": _STR,
        },
        summary="A problem worked through numbered solution steps to an answer.",
        example='{"problem": "...", "steps": [{"action":"...", "math?":"<latex>", "reasoning?":"..."}], "answer?": "...", "tag_label?": "Worked example", "cite?": "<corpus location>"}',
    ),
    "mnemonic-card": BlockSchema(
        # mnemonic_card() takes **kwargs — every field is technically optional;
        # but acronym+expansion (or story) is the useful shape.
        optional=("acronym", "expansion", "story", "title", "label"),
        field_types={
            "acronym": _STR, "expansion": _LIST, "story": _STR,
            "title": _STR, "label": _STR,
        },
        summary="A memory aid — acronym + expansion, or a short story.",
        example='{"acronym?": "...", "expansion?": [{"letter":"X","text":"..."}], "story?": "..."}',
    ),
    "trap-callout": BlockSchema(
        required=("body",),
        optional=("kind", "title"),
        field_types={"body": _STR, "kind": _STR, "title": _STR},
        summary="A boxed warning / note / OK callout (kind=trap|warn|note|ok).",
        example='{"kind?": "trap|warn|note|ok", "title?": "...", "body": "..."}',
    ),
    "why-it-matters": BlockSchema(
        required=("body",),
        optional=("bart_svg",),
        field_types={"body": _STR, "bart_svg": _STR},
        summary="A short 'why this matters' framing box.",
        example='{"body": "..."}',
    ),
    "flowchart": BlockSchema(
        required=("steps",),
        optional=("title",),
        field_types={"steps": _LIST, "title": _STR},
        summary="A vertical flow of labelled steps.",
        example='{"steps": [{"label":"...","body?":"...","tone?":"paper|accent"}], "title?": "..."}',
    ),
    "concept-build": BlockSchema(
        required=("name",),
        optional=(
            "motivate", "define", "define_tex", "example", "example_tex",
            "connect", "contrast", "apply", "threshold",
        ),
        field_types={
            "name": _STR, "motivate": _STR, "define": _STR, "define_tex": _STR,
            "example": _STR, "example_tex": _STR, "connect": _STR,
            "contrast": _STR, "apply": _STR, "threshold": _BOOL,
        },
        summary="The teaching-arc card: motivate → define → example → connect → contrast → apply.",
        example='{"name": "<concept name>", "motivate?": "<why this needs to exist>", "define?": "<formal statement>", "define_tex?": "<latex>", "example?": "<concrete numerical example prose>", "example_tex?": "<latex>", "connect?": "<tie to prior concept>", "contrast?": "<vs the closest neighbor concept students confuse it with>", "apply?": "<use it on a corpus problem>", "threshold?": true}',
    ),
    # ── Visuals (extras) ──────────────────────────────────────────
    "timeline": BlockSchema(
        required=("events",),
        optional=("title", "unit"),
        field_types={"events": _LIST, "title": _STR, "unit": _STR},
        summary="A horizontal timeline of dated events.",
        example='{"events": [{"date":"...","label":"...","body?":"...","tone?":"ink|accent"}], "title?": "...", "unit?": "year"}',
    ),
    "concept-map": BlockSchema(
        required=("nodes", "edges"),
        optional=("title", "aspect_ratio"),
        field_types={
            "nodes": _LIST, "edges": _LIST, "title": _STR, "aspect_ratio": _STR,
        },
        summary="A node-and-edge concept graph.",
        example='{"nodes": [{"id":"a","x":50,"y":50,"label":"...","kind?":"hub"}], "edges": [{"from":"a","to":"b","label?":"...","curve?":0}], "title?": "...", "aspect_ratio?": "3/2"}',
    ),
    "comparison-matrix": BlockSchema(
        required=("cols", "rows"),
        optional=("title", "legend"),
        field_types={
            "cols": _LIST, "rows": _LIST, "title": _STR, "legend": _BOOL,
        },
        summary="A feature-comparison grid.",
        example='{"cols": [{"id":"x","label":"X"}], "rows": [{"label":"...","cells":{"x":"yes|no|partial|star|<freeform>"}}], "title?": "...", "legend?": true}',
    ),
    "process-ribbon": BlockSchema(
        required=("phases",),
        optional=("title", "outcome"),
        field_types={"phases": _LIST, "title": _STR, "outcome": _STR},
        summary="A horizontal ribbon of process phases with beats.",
        example='{"phases": [{"label":"...","beats?":["..."],"color?":"accent|warn|info|ok"}], "title?": "...", "outcome?": "..."}',
    ),
    "anatomy-diagram": BlockSchema(
        required=("image_html",),
        optional=("left_labels", "right_labels", "title", "caption", "height"),
        field_types={
            "image_html": _STR, "left_labels": _LIST, "right_labels": _LIST,
            "title": _STR, "caption": _STR, "height": _INT,
        },
        summary="An SVG diagram with anchored callout labels on either side.",
        example='{"image_html": "<svg>...</svg>", "left_labels?": [{"y":50,"label":"...","body?":"..."}], "right_labels?": [...], "title?": "...", "caption?": "...", "height?": 360}',
    ),
    "number-line": BlockSchema(
        optional=("min", "max", "ticks", "points", "intervals", "title", "height"),
        field_types={
            "min": NUM, "max": NUM, "ticks": _LIST, "points": _LIST,
            "intervals": _LIST, "title": _STR, "height": _INT,
        },
        summary="A number line with ticks, marked points, and shaded intervals.",
        example='{"min": -2, "max": 4, "ticks?": [-2,0,2,4], "points?": [{"x":1.5,"label?":"x","color?":"..."}], "intervals?": [{"from":0,"to":3,"label?":"...","openLeft?":true,"openRight?":false}], "title?": "..."}',
    ),
    "proof-ladder": BlockSchema(
        required=("steps",),
        optional=("title", "given", "prove"),
        field_types={
            "steps": _LIST, "title": _STR, "given": _STR, "prove": _STR,
        },
        summary="A two-column proof (statement / justification) ladder.",
        example='{"steps": [{"statement":"...","justification":"...","kind?":"qed"}], "title?": "Proof", "given?": "...", "prove?": "..."}',
    ),
    "annotated-quote": BlockSchema(
        required=("quote",),
        optional=("annotations", "source"),
        field_types={"quote": _STR, "annotations": _LIST, "source": _STR},
        summary="A pull-quote with phrase-level annotations.",
        example='{"quote": "...", "annotations?": [{"phrase":"<exact substring>","note":"..."}], "source?": "..."}',
    ),
    # ── Interactives (originals) ──────────────────────────────────
    "quick-check": BlockSchema(
        required=("question", "answer"),
        optional=("hint", "label"),
        field_types={
            "question": _STR, "answer": _STR, "hint": _STR, "label": _STR,
        },
        summary="A one-shot self-check: question, reveal-able answer, optional hint.",
        example='{"question": "...", "answer": "...", "hint?": "...", "label?": "Quick check"}',
    ),
    "multi-step": BlockSchema(
        required=("problem", "hints", "solution"),
        optional=("label",),
        field_types={
            "problem": _STR, "hints": _LIST, "solution": _STR, "label": _STR,
        },
        summary="A practice problem with progressive hints and a full solution.",
        example='{"problem": "...", "hints": ["...", "..."], "solution": "...", "label?": "Practice problem"}',
    ),
    "multiple-choice": BlockSchema(
        required=("question", "choices"),
        optional=("label",),
        field_types={"question": _STR, "choices": _LIST, "label": _STR},
        summary="A multiple-choice question; each choice {text, correct, explanation?}.",
        example='{"question": "...", "choices": [{"text":"...","correct":true,"explanation?":"..."}], "label?": "Multiple choice"}',
    ),
    "hotspots": BlockSchema(
        required=("image_html", "spots"),
        optional=("caption",),
        field_types={"image_html": _STR, "spots": _LIST, "caption": _STR},
        summary="An image with clickable hotspot annotations at (x, y) percentages.",
        example='{"image_html": "<svg>...</svg>", "spots": [{"x":50,"y":50,"label":"...","body?":"..."}], "caption?": "..."}',
    ),
    "checkpoint": BlockSchema(
        required=("cards",),
        optional=("title",),
        field_types={"cards": _LIST, "title": _STR},
        summary="A small flip-card deck (front/back) for end-of-section recall.",
        example='{"cards": [{"front":"...","back":"..."}], "title?": "Checkpoint"}',
    ),
    # ── Interactives (extras) ─────────────────────────────────────
    "drag-order": BlockSchema(
        required=("prompt", "items"),
        optional=("correct_order",),
        field_types={"prompt": _STR, "items": _LIST, "correct_order": _LIST},
        summary="A reorder-the-items exercise (items: list of {id,label} or strings).",
        example='{"prompt": "...", "items": [{"id":"1","label":"..."}], "correct_order": ["1","2","3"]}',
    ),
    "fill-in-blank": BlockSchema(
        optional=("template", "blanks", "hint", "prompt", "label", "title"),
        field_types={
            "template": _STR, "blanks": _LIST, "hint": _STR, "prompt": _STR,
            "label": _STR, "title": _STR,
        },
        summary="A cloze exercise — a template with {{n}} blanks and an accept-list each.",
        example='{"template": "...{{0}}... {{1}}...", "blanks": [{"accept":["x","X"],"placeholder?":"..."}], "hint?": "..."}',
    ),
    "match-pairs": BlockSchema(
        optional=("prompt", "pairs", "title", "label"),
        field_types={
            "prompt": _STR, "pairs": _LIST, "title": _STR, "label": _STR,
        },
        summary="A match-the-left-to-the-right exercise (pairs: list of {id,left,right}).",
        example='{"prompt": "...", "pairs": [{"id":"a","left":"...","right":"..."}]}',
    ),
    "parameter-slider": BlockSchema(
        required=("label",),
        optional=("min", "max", "step", "default", "formula_tex", "expr"),
        field_types={
            "label": _STR, "min": NUM, "max": NUM, "step": NUM,
            "default": NUM, "formula_tex": _STR, "expr": _STR,
        },
        summary="An interactive slider that drives a formula / JS expression in `v`.",
        example='{"label": "...", "min": 0, "max": 10, "step?": 0.1, "default?": 5, "formula_tex?": "<latex>", "expr?": "<JS expr in v>"}',
    ),
    "build-equation": BlockSchema(
        required=("prompt", "tokens"),
        optional=("correct", "answer"),
        field_types={
            "prompt": _STR, "tokens": _LIST, "correct": _LIST, "answer": object,
        },
        summary="A drag-the-tokens-into-an-equation exercise.",
        example='{"prompt": "...", "tokens": [{"id":"a","label":"a"},{"id":"b","math":"<latex>"}], "correct": ["a","b"]}',
    ),
    "estimate-range": BlockSchema(
        required=("question", "actual"),
        optional=("min", "max", "unit", "generous_width"),
        field_types={
            "question": _STR, "actual": NUM, "min": NUM, "max": NUM,
            "unit": _STR, "generous_width": NUM,
        },
        summary="A 'guess a number' exercise scored against the actual value.",
        example='{"question": "...", "actual": 42, "min?": 0, "max?": 100, "unit?": "", "generous_width?": 10}',
    ),
    "confidence-poll": BlockSchema(
        required=("question", "options"),
        optional=("commentary",),
        field_types={"question": _STR, "options": _LIST, "commentary": _STR},
        summary="A how-confident-are-you poll with per-option commentary.",
        example='{"question": "...", "options": [{"label":"guess","commentary?":"..."}], "commentary?": "..."}',
    ),
    # ── Chem visuals ──────────────────────────────────────────────
    "molecule-diagram": BlockSchema(
        required=("atoms",),
        optional=(
            "bonds", "width", "height", "pad", "label", "caption",
            "highlights", "arrows",
        ),
        field_types={
            "atoms": _LIST, "bonds": _LIST, "width": _INT, "height": _INT,
            "pad": _INT, "label": _STR, "caption": _STR, "highlights": _LIST,
            "arrows": _LIST,
        },
        summary="A 2-D molecule (atoms + bonds), with optional highlights / curved arrows.",
        example='{"atoms": [{"id":"a1","x":0,"y":0,"el":"C","charge?":0,"lonePairs?":0}], "bonds?": [{"a":"a1","b":"a2","order?":1,"kind?":"wedge|dash"}], "highlights?": ["a1", 0], "arrows?": [{"from":"a1","to":"a2","kind?":"half","curve?":0.4}], "label?": "...", "caption?": "..."}',
    ),
    "reaction-equation": BlockSchema(
        required=("reactants", "products"),
        optional=("reagents", "conditions", "equilibrium", "label", "caption"),
        field_types={
            "reactants": _LIST, "products": _LIST, "reagents": _STR,
            "conditions": _STR, "equilibrium": _BOOL, "label": _STR,
            "caption": _STR,
        },
        summary="A chemical reaction: reactants → products, with reagents / conditions.",
        example='{"reactants": ["..."], "products": ["..."], "reagents?": "...", "conditions?": "...", "equilibrium?": false, "label?": "...", "caption?": "..."}',
    ),
    "arrow-pushing": BlockSchema(
        required=("steps",),
        optional=("title",),
        field_types={"steps": _LIST, "title": _STR},
        summary="A sequence of arrow-pushing mechanism steps (each with a pre-rendered diagram).",
        example='{"steps": [{"label":"...","body?":"...","diagram?":"<pre-rendered HTML>"}], "title?": "..."}',
    ),
    "energy-diagram": BlockSchema(
        required=("nodes",),
        optional=("title", "delta_g", "delta_g_dagger", "caption"),
        field_types={
            "nodes": _LIST, "title": _STR, "delta_g": _STR,
            "delta_g_dagger": _STR, "caption": _STR,
        },
        summary="A reaction energy profile (minima + transition states).",
        example='{"nodes": [{"label":"reactants","energy":0,"kind?":"min|ts"}], "title?": "...", "delta_g?": "−3 kcal/mol", "delta_g_dagger?": "+5 kcal/mol", "caption?": "..."}',
    ),
    "orbital-diagram": BlockSchema(
        required=("levels",),
        optional=("title", "width", "height", "caption"),
        field_types={
            "levels": _LIST, "title": _STR, "width": _INT, "height": _INT,
            "caption": _STR,
        },
        summary="An orbital energy-level diagram with electron arrows.",
        example='{"levels": [{"label":"1s","orbitals":[{"electrons":2}]}], "title?": "...", "caption?": "..."}',
    ),
    "ph-scale": BlockSchema(
        optional=("points", "title"),
        field_types={"points": _LIST, "title": _STR},
        summary="A 0–14 pH scale with labelled points.",
        example='{"points?": [{"ph":1,"label":"Stomach"}], "title?": "..."}',
    ),
    "periodic-snippet": BlockSchema(
        optional=("highlight", "notes", "title"),
        field_types={"highlight": _LIST, "notes": _DICT, "title": _STR},
        summary="A mini periodic-table snippet highlighting selected elements.",
        example='{"highlight?": ["C","H","N","O","P","S"], "notes?": {"C":"4"}, "title?": "..."}',
    ),
    # ── Chem interactives ─────────────────────────────────────────
    "balance-equation": BlockSchema(
        required=("reactants", "products"),
        optional=("correct",),
        field_types={"reactants": _LIST, "products": _LIST, "correct": _LIST},
        summary="A balance-the-coefficients exercise.",
        example='{"reactants": ["CH4","O2"], "products": ["CO2","H2O"], "correct?": [1,2,1,2]}',
    ),
    "isomer-spotter": BlockSchema(
        required=("prompt", "candidates"),
        optional=("target",),
        field_types={"prompt": _STR, "candidates": _LIST, "target": _STR},
        summary="A spot-the-isomer exercise; candidates each carry a pre-rendered diagram.",
        example='{"prompt": "...", "target?": "<HTML>", "candidates": [{"id":"a","label":"A","diagram":"<HTML>","isMatch":true,"reason?":"..."}]}',
    ),
    "titration-curve": BlockSchema(
        optional=("label", "acid_vol", "acid_conc", "base_conc", "pka", "max_base"),
        field_types={
            "label": _STR, "acid_vol": NUM, "acid_conc": NUM,
            "base_conc": NUM, "pka": NUM, "max_base": NUM,
        },
        summary="A titration curve (strong/strong, or weak acid via pKa).",
        example='{"label?": "...", "acid_vol?": 25, "acid_conc?": 0.1, "base_conc?": 0.1, "pka?": 4.76, "max_base?": 50}',
    ),
    "electron-config": BlockSchema(
        required=("element", "atomic_number", "correct_config"),
        field_types={
            "element": _STR, "atomic_number": _INT, "correct_config": _LIST,
        },
        summary="A fill-the-orbital-boxes electron-configuration exercise.",
        example='{"element": "Carbon", "atomic_number": 6, "correct_config": [{"label":"1s","slots":1},{"label":"2s","slots":1},{"label":"2p","slots":3}]}',
    ),
    # ── ML pack ───────────────────────────────────────────────────
    "confusion-matrix": BlockSchema(
        required=("classes", "counts"),
        optional=("title", "normalize", "caption"),
        field_types={
            "classes": _LIST, "counts": _LIST, "title": _STR,
            "normalize": _BOOL, "caption": _STR,
        },
        summary="A classifier confusion matrix (counts, optionally row-normalized).",
        example='{"classes": ["neg","neu","pos"], "counts": [[82,12,6],[10,70,20],[4,14,82]], "title?": "...", "normalize?": false, "caption?": "..."}',
    ),
    "loss-curve": BlockSchema(
        required=("series",),
        optional=("title", "x_label", "y_label", "caption"),
        field_types={
            "series": _LIST, "title": _STR, "x_label": _STR, "y_label": _STR,
            "caption": _STR,
        },
        summary="A training/validation loss-vs-epoch line chart.",
        example='{"series": [{"label":"train","data":[1.5,1.0,0.7,0.5]},{"label":"val","data":[1.4,1.1,0.9,0.85]}], "x_label?": "epoch", "y_label?": "loss", "title?": "...", "caption?": "..."}',
    ),
    "neural-net-diagram": BlockSchema(
        required=("layers",),
        optional=("title", "width", "height", "caption"),
        field_types={
            "layers": _LIST, "title": _STR, "width": _INT, "height": _INT,
            "caption": _STR,
        },
        summary="A feed-forward network layer diagram.",
        example='{"layers": [{"label":"input · 4","units":4},{"label":"hidden · 8","units":8},{"label":"output · 3","units":3}], "title?": "...", "caption?": "..."}',
    ),
    "attention-matrix": BlockSchema(
        required=("row_tokens", "col_tokens", "weights"),
        optional=("title", "caption"),
        field_types={
            "row_tokens": _LIST, "col_tokens": _LIST, "weights": _LIST,
            "title": _STR, "caption": _STR,
        },
        summary="A token-by-token attention heatmap.",
        example='{"row_tokens": ["The","cat"], "col_tokens": ["The","cat"], "weights": [[0.5,0.3],[0.4,0.5]], "title?": "...", "caption?": "..."}',
    ),
    "embedding-scatter": BlockSchema(
        required=("points",),
        optional=("title", "width", "height", "caption"),
        field_types={
            "points": _LIST, "title": _STR, "width": _INT, "height": _INT,
            "caption": _STR,
        },
        summary="A 2-D scatter of labelled, grouped embedding points.",
        example='{"points": [{"x":1.2,"y":2.1,"label":"cat","group":"A"}], "title?": "...", "caption?": "..."}',
    ),
    # ── CS pack ───────────────────────────────────────────────────
    "code-block": BlockSchema(
        required=("lines",),
        optional=("language", "title", "annotations", "caption"),
        field_types={
            "lines": _LIST, "language": _STR, "title": _STR,
            "annotations": _LIST, "caption": _STR,
        },
        summary="A syntax-highlighted code listing with optional line annotations.",
        example='{"lines": ["def fib(n):","    return n if n<2 else fib(n-1)+fib(n-2)"], "language?": "python", "annotations?": [{"line":1,"text":"base case"}], "title?": "...", "caption?": "..."}',
    ),
    "call-stack": BlockSchema(
        required=("frames",),
        optional=("title", "caption"),
        field_types={"frames": _LIST, "title": _STR, "caption": _STR},
        summary="A snapshot of a call stack (frames with args + locals).",
        example='{"frames": [{"fn":"main","args":[{"value":3}]},{"fn":"fib","args":[{"value":2}],"locals":[{"name":"n","value":2}]}], "title?": "...", "caption?": "..."}',
    ),
    "memory-layout": BlockSchema(
        required=("regions",),
        optional=("title", "caption"),
        field_types={"regions": _LIST, "title": _STR, "caption": _STR},
        summary="A memory-regions diagram (stack / heap / … with addressed items).",
        example='{"regions": [{"name":"stack","items":[{"addr":"0x7ffe","label":"x","value":42}]},{"name":"heap","items":[{"addr":"0x6010","label":"buf"}]}], "title?": "...", "caption?": "..."}',
    ),
    "binary-tree": BlockSchema(
        required=("root",),
        optional=("title", "width", "height", "caption"),
        field_types={
            "root": _DICT, "title": _STR, "width": _INT, "height": _INT,
            "caption": _STR,
        },
        summary="A binary tree (recursive {value,left?,right?} root).",
        example='{"root": {"value":5,"left":{"value":3},"right":{"value":7,"left":{"value":6},"right":{"value":9}}}, "title?": "...", "caption?": "..."}',
    ),
    "process-timeline": BlockSchema(
        required=("processes",),
        optional=("total_time", "title", "caption"),
        field_types={
            "processes": _LIST, "total_time": _INT, "title": _STR,
            "caption": _STR,
        },
        summary="A Gantt-style process scheduling timeline.",
        example='{"processes": [{"name":"P1","segments":[{"start":0,"dur":3,"kind":"run"},{"start":3,"dur":2,"kind":"wait"}]}], "total_time?": 20, "title?": "...", "caption?": "..."}',
    ),
    # ── Phil pack ─────────────────────────────────────────────────
    "argument-map": BlockSchema(
        required=("premises", "conclusion"),
        optional=("title", "caption"),
        field_types={
            "premises": _LIST, "conclusion": _STR, "title": _STR,
            "caption": _STR,
        },
        summary="A premises-to-conclusion argument diagram.",
        example='{"premises": ["All men are mortal","Socrates is a man"], "conclusion": "Socrates is mortal", "title?": "...", "caption?": "..."}',
    ),
    "truth-table": BlockSchema(
        required=("vars", "formula", "rows"),
        optional=("title", "caption"),
        field_types={
            "vars": _LIST, "formula": _STR, "rows": _LIST, "title": _STR,
            "caption": _STR,
        },
        summary="A truth table for a propositional formula.",
        example='{"vars": ["P","Q"], "formula": "P → Q", "rows": [{"values":[true,true],"result":true},{"values":[true,false],"result":false}], "title?": "...", "caption?": "..."}',
    ),
    "venn-logic": BlockSchema(
        optional=("sets", "shaded", "title", "caption"),
        field_types={
            "sets": _LIST, "shaded": _LIST, "title": _STR, "caption": _STR,
        },
        summary="A 2- or 3-set Venn diagram with shaded regions.",
        example='{"sets?": [{"label":"A","cx":130,"cy":110,"r":70},{"label":"B","cx":230,"cy":110,"r":70}], "shaded?": ["AB"], "title?": "...", "caption?": "..."}',
    ),
    "dialectic-tree": BlockSchema(
        required=("thesis", "antithesis", "synthesis"),
        optional=("title", "caption"),
        field_types={
            "thesis": _STR, "antithesis": _STR, "synthesis": _STR,
            "title": _STR, "caption": _STR,
        },
        summary="A thesis / antithesis / synthesis triad.",
        example='{"thesis": "...", "antithesis": "...", "synthesis": "...", "title?": "...", "caption?": "..."}',
    ),
    "quote-pull": BlockSchema(
        required=("quote",),
        optional=("attribution", "work", "caption"),
        field_types={
            "quote": _STR, "attribution": _STR, "work": _STR, "caption": _STR,
        },
        summary="A large pull-quote with attribution.",
        example='{"quote": "...", "attribution?": "...", "work?": "...", "caption?": "..."}',
    ),
    # ── Math pack ─────────────────────────────────────────────────
    "proof-block": BlockSchema(
        required=("steps",),
        optional=("given", "qed", "title", "caption"),
        field_types={
            "steps": _LIST, "given": _LIST, "qed": _BOOL, "title": _STR,
            "caption": _STR,
        },
        summary="A statement/justification proof block (with optional givens + QED).",
        example='{"steps": [{"statement":"a²+b²=c²","justification":"Pythagoras"}], "given?": ["right triangle ABC"], "qed?": true, "title?": "...", "caption?": "..."}',
    ),
    "matrix-view": BlockSchema(
        required=("rows",),
        optional=("label", "title", "highlight", "caption"),
        field_types={
            "rows": _LIST, "label": _STR, "title": _STR, "highlight": _DICT,
            "caption": _STR,
        },
        summary="A rendered matrix, optionally highlighting a cell / row / col.",
        example='{"rows": [[1,2],[3,4]], "label?": "A", "highlight?": {"cell":[0,1]}, "title?": "...", "caption?": "..."}',
    ),
    "graph-plot": BlockSchema(
        required=("fns",),
        optional=("x_range", "y_range", "marks", "width", "height", "title", "caption"),
        field_types={
            "fns": _LIST, "x_range": _LIST, "y_range": _LIST, "marks": _LIST,
            "width": _INT, "height": _INT, "title": _STR, "caption": _STR,
        },
        summary="A function plot from sampled points, with optional marked points.",
        example='{"fns": [{"points":[[-2,4],[-1,1],[0,0],[1,1],[2,4]],"color?":"...","label?":"y=x²"}], "x_range?": [-5,5], "y_range?": [-5,5], "marks?": [{"x":0,"y":0,"label":"origin"}], "title?": "...", "caption?": "..."}',
    ),
    "integral-area": BlockSchema(
        required=("points", "a", "b"),
        optional=("x_range", "y_range", "width", "height", "title", "caption"),
        field_types={
            "points": _LIST, "a": NUM, "b": NUM, "x_range": _LIST,
            "y_range": _LIST, "width": _INT, "height": _INT, "title": _STR,
            "caption": _STR,
        },
        summary="A curve with the area under it between a and b shaded.",
        example='{"points": [[0,0],[1,1],[2,4],[3,9]], "a": 0, "b": 3, "x_range?": [-1,5], "y_range?": [-1,10], "title?": "...", "caption?": "..."}',
    ),
    # ── Lit pack ──────────────────────────────────────────────────
    "passage-annotated": BlockSchema(
        required=("passage",),
        optional=("annotations", "title", "attribution", "caption"),
        field_types={
            "passage": _STR, "annotations": _LIST, "title": _STR,
            "attribution": _STR, "caption": _STR,
        },
        summary="A literary passage with margin annotations on chosen phrases.",
        example='{"passage": "Long passage text here.", "annotations?": [{"phrase":"passage","note":"meta-reference"}], "attribution?": "...", "title?": "...", "caption?": "..."}',
    ),
    "character-graph": BlockSchema(
        required=("nodes", "edges"),
        optional=("title", "width", "height", "caption"),
        field_types={
            "nodes": _LIST, "edges": _LIST, "title": _STR, "width": _INT,
            "height": _INT, "caption": _STR,
        },
        summary="A character-relationship graph.",
        example='{"nodes": [{"id":"a","label":"Hamlet","x":120,"y":120}], "edges": [{"from":"a","to":"b","kind":"family","label":"son"}], "title?": "...", "caption?": "..."}',
    ),
    "theme-weave": BlockSchema(
        required=("chapters", "themes", "presence"),
        optional=("title", "caption"),
        field_types={
            "chapters": _LIST, "themes": _LIST, "presence": _LIST,
            "title": _STR, "caption": _STR,
        },
        summary="A theme-presence-per-chapter heatmap.",
        example='{"chapters": ["Ch.1","Ch.2","Ch.3"], "themes": ["guilt","exile"], "presence": [[0.2,0.6,0.9],[0.8,0.4,0.1]], "title?": "...", "caption?": "..."}',
    ),
    "style-spectrum": BlockSchema(
        required=("axis_x", "axis_y", "items"),
        optional=("title", "caption", "width", "height"),
        field_types={
            "axis_x": _LIST, "axis_y": _LIST, "items": _LIST, "title": _STR,
            "caption": _STR, "width": _INT, "height": _INT,
        },
        summary="A 2-axis stylistic positioning chart.",
        example='{"axis_x": ["concrete","abstract"], "axis_y": ["sparse","dense"], "items": [{"label":"Hemingway","x":-0.7,"y":-0.4}], "title?": "...", "caption?": "..."}',
    ),
}


def catalog_entry(name: str) -> str:
    """Render one block's catalog line from its schema.

    Format mirrors (and extends) the old hand-written catalog:
        - `bart-<name>`: <summary>
          required: a, b · optional: c?, d?
          e.g. {<example json>}
    Falls back gracefully if `name` has no schema (shouldn't happen — the
    test suite enforces coverage — but be defensive).
    """
    schema = BLOCK_SCHEMAS.get(name)
    if schema is None:
        return f"- `bart-{name}`: (no schema on record)"
    bits: list[str] = [f"- `bart-{name}`"]
    if schema.summary:
        bits[0] += f": {schema.summary}"
    sub: list[str] = []
    if schema.required:
        sub.append("required: " + ", ".join(schema.required))
    if schema.optional:
        sub.append("optional: " + ", ".join(f"{o}?" for o in schema.optional))
    line = bits[0]
    if sub:
        line += "\n  " + " · ".join(sub)
    line += f"\n  e.g. {schema.example}"
    return line
