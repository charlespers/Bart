"""Interactive bart-block renderers — quizzes, drag/match, sliders, polls.

``quick-check`` / ``multi-step`` / ``multiple-choice`` / ``hotspots`` /
``checkpoint`` (flip-card deck) / ``drag-order`` / ``fill-in-blank`` /
``match-pairs`` / ``parameter-slider`` / ``build-equation`` / ``estimate-range``
/ ``confidence-poll``. Each emits self-contained HTML + a small inline script
(behaviour driven by ``packet.js`` / ``lib_blocks.js`` at view time).

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

from html import escape as _esc
from typing import Any

from ._shared import (
    _block_math,
    _block_md,
    _inline_math,
)
from .primitives import fig_caption


# ─── Interactives ─────────────────────────────────────────────────


def quick_check(
    *,
    question: str,
    answer: str,
    hint: str = "",
    label: str = "Quick check",
) -> str:
    """<QuickCheck> — collapsible answer + optional hint. Static HTML uses
    native <details>; no JS needed."""
    hint_block = (
        f'<details class="b-quick-check-hint-wrap">'
        f'<summary class="b-qc-button">Need a hint?</summary>'
        f'<div class="b-quick-check-hint">'
        f'<strong>Hint. </strong>{_block_md(hint)}'
        f'</div></details>'
        if hint else ""
    )
    return (
        '<div class="b-quick-check">'
        f'<div class="b-quick-check-label">{_esc(label)}</div>'
        f'<div class="b-quick-check-question">{_block_md(question)}</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap">'
        f'{hint_block}'
        f'<details class="b-quick-check-answer-wrap">'
        f'<summary class="b-qc-button">Reveal answer</summary>'
        f'<div class="b-quick-check-answer">{_block_md(answer)}</div>'
        f'</details>'
        f'</div></div>'
    )


def multi_step_problem(
    *,
    problem: str,
    hints: list[str],
    solution: str,
    label: str = "Practice problem",
) -> str:
    """<MultiStepProblem> — problem with progressively-revealed hints.

    Static version: each hint is an independent <details> the reader expands
    in order. The interactive "reveal next hint" button is replaced with
    sequential collapsibles labeled "Hint N of M".
    """
    parts: list[str] = ['<div class="b-multi-step">']
    parts.append(f'<div class="b-multi-step-label">{_esc(label)}</div>')
    parts.append(f'<div class="b-multi-step-problem">{_block_md(problem)}</div>')
    for i, h in enumerate(hints):
        parts.append(
            f'<details class="b-multi-step-hint">'
            f'<summary class="b-multi-step-hint-label">Hint {i+1} of {len(hints)}</summary>'
            f'<div class="b-multi-step-hint-body">{_block_md(h)}</div>'
            f'</details>'
        )
    parts.append(
        f'<details>'
        f'<summary class="b-qc-button" style="margin-top:14px">Show full solution</summary>'
        f'<div class="b-multi-step-solution">'
        f'<div class="b-multi-step-solution-label">Solution</div>'
        f'{_block_md(solution)}'
        f'</div></details>'
    )
    parts.append("</div>")
    return "".join(parts)


def multiple_choice(
    *,
    question: str,
    choices: list[dict[str, Any]],
    label: str = "Multiple choice",
) -> str:
    """<MultipleChoice> — needs JS for selection feedback. The HTML emits the
    skeleton; bart.render.lib_blocks.js wires up clicks at view time.

    Each choice: {text, correct, explanation?}.
    """
    parts: list[str] = ['<div class="b-mc" data-mc>']
    parts.append(f'<div class="b-mc-label">{_esc(label)}</div>')
    parts.append(f'<div class="b-mc-question">{_block_md(question)}</div>')
    parts.append('<div class="b-mc-choices">')
    for i, c in enumerate(choices):
        correct = "true" if c.get("correct") else "false"
        text = _esc(c.get("text", ""))
        letter = chr(65 + i)
        explanation = c.get("explanation", "")
        parts.append(
            f'<button class="b-mc-choice" data-correct="{correct}" data-idx="{i}">'
            f'<span class="b-mc-letter">{letter}</span>'
            f'<span style="flex:1">{text}</span>'
            f'</button>'
        )
        if explanation:
            parts.append(
                f'<div class="b-mc-explanation" data-explain="{i}" hidden>'
                f'{_esc(explanation)}</div>'
            )
    parts.append("</div></div>")
    return "".join(parts)


def hotspots(
    *,
    image_html: str,
    spots: list[dict[str, Any]],
    caption: str = "",
) -> str:
    """<Hotspots> — clickable annotations on a diagram.

    `image_html`: trusted HTML to render inside the canvas (e.g. an SVG).
    `spots`: [{x, y, label, body?}] with x/y in 0-100 (percent).
    """
    parts: list[str] = ['<figure>']
    parts.append('<div class="b-hotspots" data-hotspots>')
    parts.append(f'<div class="b-hotspots-canvas">{image_html}</div>')
    for i, s in enumerate(spots):
        x = float(s.get("x", 50))
        y = float(s.get("y", 50))
        parts.append(
            f'<button class="b-hotspot-marker" data-spot="{i}" '
            f'style="left:{x}%;top:{y}%">{i+1}</button>'
        )
        body_html = (
            f'<div class="b-hotspot-popover-body">{_esc(s["body"])}</div>'
            if s.get("body") else ""
        )
        parts.append(
            f'<div class="b-hotspot-popover" data-popover="{i}">'
            f'<div class="b-hotspot-popover-title">{i+1}. {_esc(s.get("label",""))}</div>'
            f'{body_html}'
            f'</div>'
        )
    parts.append("</div>")
    if caption:
        parts.append(fig_caption(caption))
    parts.append("</figure>")
    return "".join(parts)


def checkpoint(*, cards: list[dict[str, str]], title: str = "Checkpoint") -> str:
    """<Checkpoint> — spaced-repetition deck. `cards`: [{front, back}]."""
    import json as _json
    deck = _json.dumps(cards)
    return (
        '<div class="b-checkpoint" data-checkpoint '
        f'data-cards=\'{_esc(deck, quote=True)}\'>'
        '<div class="b-checkpoint-header">'
        f'<div class="b-checkpoint-title">{_esc(title)}</div>'
        '<div class="b-checkpoint-progress">'
        f'<span class="b-checkpoint-progress-text">0 / {len(cards)}</span>'
        '<div class="b-checkpoint-progress-bar">'
        '<div class="b-checkpoint-progress-fill" style="width:0%"></div>'
        '</div></div></div>'
        '<div class="b-checkpoint-card" data-card-face>click to reveal</div>'
        '<div class="b-checkpoint-hint">click card to flip</div>'
        '<div class="b-checkpoint-ratings" data-ratings hidden>'
        '<button class="b-checkpoint-rating b-rating-again" data-rating="again">'
        '<span class="b-checkpoint-rating-label">Again</span>'
        '<span class="b-checkpoint-rating-sub">&lt; 1 min</span></button>'
        '<button class="b-checkpoint-rating b-rating-hard" data-rating="hard">'
        '<span class="b-checkpoint-rating-label">Hard</span>'
        '<span class="b-checkpoint-rating-sub">10 min</span></button>'
        '<button class="b-checkpoint-rating b-rating-good" data-rating="good">'
        '<span class="b-checkpoint-rating-label">Good</span>'
        '<span class="b-checkpoint-rating-sub">1 day</span></button>'
        '<button class="b-checkpoint-rating b-rating-easy" data-rating="easy">'
        '<span class="b-checkpoint-rating-label">Easy</span>'
        '<span class="b-checkpoint-rating-sub">4 days</span></button>'
        '</div></div>'
    )


def drag_order(
    *,
    prompt: str,
    items: list[Any],
    correct_order: list[Any] | None = None,
    **_extra: Any,
) -> str:
    """<DragOrder> — draggable list; lib_blocks.js grades against `correct_order`.

    `items` accepts either `[{id, label}]` or `[label_str, ...]`. When items are
    plain strings, ids are auto-assigned by index and the supplied order is
    treated as the canonical sequence (so authors can omit `correct_order`).
    `correct_order`: list of item ids in canonical sequence — optional when
    items are strings.
    `**_extra` swallows author-friendly extras like `explanation` / `label`
    that other quiz blocks accept; they're rendered no-op here rather than
    crashing the page.
    """
    import json as _json
    norm_items: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        if isinstance(it, dict):
            norm_items.append({
                "id": str(it.get("id", str(i))),
                "label": str(it.get("label", "")),
            })
        else:
            norm_items.append({"id": str(i), "label": str(it)})
    if correct_order is None or not correct_order:
        correct_order = [d["id"] for d in norm_items]
    correct_json = _json.dumps([str(x) for x in correct_order])
    parts: list[str] = [
        f'<div class="b-drag-order" data-drag-order data-correct=\'{_esc(correct_json, quote=True)}\'>'
        '<div class="b-drag-order-prompt">'
        '<strong>Order</strong>'
        f'{_esc(prompt)}</div>'
        '<ol class="b-drag-list">'
    ]
    for i, it in enumerate(norm_items):
        item_id = _esc(it["id"], quote=True)
        parts.append(
            f'<li class="b-drag-item" draggable="true" data-id="{item_id}">'
            f'<span class="b-drag-idx">{i+1}.</span>'
            f'<span class="b-drag-handle" aria-hidden="true">≡≡</span>'
            f'<span class="b-drag-label">{_esc(it["label"])}</span>'
            f'<span class="b-drag-state" data-state></span>'
            f'</li>'
        )
    parts.append('</ol>')
    parts.append(
        '<div class="b-drag-actions">'
        '<button class="b-btn-primary" data-action="check">Check order</button>'
        '<button class="b-btn-ghost" data-action="reset" hidden>Reset</button>'
        '<span class="b-drag-status" data-status></span>'
        '</div></div>'
    )
    return "".join(parts)


def fill_in_blank(
    *,
    template: str = "",
    blanks: list[dict[str, Any]] | None = None,
    hint: str = "",
    prompt: str = "",     # alias — authors sometimes emit `prompt` for the sentence
    label: str = "",      # accepted alias — author hallucinated key
    title: str = "",      # accepted alias — author hallucinated key
    **_extras: Any,       # swallow unknown kwargs instead of crashing the render
) -> str:
    """<FillInBlank> — sentence with `{{0}}`, `{{1}}` placeholders.

    `blanks`: [{accept: [str], placeholder?: str}].
    Acceptance is case-insensitive, whitespace-trimmed; handled in JS.
    Accepts `template` (canonical) or `prompt` (alias the author models
    sometimes emit). Unknown extras are ignored so a single bad kwarg
    doesn't drop the block.
    """
    import json as _json
    import re as _re

    template = template or prompt or ""
    blanks = blanks or []
    eyebrow = title or label
    accept = [[str(a) for a in b.get("accept", [])] for b in blanks]
    placeholders = [b.get("placeholder", "____") for b in blanks]
    accept_json = _json.dumps(accept)

    rendered: list[str] = []
    last = 0
    for m in _re.finditer(r"\{\{(\d+)\}\}", template):
        rendered.append(_esc(template[last:m.start()]))
        idx = int(m.group(1))
        ph = placeholders[idx] if idx < len(placeholders) else "____"
        size = max(8, len(accept[idx][0]) + 2 if idx < len(accept) and accept[idx] else 10)
        rendered.append(
            f'<input class="b-fib-input" data-blank="{idx}" '
            f'placeholder="{_esc(str(ph), quote=True)}" size="{size}">'
        )
        last = m.end()
    rendered.append(_esc(template[last:]))

    hint_block = (
        '<details class="b-fib-hint-wrap">'
        '<summary class="b-btn-ghost">Hint</summary>'
        f'<div class="b-fib-hint">{_esc(hint)}</div>'
        '</details>' if hint else ""
    )
    eyebrow_block = (
        f'<div class="b-fib-label">{_esc(eyebrow)}</div>' if eyebrow else ""
    )

    return (
        '<div class="b-fib" data-fib '
        f'data-accept=\'{_esc(accept_json, quote=True)}\'>'
        f'{eyebrow_block}'
        f'<div class="b-fib-template">{"".join(rendered)}</div>'
        '<div class="b-fib-actions">'
        '<button class="b-btn-primary" data-action="check">Check</button>'
        f'{hint_block}'
        '<span class="b-fib-status" data-status></span>'
        '</div></div>'
    )


def match_pairs(
    *,
    prompt: str = "",
    pairs: list[dict[str, str]] | None = None,
    title: str = "",      # accepted alias — author models sometimes emit `title`
    label: str = "",      # accepted alias
    **_extras: Any,       # swallow unknown kwargs instead of crashing the render
) -> str:
    """<MatchPairs> — two columns; click pairs to match. Right column shuffled
    deterministically at render time.

    Accepts `prompt` (canonical) and tolerates `title` / `label` as aliases
    the author models sometimes emit. Unknown extras are ignored so a single
    bad kwarg doesn't drop the entire block.
    """
    pairs = pairs or []
    eyebrow = prompt or title or label
    # Authors usually omit the `id` field (the design schema treats it as
    # optional). Without per-pair ids the JS comparison `selL === selR` becomes
    # `"None" === "None"` and every click registers as correct. Synthesize a
    # stable id from the row index when one isn't supplied so each pair has a
    # unique key.
    keyed_pairs = [
        {
            "id": str(p.get("id") if p.get("id") is not None else f"p{i}"),
            "left": p.get("left", ""),
            "right": p.get("right", ""),
        }
        for i, p in enumerate(pairs)
    ]
    # Deterministic shuffle (matches the design's algorithm: j = (i*7 + 3) % (i+1)).
    rights = [{"id": p["id"], "label": p["right"]} for p in keyed_pairs]
    for i in range(len(rights) - 1, 0, -1):
        j = (i * 7 + 3) % (i + 1)
        rights[i], rights[j] = rights[j], rights[i]

    left_html = "".join(
        f'<div class="b-mp-card b-mp-left" data-id="{_esc(p["id"], quote=True)}">'
        f'{_esc(p["left"])}</div>'
        for p in keyed_pairs
    )
    right_html = "".join(
        f'<div class="b-mp-card b-mp-right" data-id="{_esc(r["id"], quote=True)}">'
        f'{_esc(r["label"])}</div>'
        for r in rights
    )
    return (
        '<div class="b-mp" data-mp '
        f'data-total="{len(pairs)}">'
        f'<div class="b-mp-prompt"><strong>Match</strong>{_esc(eyebrow)}</div>'
        '<div class="b-mp-grid">'
        f'<div class="b-mp-col">{left_html}</div>'
        f'<div class="b-mp-col">{right_html}</div>'
        '</div>'
        '<div class="b-mp-status" data-status>'
        f'0 / {len(pairs)} matched</div>'
        '</div>'
    )


def parameter_slider(
    *,
    label: str,
    min: float = 0,
    max: float = 10,
    step: float = 0.1,
    default: float | None = None,
    formula_tex: str = "",
    expr: str = "",
) -> str:
    """<ParameterSlider> — slider that drives a JS expression.

    `expr`: a JavaScript expression in `v` (the slider value). The result
    is rendered as text inside the live area. Trusted (author-provided).
    Example: `expr="(v*v).toFixed(2)"`.
    """
    if default is None:
        default = (float(min) + float(max)) / 2
    formula_html = (
        f'<div class="b-ps-formula">{_block_math(formula_tex)}</div>'
        if formula_tex else ""
    )
    return (
        '<div class="b-ps" data-parameter-slider '
        f'data-expr="{_esc(expr, quote=True)}" '
        f'data-step="{step}">'
        f'<div class="b-ps-label">{_esc(label)}</div>'
        f'{formula_html}'
        '<div class="b-ps-row">'
        f'<input class="b-ps-input" type="range" min="{min}" max="{max}" '
        f'step="{step}" value="{default}">'
        f'<div class="b-ps-value" data-value>{default}</div>'
        '</div>'
        '<div class="b-ps-render" data-render></div>'
        '</div>'
    )


def build_equation(
    *,
    prompt: str,
    tokens: list[Any],
    correct: list[Any] | None = None,
    answer: Any = None,
    **_extra: Any,
) -> str:
    """<BuildEquation> — drag/click chips to assemble an expression.

    `tokens` accepts either `[{id, label, math?}]` or `[label_str, ...]`. If
    `math` is set on a dict token, the chip renders that LaTeX inline.
    `correct`: list of token ids in order — optional when tokens are strings
    (defaults to the supplied order).
    `answer`: alternate spelling some authors use; if `correct` is omitted but
    `answer` is provided as a list of token ids, it's used as the answer.
    `**_extra` swallows author-friendly extras (`explanation`, `label`, …)
    rather than crashing the page.
    """
    import json as _json
    norm_tokens: list[dict[str, Any]] = []
    for i, tk in enumerate(tokens):
        if isinstance(tk, dict):
            norm_tokens.append({
                "id": str(tk.get("id", str(i))),
                "label": str(tk.get("label", "")),
                "math": tk.get("math"),
            })
        else:
            norm_tokens.append({"id": str(i), "label": str(tk), "math": None})
    if (correct is None or not correct) and isinstance(answer, list):
        correct = answer
    if correct is None or not correct:
        correct = [t["id"] for t in norm_tokens]
    correct_json = _json.dumps([str(x) for x in correct])

    chips: list[str] = []
    for tk in norm_tokens:
        token_id = _esc(tk["id"], quote=True)
        if tk.get("math"):
            content = _inline_math(tk["math"])
        else:
            content = _esc(tk["label"])
        chips.append(
            f'<button class="b-chip" data-token="{token_id}">{content}</button>'
        )

    return (
        '<div class="b-build-eq" data-build-eq '
        f'data-correct=\'{_esc(correct_json, quote=True)}\'>'
        f'<div class="b-build-eq-prompt"><strong>Build</strong>{_esc(prompt)}</div>'
        '<div class="b-build-eq-slots" data-slots>'
        '<span class="b-build-eq-placeholder">Tap tokens below to assemble…</span>'
        '</div>'
        f'<div class="b-build-eq-chips">{"".join(chips)}</div>'
        '<div class="b-build-eq-actions">'
        '<button class="b-btn-primary" data-action="check">Check</button>'
        '<button class="b-btn-ghost" data-action="clear">Clear</button>'
        '<span class="b-build-eq-status" data-status></span>'
        '</div></div>'
    )


def estimate_range(
    *,
    question: str,
    actual: float,
    min: float = 0,
    max: float = 100,
    unit: str = "",
    generous_width: float | None = None,
) -> str:
    """<EstimateRange> — user picks a min/max range; reveal-on-click shows the
    actual answer + whether their range bracketed it tightly or loosely."""
    lo, hi = float(min), float(max)
    if generous_width is None:
        generous_width = (hi - lo) * 0.1
    initial_lo = lo + (hi - lo) * 0.3
    initial_hi = lo + (hi - lo) * 0.7
    return (
        '<div class="b-er" data-estimate-range '
        f'data-min="{min}" data-max="{max}" data-actual="{actual}" '
        f'data-unit="{_esc(unit, quote=True)}" '
        f'data-generous="{generous_width}">'
        f'<div class="b-er-prompt"><strong>Estimate</strong>{_esc(question)}</div>'
        '<div class="b-er-track">'
        '<div class="b-er-axis"></div>'
        '<div class="b-er-band" data-band></div>'
        '<div class="b-er-actual" data-actual hidden>'
          '<div class="b-er-actual-bar"></div>'
          '<div class="b-er-actual-dot"></div>'
          '<div class="b-er-actual-label" data-actual-label></div>'
        '</div>'
        f'<input class="b-er-input b-er-lo" type="range" min="{min}" max="{max}" value="{initial_lo}" data-lo>'
        f'<input class="b-er-input b-er-hi" type="range" min="{min}" max="{max}" value="{initial_hi}" data-hi>'
        '</div>'
        '<div class="b-er-readout">'
        f'<span>min · {min}{_esc(unit)}</span>'
        '<span>your range: <strong data-readout></strong></span>'
        f'<span>max · {max}{_esc(unit)}</span>'
        '</div>'
        '<div class="b-er-actions">'
        '<button class="b-btn-primary" data-action="reveal">Reveal answer</button>'
        '<button class="b-btn-ghost" data-action="reset" hidden>Reset</button>'
        '<span class="b-er-status" data-status></span>'
        '</div></div>'
    )


def confidence_poll(
    *,
    question: str,
    options: list[dict[str, str]],
    commentary: str = "",
) -> str:
    """<ConfidencePoll> — calibration poll. Options laid out as a strip;
    on click the choice highlights and (option- or default-) commentary reveals."""
    n = len(options)
    opts_html: list[str] = []
    for i, o in enumerate(options):
        opts_html.append(
            f'<button class="b-cp-option" data-cp-idx="{i}" '
            f'data-commentary="{_esc(o.get("commentary", commentary), quote=True)}">'
            f'<div class="b-cp-option-num">{i+1}/{n}</div>'
            f'<div class="b-cp-option-label">{_esc(o.get("label", ""))}</div>'
            f'</button>'
        )
    grid_style = f'grid-template-columns:repeat({n},1fr)' if n else ''
    return (
        '<div class="b-cp" data-confidence-poll>'
        f'<div class="b-cp-prompt"><strong>Calibration</strong>{_esc(question)}</div>'
        f'<div class="b-cp-options" style="{grid_style}">{"".join(opts_html)}</div>'
        '<div class="b-cp-commentary" data-commentary hidden></div>'
        '</div>'
    )

