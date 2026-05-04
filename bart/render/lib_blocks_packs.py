"""Domain packs — additional design-library components for ML, CS, philosophy,
math, and comparative literature.

These are the Python ports of the new component packs added to the design
library (`project/library/{ml,cs,phil,math,lit}-pack.jsx`). Each function
returns a static HTML string that mirrors its JSX counterpart; interactive
state from the prototypes is rendered as the initial / canonical view (the
packet is print-friendly and ships no React).

All functions are skeletal — every visual attribute is data-driven, so the
agent can plug any subject's content without per-domain branching here.

Naming: Python kebab-case fence names map to snake_case functions. Class
names on emitted HTML use the existing `b-` prefix so styles in
`blocks.css` cascade naturally.
"""
from __future__ import annotations

from html import escape as _esc
from typing import Any

from .lib_blocks import _attr, _inline_md, fig_caption


# ─── Tokens (mirrored from tokens.jsx, only the colors we actually use) ─

_PAPER_HI = "var(--paper-hi)"
_PAPER = "var(--paper)"
_RULE = "var(--rule)"
_INK = "var(--ink)"
_INK_SOFT = "var(--ink-soft)"
_INK_MUTE = "var(--ink-mute)"
_INK_FAINT = "var(--ink-faint)"
_ACCENT = "var(--accent)"
_ACCENT_LO = "var(--accent-lo)"
_ACCENT_TINT = "var(--accent-tint)"
_INFO = "var(--info)"
_INFO_TINT = "var(--info-tint)"
_OK = "var(--ok)"
_OK_TINT = "var(--ok-tint)"
_WARN = "var(--warn)"
_WARN_TINT = "var(--warn-tint)"


def _label(title: str) -> str:
    """The small all-caps label that titles each figure."""
    return (
        f'<div class="b-pack-label">{_esc(title)}</div>'
        if title else ""
    )


def _wrap_caption(caption: str) -> str:
    return fig_caption(caption) if caption else ""


# ─── ML pack ──────────────────────────────────────────────────────


def confusion_matrix(
    *,
    classes: list[str],
    counts: list[list[int]],
    title: str = "",
    normalize: bool = False,
    caption: str = "",
) -> str:
    """Confusion matrix as an SVG heat-map. Diagonal cells use the accent
    palette so correct predictions stand out; off-diagonal use a neutral
    tint scaled by frequency."""
    n = len(classes)
    if not n or len(counts) != n or any(len(r) != n for r in counts):
        return ""
    cell = 64
    W = cell * (n + 1) + 80
    H = cell * (n + 1) + 60
    flat = [v for row in counts for v in row]
    mx = max(flat + [1])
    total = sum(flat) or 1
    parts: list[str] = []
    parts.append(_label(title))
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg" '
        f'preserveAspectRatio="xMidYMid meet" style="max-width:{W}px">'
    )
    parts.append(
        f'<text x="{W/2}" y="20" text-anchor="middle" class="b-pack-axis">PREDICTED →</text>'
    )
    parts.append(
        f'<text x="20" y="{H/2}" text-anchor="middle" class="b-pack-axis" '
        f'transform="rotate(-90 20 {H/2})">TRUE ↓</text>'
    )
    for j, c in enumerate(classes):
        x = 80 + cell * (j + 0.5)
        parts.append(
            f'<text x="{x}" y="42" text-anchor="middle" class="b-pack-cell-label">{_esc(c)}</text>'
        )
    for i, c in enumerate(classes):
        y = 50 + cell * (i + 0.5) + 4
        parts.append(
            f'<text x="70" y="{y}" text-anchor="end" class="b-pack-cell-label">{_esc(c)}</text>'
        )
    for i in range(n):
        for j in range(n):
            v = counts[i][j] / mx
            on_diag = i == j
            if on_diag:
                fill = f"rgba(170,90,50,{0.15 + 0.65 * v:.3f})"
                stroke = "var(--accent)"
                sw = "1.5"
                txt_class = "b-pack-cell-num diag"
            else:
                fill = f"rgba(70,80,90,{0.06 + 0.35 * v:.3f})"
                stroke = "var(--rule)"
                sw = "1"
                txt_class = "b-pack-cell-num"
            label = (
                f"{counts[i][j] / total * 100:.0f}%" if normalize else str(counts[i][j])
            )
            x = 80 + j * cell
            y = 50 + i * cell
            parts.append(
                f'<rect x="{x + 2}" y="{y + 2}" width="{cell - 4}" height="{cell - 4}" '
                f'rx="4" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
                f'<text x="{x + cell/2}" y="{y + cell/2 + 5}" text-anchor="middle" '
                f'class="{txt_class}">{label}</text>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-confusion-matrix">{"".join(parts)}</figure>'


def loss_curve(
    *,
    series: list[dict[str, Any]],
    title: str = "",
    x_label: str = "epoch",
    y_label: str = "loss",
    caption: str = "",
) -> str:
    """Line plot of one or more curves on shared axes.

    `series`: list of `{label, color?, data: [y values]}`. Colors fall back
    to accent/info palette per-series."""
    if not series:
        return ""
    W, H, pad = 600, 280, 50
    flat = [y for s in series for y in s.get("data", [])]
    if not flat:
        return ""
    y_max, y_min = max(flat), min(flat)
    x_max = max([len(s.get("data", [])) - 1 for s in series] + [1])

    def X(i: float) -> float:
        return pad + (i / x_max) * (W - pad * 2)

    def Y(y: float) -> float:
        return H - pad - ((y - y_min) / (y_max - y_min or 1)) * (H - pad * 2)

    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg b-pack-svg-paper">'
    )
    for t in (0, 0.25, 0.5, 0.75, 1):
        y = pad + t * (H - pad * 2)
        parts.append(
            f'<line x1="{pad}" y1="{y}" x2="{W - pad}" y2="{y}" '
            f'stroke="{_RULE}" stroke-dasharray="2 4" stroke-width="0.8"/>'
        )
    parts.append(
        f'<line x1="{pad}" y1="{H - pad}" x2="{W - pad}" y2="{H - pad}" stroke="{_INK}"/>'
        f'<line x1="{pad}" y1="{pad/2}" x2="{pad}" y2="{H - pad}" stroke="{_INK}"/>'
    )
    parts.append(
        f'<text x="{W - pad - 4}" y="{H - pad + 24}" text-anchor="end" '
        f'class="b-pack-axis-label">{_esc(x_label)}</text>'
    )
    parts.append(
        f'<text x="{pad + 8}" y="{pad/2 + 8}" class="b-pack-axis-label">{_esc(y_label)}</text>'
    )
    palette = [_ACCENT, _INFO, _OK, _WARN]
    for k, s in enumerate(series):
        data = s.get("data") or []
        if not data:
            continue
        color = s.get("color") or palette[k % len(palette)]
        d_parts = []
        for i, y in enumerate(data):
            d_parts.append(("L" if i else "M") + f" {X(i):.1f} {Y(y):.1f}")
        d = " ".join(d_parts)
        parts.append(
            f'<path d="{d}" stroke="{color}" stroke-width="2.2" fill="none"/>'
        )
        parts.append(
            f'<text x="{W - pad + 4}" y="{Y(data[-1]) + 4:.1f}" '
            f'class="b-pack-series-label" fill="{color}">{_esc(s.get("label", ""))}</text>'
        )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-loss-curve">{"".join(parts)}</figure>'


