"""Visual bart-block renderers — formula cards, diagrams, timelines, maps.

``formula-card`` / ``worked-example`` / ``mnemonic-card`` / ``trap-callout`` /
``why-it-matters`` / ``concept-build`` / ``flowchart`` / ``timeline`` /
``concept-map`` / ``comparison-matrix`` / ``process-ribbon`` /
``anatomy-diagram`` / ``number-line`` / ``proof-ladder`` / ``annotated-quote``.
Static visuals (some SVG-backed); the interactive ones live in ``interactives``.

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
from .primitives import (
    fig_caption,
    stamp,
)


# ─── Visuals ──────────────────────────────────────────────────────


def formula_card(
    tex: str,
    *,
    title: str = "",
    legend: list[dict[str, str]] | None = None,
    note: str = "",
    stamp_label: str = "",
    cite: str = "",
) -> str:
    """<FormulaCard> — boxed equation + optional legend + handwritten note.

    `cite`: short corpus citation (e.g., 'Lecture 4 §2', 'HW7 P3'). Renders
    as a small chip in the card footer. Lets the reader trace; lets a
    future verifier check.
    """
    parts: list[str] = ['<div class="b-formula-card">']
    if stamp_label:
        parts.append(f'<div class="b-formula-card-stamp">{stamp(stamp_label)}</div>')
    if title:
        parts.append(f'<div class="b-formula-card-title">{_esc(title)}</div>')
    parts.append(f'<div class="b-formula-card-body">{_block_math(tex)}</div>')
    if legend:
        parts.append('<div class="b-formula-card-legend">')
        for entry in legend:
            sym = entry.get("symbol", "")
            meaning = entry.get("meaning", "")
            parts.append(
                f'<span class="b-sym">{_inline_math(sym)}</span>'
                f'<span class="b-meaning">{_esc(meaning)}</span>'
            )
        parts.append("</div>")
    if note:
        parts.append(f'<div class="b-formula-card-note">{_esc(note)}</div>')
    if cite:
        parts.append(
            f'<div class="b-cite">'
            f'<span class="b-cite-label">cite</span>'
            f'<span class="b-cite-text">{_esc(cite)}</span>'
            f'</div>'
        )
    parts.append("</div>")
    return "".join(parts)


def worked_example(
    *,
    problem: str,
    steps: list[dict[str, str]],
    answer: str = "",
    tag_label: str = "Worked example",
    cite: str = "",
) -> str:
    """<WorkedExample> — problem → numbered solution steps → answer.

    Each step: {action, math?, reasoning?}. `math` is LaTeX (no delimiters).
    `cite`: short corpus citation (e.g., 'HW3 §2 P1') — rendered as a chip.
    """
    parts: list[str] = [
        '<div class="b-worked-example">',
        f'<div class="b-worked-example-tag"><span>{_esc(tag_label)}</span></div>',
        '<div class="b-worked-example-body">',
        f'<div class="b-worked-example-problem"><strong>Problem.</strong>{_block_md(problem)}</div>',
        '<ol>',
    ]
    for s in steps:
        action = _block_md(s.get("action", ""))
        math = s.get("math")
        reasoning = s.get("reasoning")
        item = [f'<li><div class="b-step-action">{action}</div>']
        if math:
            item.append(f'<div>{_block_math(math)}</div>')
        if reasoning:
            item.append(f'<div class="b-step-reasoning">{_block_md(reasoning)}</div>')
        item.append("</li>")
        parts.append("".join(item))
    parts.append("</ol>")
    if answer:
        parts.append(
            f'<div class="b-answer">'
            f'<span class="b-answer-label">Answer</span>'
            f'<span class="b-answer-text">{_block_md(answer)}</span>'
            f'</div>'
        )
    if cite:
        parts.append(
            f'<div class="b-cite">'
            f'<span class="b-cite-label">cite</span>'
            f'<span class="b-cite-text">{_esc(cite)}</span>'
            f'</div>'
        )
    parts.append("</div></div>")
    return "".join(parts)


def mnemonic_card(
    *,
    acronym: str = "",
    expansion: list[dict[str, str]] | None = None,
    story: str = "",
    title: str = "",      # accepted alias — author models often emit `title`
    label: str = "",      # accepted alias — author models often emit `label`
    **_extras: Any,       # swallow other unknown kwargs instead of crashing the page
) -> str:
    """<MnemonicCard> — acronym + per-letter expansion + optional story.

    `title` / `label` overrides the default "Mnemonic" eyebrow; both are
    accepted because the author agents in different prompt revisions have
    used both spellings. `**_extras` is a render-time safety net: when an
    author hallucinates a key like `cite` or `tag`, the page still renders
    instead of dropping the entire block as a render_error.
    """
    eyebrow = title or label or "Mnemonic"
    parts: list[str] = ['<div class="b-mnemonic-card">',
                        f'<div class="b-mnemonic-card-label">{_esc(eyebrow)}</div>']
    if acronym:
        parts.append(f'<div class="b-mnemonic-card-acronym">{_esc(acronym)}</div>')
    if expansion:
        parts.append('<div class="b-mnemonic-card-expansion">')
        for row in expansion:
            parts.append(
                f'<span class="b-letter">{_esc(row.get("letter", ""))}</span>'
                f'<span class="b-letter-text">{_esc(row.get("text", ""))}</span>'
            )
        parts.append("</div>")
    if story:
        parts.append(f'<div class="b-mnemonic-card-story">{_esc(story)}</div>')
    parts.append("</div>")
    return "".join(parts)


_TRAP_KINDS = {
    "trap": ("", "Common trap", "!"),
    "warn": ("b-trap-warn", "Watch out", "!"),
    "note": ("b-trap-note", "Note", "i"),
    "ok":   ("b-trap-ok", "Tip", "✓"),
}


def trap_callout(*, kind: str = "trap", title: str = "", body: str) -> str:
    """<TrapCallout> — colored side-rail callout. Kind: trap | warn | note | ok."""
    cls_extra, default_label, icon = _TRAP_KINDS.get(kind, _TRAP_KINDS["trap"])
    label = title or default_label
    cls = "b-trap-callout" + (f" {cls_extra}" if cls_extra else "")
    return (
        f'<div class="{cls}">'
        f'<span class="b-trap-callout-icon">{_esc(icon)}</span>'
        f'<div class="b-trap-callout-body">'
        f'<div class="b-trap-callout-title">{_esc(label)}</div>'
        f'<div class="b-trap-callout-content">{_block_md(body)}</div>'
        f'</div></div>'
    )


def concept_build(
    *,
    name: str,
    motivate: str = "",
    define: str = "",
    define_tex: str = "",
    example: str = "",
    example_tex: str = "",
    connect: str = "",
    contrast: str = "",
    apply: str = "",
    threshold: bool = False,
) -> str:
    """<ConceptBuild> — the MOTIVATE → NAME → GROUND → CONNECT → CONTRAST → APPLY
    teaching arc for a single load-bearing concept.

    Each rung is optional, but the renderer only emits the ones provided so
    a partial concept-build still looks intentional. `define_tex` and
    `example_tex` accept LaTeX (rendered as block math).
    """
    parts: list[str] = ['<div class="b-concept-build">']
    header_extra = ""
    if threshold:
        header_extra = '<span class="b-concept-build-threshold">THRESHOLD</span>'
    parts.append(
        f'<div class="b-concept-build-header">'
        f'<span class="b-concept-build-name">{_esc(name)}</span>'
        f'{header_extra}</div>'
    )

    rungs = [
        ("motivate",  "Why we need this", motivate, ""),
        ("define",    "Definition",        define,   define_tex),
        ("example",   "Concrete example",  example,  example_tex),
        ("connect",   "Connection",        connect,  ""),
        ("contrast",  "Not the same as",   contrast, ""),
        ("apply",     "Apply it",          apply,    ""),
    ]
    for slug, label, prose, tex in rungs:
        if not prose and not tex:
            continue
        rung_parts = [
            f'<div class="b-concept-build-rung b-concept-rung-{slug}">',
            f'<div class="b-concept-rung-label">{label}</div>',
        ]
        if prose:
            rung_parts.append(f'<div class="b-concept-rung-body">{_block_md(prose)}</div>')
        if tex:
            rung_parts.append(f'<div class="b-concept-rung-math">{_block_math(tex)}</div>')
        rung_parts.append("</div>")
        parts.append("".join(rung_parts))
    parts.append("</div>")
    return "".join(parts)


def why_it_matters(body: str, *, bart_svg: str = "") -> str:
    """<WhyItMatters> — eyebrow callout, optionally with a bart loaf glyph."""
    bart_html = (
        f'<div class="b-why-it-matters-bart">{bart_svg}</div>' if bart_svg else ""
    )
    return (
        '<div class="b-why-it-matters">'
        f'{bart_html}'
        '<div class="b-why-it-matters-eyebrow">Why this matters</div>'
        f'<div class="b-why-it-matters-content">{_block_md(body)}</div>'
        '</div>'
    )


def flowchart(steps: list[dict[str, str]], *, title: str = "") -> str:
    """<Flowchart> — vertical chain of nodes with arrows.

    Each step: {label, body?, tone?}. tone: 'paper' | 'accent'.
    """
    arrow = (
        '<div class="b-flow-arrow">'
        '<svg viewBox="0 0 16 32" width="16" height="32">'
        '<path d="M 8 2 Q 6 16 8 26" stroke="currentColor" stroke-width="1.5" '
        'fill="none" stroke-linecap="round" opacity="0.4"/>'
        '<path d="M 8 26 L 4 22 M 8 26 L 12 22" stroke="currentColor" '
        'stroke-width="1.5" fill="none" stroke-linecap="round" opacity="0.4"/>'
        '</svg></div>'
    )
    parts: list[str] = ['<div class="b-flowchart">']
    if title:
        parts.append(f'<div class="b-flowchart-title">{_esc(title)}</div>')
    for i, step in enumerate(steps):
        tone = step.get("tone", "paper")
        cls = "b-flow-node" + (" b-flow-node-accent" if tone == "accent" else "")
        idx = f'<span class="b-flow-node-index">{i+1:02d}</span>'
        label = f'<span class="b-flow-node-label">{_esc(step.get("label", ""))}</span>'
        node = f'<div class="{cls}"><div>{idx}{label}</div>'
        if step.get("body"):
            node += f'<div class="b-flow-node-body">{_esc(step["body"])}</div>'
        node += "</div>"
        parts.append(node)
        if i < len(steps) - 1:
            parts.append(arrow)
    parts.append("</div>")
    return "".join(parts)


def timeline(events: list[dict[str, Any]], *, title: str = "", unit: str = "year") -> str:
    """<Timeline> — horizontal events on a wobbly axis. Alternating up/down.

    `events`: [{date, label, body?, tone?}]; tone in {"ink", "accent"}.
    """
    n = max(len(events), 1)
    parts: list[str] = ['<figure class="b-timeline">']
    if title:
        parts.append(f'<div class="b-timeline-title">{_esc(title)}</div>')
    parts.append(
        '<svg class="b-timeline-axis" viewBox="0 0 1000 14" preserveAspectRatio="none">'
        '<path d="M 0 8 Q 250 4 500 7 T 1000 7" stroke="currentColor" '
        'stroke-width="1.2" fill="none" opacity="0.6"/></svg>'
    )
    parts.append(
        f'<div class="b-timeline-track" style="grid-template-columns:repeat({n},1fr)">'
    )
    for i, e in enumerate(events):
        flip = i % 2 == 1
        tone = "accent" if e.get("tone") == "accent" else "ink"
        item_cls = "b-timeline-item" + (" b-timeline-flipped" if flip else "")
        body = (
            f'<div class="b-timeline-body">{_esc(e.get("body", ""))}</div>'
            if e.get("body") else ""
        )
        parts.append(
            f'<div class="{item_cls}" data-tone="{tone}">'
            f'<div class="b-timeline-dot"></div>'
            f'<div class="b-timeline-stem"></div>'
            f'<div class="b-timeline-info">'
            f'<div class="b-timeline-date">{_esc(str(e.get("date", "")))}</div>'
            f'<div class="b-timeline-label">{_esc(e.get("label", ""))}</div>'
            f'{body}'
            f'</div></div>'
        )
    parts.append("</div>")
    parts.append(f'<div class="b-timeline-unit">per {_esc(unit)}</div>')
    parts.append("</figure>")
    return "".join(parts)


def concept_map(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    title: str = "",
    aspect_ratio: str = "3/2",
) -> str:
    """<ConceptMap> — loose node graph with curved arrows.

    `nodes`: [{id, x, y, label, kind?}] x/y in 0-100 (percent),
             kind in {"plain", "hub"} (hub = pill-shaped, ink-filled).
    `edges`: [{from, to, label?, curve?}].
    """
    by_id = {n.get("id"): n for n in nodes}

    arrows: list[str] = []
    for e in edges:
        a = by_id.get(e.get("from"))
        b = by_id.get(e.get("to"))
        if not a or not b:
            continue
        # Map percent coords to viewBox 1000 × 666 (matches design's 3:2).
        x1 = float(a.get("x", 0)) * 10
        y1 = float(a.get("y", 0)) * 6.66
        x2 = float(b.get("x", 0)) * 10
        y2 = float(b.get("y", 0)) * 6.66
        cx = (x1 + x2) / 2 + float(e.get("curve", 0)) * 40
        cy = (y1 + y2) / 2 - 30
        path = (
            f'<path d="M {x1} {y1} Q {cx} {cy} {x2} {y2}" '
            f'stroke="currentColor" stroke-width="1.2" fill="none" '
            f'marker-end="url(#cm-arr)" opacity="0.55"/>'
        )
        arrows.append(path)
        if e.get("label"):
            arrows.append(
                f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" '
                f'class="b-concept-edge-label">{_esc(e["label"])}</text>'
            )

    nodes_html: list[str] = []
    for n in nodes:
        is_hub = n.get("kind") == "hub"
        cls = "b-concept-node" + (" b-concept-node-hub" if is_hub else "")
        x = float(n.get("x", 50))
        y = float(n.get("y", 50))
        nodes_html.append(
            f'<div class="{cls}" style="left:{x}%;top:{y}%">{_esc(n.get("label", ""))}</div>'
        )

    parts = ['<figure class="b-concept-map">']
    if title:
        parts.append(f'<div class="b-concept-map-title">{_esc(title)}</div>')
    parts.append(
        f'<div class="b-concept-map-canvas" style="aspect-ratio:{_esc(aspect_ratio)}">'
        f'<svg viewBox="0 0 1000 666" preserveAspectRatio="none" class="b-concept-svg">'
        f'<defs><marker id="cm-arr" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>'
        + "".join(arrows)
        + "</svg>"
        + "".join(nodes_html)
        + "</div></figure>"
    )
    return "".join(parts)


_CMP_GLYPHS = {
    "yes":     ("●", "ok"),
    "no":      ("○", "faint"),
    "partial": ("◐", "warn"),
    "star":    ("★", "accent"),
}


def comparison_matrix(
    *,
    cols: list[dict[str, str]],
    rows: list[dict[str, Any]],
    title: str = "",
    legend: bool = True,
) -> str:
    """<ComparisonMatrix> — feature-by-option grid with mark glyphs.

    `cols`: [{id, label}].
    `rows`: [{label, cells: {<colId>: 'yes'|'no'|'partial'|'star'|<freeform>}}].
    """
    parts: list[str] = ['<figure class="b-cmp">']
    if title:
        parts.append(f'<div class="b-cmp-title">{_esc(title)}</div>')
    parts.append('<table class="b-cmp-table"><thead><tr><th></th>')
    for c in cols:
        parts.append(f'<th>{_esc(c.get("label", ""))}</th>')
    parts.append("</tr></thead><tbody>")
    for r in rows:
        parts.append(f'<tr><td class="b-cmp-row-label">{_esc(r.get("label", ""))}</td>')
        cells = r.get("cells") or {}
        for c in cols:
            v = cells.get(c.get("id"))
            if v in _CMP_GLYPHS:
                glyph, tone = _CMP_GLYPHS[v]
                parts.append(
                    f'<td class="b-cmp-cell"><span class="b-cmp-glyph b-cmp-glyph-{tone}" '
                    f'aria-label="{v}">{glyph}</span></td>'
                )
            else:
                txt = "—" if v is None else str(v)
                parts.append(f'<td class="b-cmp-cell b-cmp-text">{_esc(txt)}</td>')
        parts.append("</tr>")
    parts.append("</tbody></table>")
    if legend:
        items = []
        for k, (glyph, tone) in _CMP_GLYPHS.items():
            items.append(
                f'<span><span class="b-cmp-glyph b-cmp-glyph-{tone}">{glyph}</span>{k}</span>'
            )
        parts.append(f'<div class="b-cmp-legend">{"".join(items)}</div>')
    parts.append("</figure>")
    return "".join(parts)


def process_ribbon(
    phases: list[dict[str, Any]],
    *,
    title: str = "",
    outcome: str = "",
) -> str:
    """<ProcessRibbon> — chevroned phases left-to-right.

    `phases`: [{label, beats?: [str], color?: 'accent'|'warn'|'info'|'ok'}].
    """
    color_class = {
        "accent": "b-ribbon-accent",
        "warn":   "b-ribbon-warn",
        "info":   "b-ribbon-info",
        "ok":     "b-ribbon-ok",
    }
    palette_cycle = ("accent", "warn", "info", "ok")

    parts: list[str] = ['<figure class="b-ribbon">']
    if title:
        parts.append(f'<div class="b-ribbon-title">{_esc(title)}</div>')
    parts.append('<div class="b-ribbon-track">')
    n = len(phases)
    for i, p in enumerate(phases):
        chosen = p.get("color") or palette_cycle[i % len(palette_cycle)]
        cls = "b-ribbon-phase " + color_class.get(chosen, "b-ribbon-accent")
        if i == n - 1:
            cls += " b-ribbon-last"
        if i == 0:
            cls += " b-ribbon-first"
        beats_html = ""
        if p.get("beats"):
            beats_html = (
                '<ul class="b-ribbon-beats">'
                + "".join(f"<li>{_esc(b)}</li>" for b in p["beats"])
                + "</ul>"
            )
        parts.append(
            f'<div class="{cls}">'
            f'<div class="b-ribbon-phase-num">Phase {i+1}</div>'
            f'<div class="b-ribbon-phase-label">{_esc(p.get("label", ""))}</div>'
            f'{beats_html}'
            f'</div>'
        )
    parts.append("</div>")
    if outcome:
        parts.append(f'<div class="b-ribbon-outcome">{_esc(outcome)}</div>')
    parts.append("</figure>")
    return "".join(parts)


def anatomy_diagram(
    *,
    image_html: str,
    left_labels: list[dict[str, Any]] | None = None,
    right_labels: list[dict[str, Any]] | None = None,
    title: str = "",
    caption: str = "",
    height: int = 360,
) -> str:
    """<AnatomyDiagram> — labeled diagram with leader lines on both sides.

    `image_html`: trusted HTML to drop into the canvas.
    `left_labels` / `right_labels`: [{y, label, body?}] with y in 0-100 (percent).
    Labels are auto-numbered (left first, then right).
    """
    left = left_labels or []
    right = right_labels or []

    def _lab(items: list[dict[str, Any]], *, side: str, offset: int) -> str:
        out = []
        for i, l in enumerate(items):
            y = float(l.get("y", 50))
            num = i + offset + 1
            body_html = (
                f'<div class="b-anatomy-body">{_esc(l.get("body", ""))}</div>'
                if l.get("body") else ""
            )
            out.append(
                f'<div class="b-anatomy-label b-anatomy-{side}" style="top:{y}%">'
                f'<div class="b-anatomy-num">{num:02d}</div>'
                f'<div class="b-anatomy-label-text">{_esc(l.get("label", ""))}</div>'
                f'{body_html}'
                f'</div>'
            )
        return "".join(out)

    parts: list[str] = ['<figure class="b-anatomy">']
    if title:
        parts.append(f'<div class="b-anatomy-title">{_esc(title)}</div>')
    parts.append(
        f'<div class="b-anatomy-grid" style="height:{int(height)}px">'
        f'<div class="b-anatomy-side">{_lab(left, side="left", offset=0)}</div>'
        f'<div class="b-anatomy-canvas">{image_html}</div>'
        f'<div class="b-anatomy-side">{_lab(right, side="right", offset=len(left))}</div>'
        f'</div>'
    )
    if caption:
        parts.append(fig_caption(caption))
    parts.append("</figure>")
    return "".join(parts)


def number_line(
    *,
    min: float = 0,
    max: float = 10,
    ticks: list[float] | None = None,
    points: list[dict[str, Any]] | None = None,
    intervals: list[dict[str, Any]] | None = None,
    title: str = "",
    height: int = 140,
) -> str:
    """<NumberLine> — real number line with ticks, points, intervals.

    `points`:    [{x, label?, color?}]  (color: 'accent' | 'warn' | 'ok' | 'info' or hex).
    `intervals`: [{from, to, label?, openLeft?, openRight?, color?}].
    """
    ticks = ticks or []
    points = points or []
    intervals = intervals or []

    # Local aliases — `min`/`max` are also the keyword args, so we capture
    # them once into non-shadowing locals to keep callable builtins available.
    lo, hi = float(min), float(max)
    w = 1000
    pad = 40
    inner = w - pad * 2

    def X(v: float) -> float:
        return pad + ((v - lo) / (hi - lo)) * inner if hi != lo else pad

    parts: list[str] = ['<figure class="b-numline">']
    if title:
        parts.append(f'<div class="b-numline-title">{_esc(title)}</div>')

    svg_parts = [f'<svg viewBox="0 0 {w} {height}" class="b-numline-svg">']

    # Intervals (drawn under axis)
    for iv in intervals:
        a = X(float(iv.get("from", 0)))
        b = X(float(iv.get("to", 0)))
        rect_w = b - a if b > a else 0
        color_attr = _esc(str(iv.get("color", "")), quote=True) or "var(--accent)"
        tint = "var(--accent-tint)" if not iv.get("color") else color_attr
        svg_parts.append(
            f'<rect x="{a}" y="{height/2 - 9}" width="{rect_w}" height="18" '
            f'rx="2" fill="{tint}" opacity="0.6"/>'
        )
        for end_x, open_flag in ((a, iv.get("openLeft")), (b, iv.get("openRight"))):
            fill = "var(--paper)" if open_flag else color_attr
            svg_parts.append(
                f'<circle cx="{end_x}" cy="{height/2}" r="6" fill="{fill}" '
                f'stroke="{color_attr}" stroke-width="2"/>'
            )
        if iv.get("label"):
            svg_parts.append(
                f'<text x="{(a+b)/2}" y="{height/2 - 18}" text-anchor="middle" '
                f'class="b-numline-iv-label">{_esc(iv["label"])}</text>'
            )

    # Axis
    svg_parts.append(
        f'<line x1="{pad}" y1="{height/2}" x2="{w - pad}" y2="{height/2}" '
        f'stroke="currentColor" stroke-width="1.4"/>'
        f'<path d="M {w-pad} {height/2} l -10 -5 m 10 5 l -10 5" '
        f'stroke="currentColor" stroke-width="1.4" fill="none"/>'
    )
    for t in ticks:
        x = X(float(t))
        svg_parts.append(
            f'<line x1="{x}" y1="{height/2 - 6}" x2="{x}" y2="{height/2 + 6}" '
            f'stroke="currentColor" stroke-width="1"/>'
            f'<text x="{x}" y="{height/2 + 26}" text-anchor="middle" '
            f'class="b-numline-tick">{_esc(str(t))}</text>'
        )
    for p in points:
        x = X(float(p.get("x", 0)))
        color = _esc(str(p.get("color", "")), quote=True) or "currentColor"
        svg_parts.append(
            f'<circle cx="{x}" cy="{height/2}" r="6" fill="{color}"/>'
        )
        if p.get("label"):
            svg_parts.append(
                f'<text x="{x}" y="{height/2 - 14}" text-anchor="middle" '
                f'class="b-numline-pt-label" fill="{color}">{_esc(p["label"])}</text>'
            )

    svg_parts.append("</svg>")
    parts.append("".join(svg_parts))
    parts.append("</figure>")
    return "".join(parts)


def proof_ladder(
    *,
    steps: list[dict[str, str]],
    title: str = "Proof",
    given: str = "",
    prove: str = "",
) -> str:
    """<ProofLadder> — two-column proof. `steps`: [{statement, justification, kind?}]."""
    parts: list[str] = ['<figure class="b-proof">']
    parts.append(f'<div class="b-proof-title">{_esc(title)}</div>')
    if given:
        parts.append(
            f'<div class="b-proof-line"><strong>Given</strong>{_esc(given)}</div>'
        )
    if prove:
        parts.append(
            f'<div class="b-proof-line"><strong>Prove</strong>{_esc(prove)}</div>'
        )
    parts.append(
        '<table class="b-proof-table"><thead><tr>'
        '<th></th><th>Statement</th><th>Reason</th></tr></thead><tbody>'
    )
    for i, s in enumerate(steps):
        is_qed = s.get("kind") == "qed"
        cls = "b-proof-row"
        if is_qed:
            cls += " b-proof-qed"
        statement = _esc(s.get("statement", ""))
        if is_qed:
            statement += '<span class="b-proof-tomb">∎</span>'
        parts.append(
            f'<tr class="{cls}">'
            f'<td class="b-proof-num">{i+1}.</td>'
            f'<td class="b-proof-stmt">{statement}</td>'
            f'<td class="b-proof-just">{_esc(s.get("justification", ""))}</td>'
            f'</tr>'
        )
    parts.append("</tbody></table></figure>")
    return "".join(parts)


def annotated_quote(
    *,
    quote: str,
    annotations: list[dict[str, str]] | None = None,
    source: str = "",
) -> str:
    """<AnnotatedQuote> — pull-quote with margin notes for specific phrases.

    `annotations`: [{phrase, note}]; first occurrence of each phrase is marked.
    """
    annotations = annotations or []

    # Tokenize the quote: walk through, replacing first occurrence of each
    # phrase with a marked span carrying its index.
    parts_list: list[tuple[str, int | None]] = [(quote, None)]
    for idx, a in enumerate(annotations):
        phrase = a.get("phrase", "")
        if not phrase:
            continue
        new_parts: list[tuple[str, int | None]] = []
        replaced = False
        for text, note in parts_list:
            if note is not None or replaced:
                new_parts.append((text, note))
                continue
            i = text.find(phrase)
            if i < 0:
                new_parts.append((text, note))
                continue
            new_parts.append((text[:i], None))
            new_parts.append((phrase, idx))
            new_parts.append((text[i + len(phrase):], None))
            replaced = True
        parts_list = [p for p in new_parts if p[0]]

    quote_html: list[str] = []
    for text, note in parts_list:
        if note is None:
            quote_html.append(_esc(text))
        else:
            quote_html.append(
                f'<mark class="b-aq-mark">{_esc(text)}'
                f'<sup class="b-aq-sup">{note + 1}</sup></mark>'
            )
    source_html = (
        f'<footer class="b-aq-source">— {_esc(source)}</footer>' if source else ""
    )
    notes_html = "".join(
        f'<div class="b-aq-note">'
        f'<div class="b-aq-note-num">N. {i+1:02d}</div>'
        f'<div class="b-aq-note-body">{_esc(a.get("note", ""))}</div>'
        f'</div>'
        for i, a in enumerate(annotations)
    )
    return (
        '<figure class="b-aq">'
        '<blockquote class="b-aq-quote">'
        f'“{"".join(quote_html)}”'
        f'{source_html}'
        '</blockquote>'
        f'<aside class="b-aq-margin">{notes_html}</aside>'
        '</figure>'
    )