def neural_net_diagram(
    *,
    layers: list[dict[str, Any]],
    title: str = "",
    width: int = 480,
    height: int = 260,
    caption: str = "",
) -> str:
    """Stylized layer diagram. `layers` = `[{label, units}]`. First layer
    rendered in info-blue; last in accent."""
    if not layers:
        return ""
    pad = 40
    n_layers = len(layers)
    x_step = (width - pad * 2) / max(n_layers - 1, 1)
    positions: list[list[tuple[float, float]]] = []
    for li, L in enumerate(layers):
        units = max(int(L.get("units") or 1), 1)
        y_step = (height - pad * 2) / max(units - 1, 1)
        col = []
        for ui in range(units):
            x = pad + li * x_step
            y = height / 2 if units == 1 else pad + ui * y_step
            col.append((x, y))
        positions.append(col)
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg" style="max-width:{width}px">'
    )
    for li in range(n_layers - 1):
        for a in positions[li]:
            for b in positions[li + 1]:
                parts.append(
                    f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                    f'stroke="{_RULE}" stroke-width="0.8" opacity="0.6"/>'
                )
    for li, col in enumerate(positions):
        if li == 0:
            stroke = _INFO
        elif li == n_layers - 1:
            stroke = _ACCENT
        else:
            stroke = _INK
        for x, y in col:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{_PAPER_HI}" '
                f'stroke="{stroke}" stroke-width="1.6"/>'
            )
    for li, L in enumerate(layers):
        x = pad + li * x_step
        parts.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" '
            f'class="b-pack-axis">{_esc(str(L.get("label", "")))}</text>'
        )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-neural-net">{"".join(parts)}</figure>'


def attention_matrix(
    *,
    row_tokens: list[str],
    col_tokens: list[str],
    weights: list[list[float]],
    title: str = "",
    caption: str = "",
) -> str:
    """Token-to-token attention heat-map. Weights ∈ [0,1]."""
    nr, nc = len(row_tokens), len(col_tokens)
    if not nr or not nc or len(weights) != nr or any(len(r) != nc for r in weights):
        return ""
    cell = 36
    pad_l, pad_t = 90, 70
    W = pad_l + nc * cell + 20
    H = pad_t + nr * cell + 20
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg" style="max-width:{W}px">'
    )
    for j, t in enumerate(col_tokens):
        cx = pad_l + j * cell + cell / 2
        parts.append(
            f'<text x="{cx}" y="{pad_t - 6}" '
            f'transform="rotate(-35 {cx} {pad_t - 6})" '
            f'class="b-pack-token">{_esc(t)}</text>'
        )
    for i, t in enumerate(row_tokens):
        parts.append(
            f'<text x="{pad_l - 8}" y="{pad_t + i * cell + cell/2 + 4}" '
            f'text-anchor="end" class="b-pack-token">{_esc(t)}</text>'
        )
    for i in range(nr):
        for j in range(nc):
            w = max(0.0, min(float(weights[i][j]), 1.0))
            parts.append(
                f'<rect x="{pad_l + j * cell + 1}" y="{pad_t + i * cell + 1}" '
                f'width="{cell - 2}" height="{cell - 2}" rx="2" '
                f'fill="rgba(170,90,50,{0.06 + 0.85 * w:.3f})" '
                f'stroke="{_RULE}" stroke-width="0.5"/>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-attention-matrix">{"".join(parts)}</figure>'


def embedding_scatter(
    *,
    points: list[dict[str, Any]],
    title: str = "",
    width: int = 480,
    height: int = 360,
    caption: str = "",
) -> str:
    """2D scatter of embedding points. `points` = `[{x, y, label?, group?}]`."""
    if not points:
        return ""
    pad = 30
    xs = [float(p.get("x", 0)) for p in points]
    ys = [float(p.get("y", 0)) for p in points]
    x_min, x_max = min(xs) - 0.5, max(xs) + 0.5
    y_min, y_max = min(ys) - 0.5, max(ys) + 0.5
    x_span = (x_max - x_min) or 1
    y_span = (y_max - y_min) or 1
    group_color = {"A": _ACCENT, "B": _INFO, "C": _OK, "D": _WARN}

    def X(x: float) -> float:
        return pad + (x - x_min) / x_span * (width - pad * 2)

    def Y(y: float) -> float:
        return height - pad - (y - y_min) / y_span * (height - pad * 2)

    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
    )
    for p in points:
        c = group_color.get(p.get("group", ""), _INK)
        cx, cy = X(float(p.get("x", 0))), Y(float(p.get("y", 0)))
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{c}" opacity="0.85"/>'
        )
        if p.get("label"):
            parts.append(
                f'<text x="{cx + 7:.1f}" y="{cy + 4:.1f}" '
                f'class="b-pack-point-label">{_esc(str(p["label"]))}</text>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-embedding-scatter">{"".join(parts)}</figure>'


# ─── CS pack ─────────────────────────────────────────────────────


_CODE_KEYWORDS = (
    "def class return if elif else for while in import from as try except "
    "with lambda None True False and or not self function const let var new "
    "throw catch finally public private static void int float bool struct "
    "typedef extern sizeof"
).split()


def _highlight_code(line: str, lang: str) -> str:
    s = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    import re
    s = re.sub(
        r'(["\'`])((?:\\.|(?!\1).)*?)\1',
        r'<span class="b-code-str">\1\2\1</span>',
        s,
    )
    if lang in ("python", "shell"):
        s = re.sub(r"(#.*)$", r'<span class="b-code-com">\1</span>', s)
    else:
        s = re.sub(r"(//.*)$", r'<span class="b-code-com">\1</span>', s)
    kw_pat = r"\b(" + "|".join(_CODE_KEYWORDS) + r")\b"
    s = re.sub(kw_pat, r'<span class="b-code-kw">\1</span>', s)
    s = re.sub(r"(\b\d+(?:\.\d+)?\b)", r'<span class="b-code-num">\1</span>', s)
    return s


def code_block(
    *,
    lines: list[str],
    language: str = "python",
    title: str = "",
    annotations: list[dict[str, Any]] | None = None,
    caption: str = "",
) -> str:
    """Dark-theme syntax-highlighted code with optional inline annotations.

    `annotations`: `[{line: 1-based, text, color?}]`. Annotations render
    as hand-written notes in the right margin of their line."""
    if not lines:
        return ""
    annotations = annotations or []
    ann_by_line: dict[int, dict[str, Any]] = {a.get("line", 0): a for a in annotations}
    head = (
        f'<div class="b-pack-label">{_esc(title)} · {_esc(language)}</div>'
        if title else ""
    )
    rows = []
    for i, ln in enumerate(lines):
        n = i + 1
        ann = ann_by_line.get(n)
        marker = ""
        if ann:
            color = _esc(ann.get("color", "#e8b070"))
            marker = (
                f'<span class="b-code-ann" style="color:{color}">'
                f'← {_esc(ann.get("text", ""))}</span>'
            )
        rows.append(
            f'<tr><td class="b-code-num-cell">{n}</td>'
            f'<td class="b-code-line">{_highlight_code(ln, language)}{marker}</td></tr>'
        )
    body = (
        f'<div class="b-code-block">'
        f'<table><tbody>{"".join(rows)}</tbody></table></div>'
    )
    return (
        f'<figure class="b-pack b-code">{head}{body}{_wrap_caption(caption)}</figure>'
    )


def call_stack(
    *,
    frames: list[dict[str, Any]],
    title: str = "",
    caption: str = "",
) -> str:
    """Function call stack — top frame highlighted, bottom labeled.

    `frames`: `[{fn, args?: [{name?, value}], locals?: [{name, value}]}]`.
    Order: `frames[-1]` is the currently-running frame at the top."""
    if not frames:
        return ""
    rows = []
    n = len(frames)
    for i, f in enumerate(frames):
        is_top = i == n - 1
        args = f.get("args") or []
        arg_str = ", ".join(_esc(str(a.get("value", ""))) for a in args)
        locals_html = ""
        loc = f.get("locals") or []
        if loc:
            spans = " ".join(
                f'<span>{_esc(l.get("name", ""))}={_esc(str(l.get("value", "")))}</span>'
                for l in loc
            )
            locals_html = f'<div class="b-frame-locals">{spans}</div>'
        cls = "b-frame b-frame-top" if is_top else "b-frame"
        rows.append(
            f'<div class="{cls}">'
            f'<div class="b-frame-fn">{_esc(str(f.get("fn", "")))}({arg_str})</div>'
            f'{locals_html}'
            f'</div>'
        )
    # column-reverse stacking via CSS so frames[-1] visually sits at top.
    rows_html = "".join(rows)
    return (
        f'<figure class="b-pack b-call-stack">{_label(title)}'
        f'<div class="b-call-stack-body">'
        f'<div class="b-frame-marker top">↑ STACK TOP (running)</div>'
        f'{rows_html}'
        f'<div class="b-frame-marker bottom">STACK BOTTOM</div>'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def memory_layout(
    *,
    regions: list[dict[str, Any]],
    title: str = "",
    caption: str = "",
) -> str:
    """Heap/stack/text region columns. `regions`: `[{name, color?, items: [{addr, label, value?}]}]`."""
    if not regions:
        return ""
    cols = []
    for r in regions:
        items_html = []
        for it in r.get("items") or []:
            value_span = (
                f'<span class="b-mem-val">= {_esc(str(it.get("value")))}</span>'
                if it.get("value") is not None else ""
            )
            items_html.append(
                f'<div class="b-mem-row">'
                f'<span class="b-mem-addr">{_esc(str(it.get("addr", "")))}</span>'
                f'<span class="b-mem-label">{_esc(str(it.get("label", "")))}</span>'
                f'{value_span}'
                f'</div>'
            )
        color_style = f' style="color:{_esc(r["color"])}"' if r.get("color") else ""
        cols.append(
            f'<div class="b-mem-region">'
            f'<div class="b-mem-region-title"{color_style}>{_esc(str(r.get("name", "")))}</div>'
            f'<div class="b-mem-rows">{"".join(items_html)}</div>'
            f'</div>'
        )
    return (
        f'<figure class="b-pack b-memory-layout">{_label(title)}'
        f'<div class="b-memory-layout-body">{"".join(cols)}</div>'
        f'{_wrap_caption(caption)}</figure>'
    )


def binary_tree(
    *,
    root: dict[str, Any],
    title: str = "",
    width: int = 480,
    height: int = 280,
    caption: str = "",
) -> str:
    """Balanced binary-tree visual. `root` is a recursive `{value, left?, right?}`."""
    if not root:
        return ""
    nodes: list[dict[str, Any]] = []
    edges: list[tuple[float, float, float, float]] = []

    def walk(n: dict[str, Any], depth: int, lo: float, hi: float) -> None:
        x = (lo + hi) / 2
        y = 30 + depth * 70
        nodes.append({"x": x, "y": y, "value": n.get("value", "")})
        if n.get("left"):
            edges.append((x, y, (lo + x) / 2, 30 + (depth + 1) * 70))
            walk(n["left"], depth + 1, lo, x)
        if n.get("right"):
            edges.append((x, y, (x + hi) / 2, 30 + (depth + 1) * 70))
            walk(n["right"], depth + 1, x, hi)

    walk(root, 0, 30, width - 30)
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
    )
    for x1, y1, x2, y2 in edges:
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{_RULE}" stroke-width="1.5"/>'
        )
    for n in nodes:
        parts.append(
            f'<circle cx="{n["x"]:.1f}" cy="{n["y"]:.1f}" r="18" '
            f'fill="{_PAPER}" stroke="{_INK}" stroke-width="1.8"/>'
            f'<text x="{n["x"]:.1f}" y="{n["y"] + 5:.1f}" text-anchor="middle" '
            f'class="b-pack-node-label">{_esc(str(n["value"]))}</text>'
        )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-binary-tree">{"".join(parts)}</figure>'


def process_timeline(
    *,
    processes: list[dict[str, Any]],
    total_time: int = 20,
    title: str = "",
    caption: str = "",
) -> str:
    """Gantt-style scheduling diagram. `processes`: `[{name, color?, segments: [{start, dur, kind?: run|wait|io}]}]`."""
    if not processes:
        return ""
    W = 700
    row_h = 36
    pad_l, pad_t = 80, 30
    H = pad_t + len(processes) * row_h + 30

    def X(t: float) -> float:
        return pad_l + (t / max(total_time, 1)) * (W - pad_l - 20)

    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg b-pack-svg-paper">'
    )
    for t in range(total_time + 1):
        x = X(t)
        parts.append(
            f'<line x1="{x:.1f}" y1="{pad_t - 4}" x2="{x:.1f}" y2="{H - 22}" '
            f'stroke="{_RULE}" stroke-dasharray="2 3" stroke-width="0.6"/>'
        )
        if t % 2 == 0:
            parts.append(
                f'<text x="{x:.1f}" y="{H - 6}" text-anchor="middle" '
                f'class="b-pack-axis">{t}</text>'
            )
    for pi, p in enumerate(processes):
        y_label = pad_t + pi * row_h + row_h / 2 + 4
        parts.append(
            f'<text x="{pad_l - 8}" y="{y_label}" text-anchor="end" '
            f'class="b-pack-row-label">{_esc(str(p.get("name", "")))}</text>'
        )
        for s in p.get("segments") or []:
            kind = s.get("kind", "run")
            x0 = X(s.get("start", 0))
            x1 = X(s.get("start", 0) + s.get("dur", 0))
            w = max(0.0, x1 - x0 - 1)
            if kind == "wait":
                fill = "transparent"
                stroke = _RULE
                dash = ' stroke-dasharray="3 3"'
            elif kind == "io":
                fill = _INFO
                stroke = "none"
                dash = ""
            else:
                fill = p.get("color") or _ACCENT
                stroke = "none"
                dash = ""
            parts.append(
                f'<rect x="{x0:.1f}" y="{pad_t + pi * row_h + 4}" '
                f'width="{w:.1f}" height="{row_h - 12}" rx="3" '
                f'fill="{fill}" stroke="{stroke}"{dash}/>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-process-timeline">{"".join(parts)}</figure>'


# ─── Phil pack ────────────────────────────────────────────────────


def argument_map(
    *,
    premises: list[str],
    conclusion: str,
    title: str = "",
    caption: str = "",
) -> str:
    """Premises stacked above a horizontal rule with `∴ conclusion` below."""
    if not premises or not conclusion:
        return ""
    rows = "".join(
        f'<div class="b-arg-row">'
        f'<span class="b-arg-tag">P{i + 1}</span>'
        f'<span class="b-arg-text">{_inline_md(p)}</span>'
        f'</div>'
        for i, p in enumerate(premises)
    )
    return (
        f'<figure class="b-pack b-argument-map">{_label(title)}'
        f'<div class="b-arg-body">'
        f'{rows}'
        f'<div class="b-arg-rule"></div>'
        f'<div class="b-arg-row b-arg-conclusion">'
        f'<span class="b-arg-tag b-arg-tag-then">∴</span>'
        f'<span class="b-arg-text">{_inline_md(conclusion)}</span>'
        f'</div>'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def truth_table(
    *,
    vars: list[str],
    formula: str,
    rows: list[dict[str, Any]],
    title: str = "",
    caption: str = "",
) -> str:
    """Logic truth table. `rows`: `[{values: [bool,...], result: bool}]`."""
    if not vars or not rows:
        return ""
    head = (
        "<tr>"
        + "".join(f'<th class="b-tt-var">{_esc(v)}</th>' for v in vars)
        + f'<th class="b-tt-formula">{_esc(formula)}</th>'
        + "</tr>"
    )
    body_rows = []
    for r in rows:
        vals = r.get("values") or []
        cells = "".join(
            f'<td class="b-tt-cell {"on" if v else "off"}">{"T" if v else "F"}</td>'
            for v in vals
        )
        result = r.get("result", False)
        body_rows.append(
            f'<tr>{cells}'
            f'<td class="b-tt-result {"on" if result else "off"}">'
            f'{"T" if result else "F"}</td></tr>'
        )
    return (
        f'<figure class="b-pack b-truth-table">{_label(title)}'
        f'<div class="b-tt-wrap">'
        f'<table><thead>{head}</thead><tbody>{"".join(body_rows)}</tbody></table>'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def venn_logic(
    *,
    sets: list[dict[str, Any]] | None = None,
    shaded: list[str] | None = None,
    title: str = "",
    caption: str = "",
) -> str:
    """Two-circle Venn. `sets` = `[{label, cx, cy, r}, ...]`. `shaded`
    accepts `'A'`, `'B'`, `'AB'`."""
    sets = sets or [
        {"label": "A", "cx": 130, "cy": 110, "r": 70},
        {"label": "B", "cx": 230, "cy": 110, "r": 70},
    ]
    shaded = shaded or []
    if len(sets) < 2:
        return ""
    A, B = sets[0], sets[1]
    W, H = 360, 220
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg b-pack-svg-paper" style="max-width:{W}px">'
        f'<defs>'
        f'<clipPath id="bvenn-A"><circle cx="{A["cx"]}" cy="{A["cy"]}" r="{A["r"]}"/></clipPath>'
        f'</defs>'
    )
    if "A" in shaded:
        parts.append(
            f'<circle cx="{A["cx"]}" cy="{A["cy"]}" r="{A["r"]}" fill="{_ACCENT_TINT}"/>'
        )
    if "B" in shaded:
        parts.append(
            f'<circle cx="{B["cx"]}" cy="{B["cy"]}" r="{B["r"]}" fill="{_ACCENT_TINT}"/>'
        )
    if "AB" in shaded:
        parts.append(
            f'<g clip-path="url(#bvenn-A)">'
            f'<circle cx="{B["cx"]}" cy="{B["cy"]}" r="{B["r"]}" fill="{_ACCENT}" fill-opacity="0.5"/>'
            f'</g>'
        )
    parts.append(
        f'<circle cx="{A["cx"]}" cy="{A["cy"]}" r="{A["r"]}" fill="none" '
        f'stroke="{_INK}" stroke-width="1.8"/>'
        f'<circle cx="{B["cx"]}" cy="{B["cy"]}" r="{B["r"]}" fill="none" '
        f'stroke="{_INK}" stroke-width="1.8"/>'
        f'<text x="{A["cx"] - A["r"]/2}" y="{A["cy"] - A["r"] - 6}" '
        f'class="b-venn-label">{_esc(str(A.get("label", "A")))}</text>'
        f'<text x="{B["cx"] + B["r"]/2}" y="{B["cy"] - B["r"] - 6}" '
        f'class="b-venn-label">{_esc(str(B.get("label", "B")))}</text>'
    )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-venn-logic">{"".join(parts)}</figure>'


def dialectic_tree(
    *,
    thesis: str,
    antithesis: str,
    synthesis: str,
    title: str = "",
    caption: str = "",
) -> str:
    """Hegelian thesis / antithesis / synthesis frame."""

    def _box(label: str, text: str, color: str, bold: bool = False) -> str:
        weight = "600" if bold else "400"
        return (
            f'<div class="b-dial-box" style="border-color:{color}">'
            f'<div class="b-dial-label" style="color:{color}">{_esc(label)}</div>'
            f'<div class="b-dial-text" style="font-weight:{weight}">{_inline_md(text)}</div>'
            f'</div>'
        )

    return (
        f'<figure class="b-pack b-dialectic-tree">{_label(title)}'
        f'<div class="b-dial-body">'
        f'<div class="b-dial-row">'
        f'{_box("Thesis", thesis, _INFO)}'
        f'{_box("Antithesis", antithesis, _WARN)}'
        f'</div>'
        f'<div class="b-dial-arrow">↓</div>'
        f'<div class="b-dial-syn">'
        f'{_box("Synthesis", synthesis, _ACCENT, bold=True)}'
        f'</div>'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def quote_pull(
    *,
    quote: str,
    attribution: str = "",
    work: str = "",
    caption: str = "",
) -> str:
    """Editorial pull-quote block (large italic serif)."""
    if not quote:
        return ""
    work_part = (
        f'<span class="b-qp-work">, {_esc(work)}</span>' if work else ""
    )
    attr = (
        f'<div class="b-qp-attr">— <strong>{_esc(attribution)}</strong>{work_part}</div>'
        if attribution else ""
    )
    return (
        f'<figure class="b-pack b-quote-pull">'
        f'<div class="b-qp-body">'
        f'<div class="b-qp-text">"{_inline_md(quote)}"</div>'
        f'{attr}'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


# ─── Math pack ────────────────────────────────────────────────────


def proof_block(
    *,
    steps: list[dict[str, Any]],
    given: list[str] | None = None,
    qed: bool = True,
    title: str = "",
    caption: str = "",
) -> str:
    """Two-column proof: numbered statement | justification, with optional
    `Given:` preamble and ∎ at the end. Distinct from `proof-ladder` —
    this one renders with statement+justification on the same row, the
    ladder uses vertical reasoning steps."""
    if not steps:
        return ""
    given_html = ""
    if given:
        given_html = (
            f'<div class="b-pf-given">'
            f'<em class="b-pf-given-label">Given.</em> '
            f'<span>{"; ".join(_inline_md(g) for g in given)}.</span>'
            f'</div>'
        )
    rows = []
    for i, s in enumerate(steps):
        rows.append(
            f'<tr>'
            f'<td class="b-pf-num">{i + 1}.</td>'
            f'<td class="b-pf-stmt">{_inline_md(str(s.get("statement", "")))}</td>'
            f'<td class="b-pf-just">{_inline_md(str(s.get("justification", "")))}</td>'
            f'</tr>'
        )
    qed_html = '<div class="b-pf-qed">∎</div>' if qed else ""
    return (
        f'<figure class="b-pack b-proof-block">{_label(title)}'
        f'<div class="b-pf-body">'
        f'{given_html}'
        f'<table><tbody>{"".join(rows)}</tbody></table>'
        f'{qed_html}'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def matrix_view(
    *,
    rows: list[list[Any]],
    label: str = "",
    title: str = "",
    highlight: dict[str, Any] | None = None,
    caption: str = "",
) -> str:
    """Matrix with bracket markers and optional row/col/cell highlight.

    `highlight` accepts `{"row": i}`, `{"col": j}`, or `{"cell": [i, j]}`."""
    if not rows or not rows[0]:
        return ""
    h = highlight or {}
    h_row = h.get("row")
    h_col = h.get("col")
    h_cell = h.get("cell")

    def cell_class(i: int, j: int) -> str:
        hi = (
            (h_row is not None and h_row == i)
            or (h_col is not None and h_col == j)
            or (h_cell and h_cell[0] == i and h_cell[1] == j)
        )
        return "b-mx-cell hi" if hi else "b-mx-cell"

    body_rows = "".join(
        "<tr>"
        + "".join(f'<td class="{cell_class(i, j)}">{_esc(str(v))}</td>'
                  for j, v in enumerate(r))
        + "</tr>"
        for i, r in enumerate(rows)
    )
    label_html = (
        f'<span class="b-mx-label">{_esc(label)} =</span>' if label else ""
    )
    return (
        f'<figure class="b-pack b-matrix-view">{_label(title)}'
        f'<div class="b-mx-row">{label_html}'
        f'<div class="b-mx-bracket">'
        f'<span class="b-mx-bk b-mx-bk-l"></span>'
        f'<span class="b-mx-bk b-mx-bk-r"></span>'
        f'<table><tbody>{body_rows}</tbody></table>'
        f'</div></div>{_wrap_caption(caption)}</figure>'
    )


def graph_plot(
    *,
    fns: list[dict[str, Any]],
    x_range: tuple[float, float] = (-5, 5),
    y_range: tuple[float, float] = (-5, 5),
    marks: list[dict[str, Any]] | None = None,
    width: int = 480,
    height: int = 320,
    title: str = "",
    caption: str = "",
) -> str:
    """Cartesian plot. `fns`: `[{points: [[x,y],...], color?, label?}]`.

    The design version takes JS callable `f(x)` — we sample x values
    server-side; the agent provides points (or we sample if `expr` was
    one of a few common forms). Pass `points` directly for arbitrary
    curves."""
    if not fns:
        return ""
    pad = 30
    x0, x1 = x_range
    y0, y1 = y_range
    x_span = (x1 - x0) or 1
    y_span = (y1 - y0) or 1

    def X(x: float) -> float:
        return pad + (x - x0) / x_span * (width - pad * 2)

    def Y(y: float) -> float:
        return height - pad - (y - y0) / y_span * (height - pad * 2)

    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
    )
    for i in range(int(x0), int(x1) + 1):
        parts.append(
            f'<line x1="{X(i):.1f}" y1="{pad}" x2="{X(i):.1f}" y2="{height - pad}" '
            f'stroke="{_RULE}" stroke-width="0.5"/>'
        )
    for j in range(int(y0), int(y1) + 1):
        parts.append(
            f'<line x1="{pad}" y1="{Y(j):.1f}" x2="{width - pad}" y2="{Y(j):.1f}" '
            f'stroke="{_RULE}" stroke-width="0.5"/>'
        )
    parts.append(
        f'<line x1="{pad}" y1="{Y(0):.1f}" x2="{width - pad}" y2="{Y(0):.1f}" '
        f'stroke="{_INK}" stroke-width="1.2"/>'
        f'<line x1="{X(0):.1f}" y1="{pad}" x2="{X(0):.1f}" y2="{height - pad}" '
        f'stroke="{_INK}" stroke-width="1.2"/>'
    )
    palette = [_ACCENT, _INFO, _OK, _WARN]
    for k, fn in enumerate(fns):
        color = fn.get("color") or palette[k % len(palette)]
        pts = fn.get("points") or []
        if not pts:
            continue
        d_parts = []
        for i, (px, py) in enumerate(pts):
            d_parts.append(("L" if i else "M") + f" {X(px):.1f} {Y(py):.1f}")
        parts.append(
            f'<path d="{" ".join(d_parts)}" stroke="{color}" stroke-width="2.2" fill="none"/>'
        )
        if fn.get("label"):
            last_x, last_y = pts[-1]
            parts.append(
                f'<text x="{X(last_x):.1f}" y="{Y(last_y) - 6:.1f}" text-anchor="end" '
                f'class="b-pack-fn-label" fill="{color}">{_esc(str(fn["label"]))}</text>'
            )
    for m in marks or []:
        parts.append(
            f'<circle cx="{X(m.get("x", 0)):.1f}" cy="{Y(m.get("y", 0)):.1f}" '
            f'r="4" fill="{_ACCENT}"/>'
        )
        if m.get("label"):
            parts.append(
                f'<text x="{X(m.get("x", 0)) + 8:.1f}" y="{Y(m.get("y", 0)) - 6:.1f}" '
                f'class="b-pack-mark-label">{_esc(str(m["label"]))}</text>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-graph-plot">{"".join(parts)}</figure>'


def integral_area(
    *,
    points: list[list[float]],
    a: float,
    b: float,
    x_range: tuple[float, float] = (-1, 5),
    y_range: tuple[float, float] = (-1, 5),
    width: int = 480,
    height: int = 300,
    title: str = "",
    caption: str = "",
) -> str:
    """Curve plot with shaded area between `a` and `b` under the curve.

    `points` is the curve sampled across `x_range`. We slice it to
    `[a, b]` for the shaded polygon."""
    if not points:
        return ""
    pad = 30
    x0, x1 = x_range
    y0, y1 = y_range
    x_span = (x1 - x0) or 1
    y_span = (y1 - y0) or 1

    def X(x: float) -> float:
        return pad + (x - x0) / x_span * (width - pad * 2)

    def Y(y: float) -> float:
        return height - pad - (y - y0) / y_span * (height - pad * 2)

    in_band = [(px, py) for px, py in points if a <= px <= b]
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
        f'<line x1="{pad}" y1="{Y(0):.1f}" x2="{width - pad}" y2="{Y(0):.1f}" '
        f'stroke="{_INK}" stroke-width="1"/>'
        f'<line x1="{X(0):.1f}" y1="{pad}" x2="{X(0):.1f}" y2="{height - pad}" '
        f'stroke="{_INK}" stroke-width="1"/>'
    )
    if in_band:
        shade = [f"M {X(a):.1f} {Y(0):.1f}"]
        for px, py in in_band:
            shade.append(f"L {X(px):.1f} {Y(py):.1f}")
        shade.append(f"L {X(b):.1f} {Y(0):.1f}")
        shade.append("Z")
        parts.append(
            f'<path d="{" ".join(shade)}" fill="{_ACCENT_TINT}" stroke="none"/>'
        )
    curve = []
    for i, (px, py) in enumerate(points):
        curve.append(("L" if i else "M") + f" {X(px):.1f} {Y(py):.1f}")
    parts.append(
        f'<path d="{" ".join(curve)}" stroke="{_ACCENT}" stroke-width="2.2" fill="none"/>'
    )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-integral-area">{"".join(parts)}</figure>'


# ─── Lit pack ─────────────────────────────────────────────────────


def passage_annotated(
    *,
    passage: str,
    annotations: list[dict[str, str]] | None = None,
    title: str = "",
    attribution: str = "",
    caption: str = "",
) -> str:
    """Two-column layout: passage with marked phrases on the left, marginal
    notes on the right. `annotations`: `[{phrase, note}]`."""
    if not passage:
        return ""
    annotations = annotations or []
    segs: list[dict[str, Any]] = []
    remaining = passage
    notes: list[dict[str, Any]] = []
    for i, a in enumerate(annotations):
        phrase = a.get("phrase", "")
        if not phrase:
            continue
        idx = remaining.find(phrase)
        if idx < 0:
            continue
        if idx > 0:
            segs.append({"text": remaining[:idx]})
        segs.append({"text": phrase, "marker": i + 1})
        notes.append({"marker": i + 1, "note": a.get("note", "")})
        remaining = remaining[idx + len(phrase):]
    if remaining:
        segs.append({"text": remaining})
    seg_html = []
    for s in segs:
        if s.get("marker"):
            seg_html.append(
                f'<span class="b-pa-mark">{_esc(s["text"])}'
                f'<sup class="b-pa-sup">{s["marker"]}</sup></span>'
            )
        else:
            seg_html.append(_esc(s["text"]))
    attr_html = (
        f'<div class="b-pa-attr">— {_esc(attribution)}</div>'
        if attribution else ""
    )
    notes_html = "".join(
        f'<div class="b-pa-note">'
        f'<sup class="b-pa-sup">{n["marker"]}</sup>{_inline_md(n["note"])}'
        f'</div>'
        for n in notes
    )
    return (
        f'<figure class="b-pack b-passage-annotated">{_label(title)}'
        f'<div class="b-pa-body">'
        f'<div class="b-pa-passage">{"".join(seg_html)}{attr_html}</div>'
        f'<div class="b-pa-notes">{notes_html}</div>'
        f'</div>{_wrap_caption(caption)}</figure>'
    )


def character_graph(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    title: str = "",
    width: int = 520,
    height: int = 360,
    caption: str = "",
) -> str:
    """Character relationship graph. Edges are typed:
    `love` (accent), `rival` (warn, dashed), `family` (info), `allied` (ok)."""
    if not nodes:
        return ""
    by_id = {n.get("id"): n for n in nodes}
    color_for = {
        "love": _ACCENT, "rival": _WARN, "family": _INFO, "allied": _OK,
    }
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
    )
    for e in edges:
        a = by_id.get(e.get("from")), by_id.get(e.get("to"))
        if not all(a):
            continue
        a, b = a
        kind = e.get("kind", "")
        color = color_for.get(kind, _INK)
        dash = ' stroke-dasharray="5 4"' if kind == "rival" else ""
        ax, ay = float(a.get("x", 0)), float(a.get("y", 0))
        bx, by_ = float(b.get("x", 0)), float(b.get("y", 0))
        mx, my = (ax + bx) / 2, (ay + by_) / 2
        parts.append(
            f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by_}" '
            f'stroke="{color}" stroke-width="1.6"{dash}/>'
        )
        if e.get("label"):
            parts.append(
                f'<text x="{mx}" y="{my - 4}" text-anchor="middle" '
                f'class="b-pack-edge-label" fill="{color}">{_esc(str(e["label"]))}</text>'
            )
    for n in nodes:
        x, y = float(n.get("x", 0)), float(n.get("y", 0))
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="22" fill="{_PAPER}" '
            f'stroke="{_INK}" stroke-width="1.8"/>'
            f'<text x="{x}" y="{y + 5}" text-anchor="middle" '
            f'class="b-pack-graph-node">{_esc(str(n.get("label", "")))}</text>'
        )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-character-graph">{"".join(parts)}</figure>'


def theme_weave(
    *,
    chapters: list[str],
    themes: list[str],
    presence: list[list[float]],
    title: str = "",
    caption: str = "",
) -> str:
    """Per-chapter ribbon thickness chart — thicker = more thematic intensity.
    `presence` = `[themes][chapters]` ∈ [0,1]."""
    nC, nT = len(chapters), len(themes)
    if not nC or not nT or len(presence) != nT:
        return ""
    W = 600
    pad_l, pad_r, pad_t, row_h = 100, 30, 24, 38
    H = pad_t + nT * row_h + 30
    col_w = (W - pad_l - pad_r) / nC
    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-pack-svg b-pack-svg-paper">'
    )
    for j, c in enumerate(chapters):
        parts.append(
            f'<text x="{pad_l + col_w * (j + 0.5)}" y="{pad_t - 8}" '
            f'text-anchor="middle" class="b-pack-axis">{_esc(c)}</text>'
        )
    for i, t in enumerate(themes):
        cy = pad_t + i * row_h + row_h / 2
        parts.append(
            f'<text x="{pad_l - 10}" y="{cy + 4}" text-anchor="end" '
            f'class="b-pack-theme-label">{_esc(t)}</text>'
        )
        row_pres = presence[i] if i < len(presence) else []
        for j in range(nC):
            v = max(0.0, min(float(row_pres[j] if j < len(row_pres) else 0), 1.0))
            h = 2 + v * 22
            parts.append(
                f'<rect x="{pad_l + col_w * j + 1}" y="{cy - h/2}" '
                f'width="{col_w - 2}" height="{h}" rx="2" '
                f'fill="rgba(170,90,50,{0.15 + v * 0.65:.3f})"/>'
            )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-theme-weave">{"".join(parts)}</figure>'


def style_spectrum(
    *,
    axis_x: tuple[str, str],
    axis_y: tuple[str, str],
    items: list[dict[str, Any]],
    title: str = "",
    caption: str = "",
    width: int = 480,
    height: int = 360,
) -> str:
    """2-axis position map. `items` = `[{label, x, y}]` with x,y ∈ [-1, 1]."""
    if not items:
        return ""
    pad = 50

    def X(x: float) -> float:
        return pad + (x + 1) / 2 * (width - pad * 2)

    def Y(y: float) -> float:
        return height - pad - (y + 1) / 2 * (height - pad * 2)

    parts = [_label(title)]
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-pack-svg b-pack-svg-paper" '
        f'style="max-width:{width}px">'
        f'<line x1="{pad}" y1="{height/2}" x2="{width - pad}" y2="{height/2}" '
        f'stroke="{_INK}" stroke-width="1"/>'
        f'<line x1="{width/2}" y1="{pad}" x2="{width/2}" y2="{height - pad}" '
        f'stroke="{_INK}" stroke-width="1"/>'
        f'<text x="{pad - 4}" y="{height/2 + 4}" text-anchor="end" '
        f'class="b-pack-axis">{_esc(axis_x[0])}</text>'
        f'<text x="{width - pad + 4}" y="{height/2 + 4}" '
        f'class="b-pack-axis">{_esc(axis_x[1])}</text>'
        f'<text x="{width/2}" y="{pad - 6}" text-anchor="middle" '
        f'class="b-pack-axis">{_esc(axis_y[1])}</text>'
        f'<text x="{width/2}" y="{height - pad + 16}" text-anchor="middle" '
        f'class="b-pack-axis">{_esc(axis_y[0])}</text>'
    )
    for it in items:
        x, y = float(it.get("x", 0)), float(it.get("y", 0))
        parts.append(
            f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="5" fill="{_ACCENT}"/>'
            f'<text x="{X(x) + 8:.1f}" y="{Y(y) + 4:.1f}" '
            f'class="b-pack-point-label">{_esc(str(it.get("label", "")))}</text>'
        )
    parts.append("</svg>")
    parts.append(_wrap_caption(caption))
    return f'<figure class="b-pack b-style-spectrum">{"".join(parts)}</figure>'
