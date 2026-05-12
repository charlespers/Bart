"""Chemistry bart-block renderers — molecules, reactions, energy, orbitals.

``molecule-diagram`` (2-D atoms + bonds + curved arrows) / ``reaction-equation``
/ ``arrow-pushing`` / ``energy-diagram`` / ``orbital-diagram`` / ``ph-scale`` /
``periodic-snippet`` / ``balance-equation`` / ``isomer-spotter`` /
``titration-curve`` / ``electron-config``. Surfaced in daily-lesson catalogs
when the subject keywords look like chemistry.

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

from html import escape as _esc
from typing import Any

from .primitives import (
    fig_caption,
    highlight,
)


# ─── Chemistry ────────────────────────────────────────────────────


_CHEM_EL_COLOR = {
    "O": "#b8472a", "N": "#4a6a7a", "S": "#b8893a",
    "Cl": "#5e7a4a", "Br": "#9a4628", "F": "#5e7a4a", "P": "#b8893a",
}


def molecule_diagram(
    *,
    atoms: list[dict[str, Any]],
    bonds: list[dict[str, Any]] | None = None,
    width: int = 360,
    height: int = 240,
    pad: int = 30,
    label: str = "",
    caption: str = "",
    highlights: list[Any] | None = None,
    arrows: list[dict[str, Any]] | None = None,
) -> str:
    """<MoleculeDiagram> — 2D skeletal structure renderer.

    `atoms`: [{id, x, y, el, charge?, lonePairs?}].
    `bonds`: [{a, b, order?, kind?}], kind in {"wedge", "dash"}.
    `highlights`: list of atom ids OR bond indices to color with accent.
    `arrows`: mechanism curly-arrows: [{from, to, kind?, curve?}], where
              from/to is an atom id, [aId,bId] for bond midpoint, or {x,y}.
    """
    bonds = bonds or []
    highlights = highlights or []
    arrows = arrows or []
    if not atoms:
        return ""

    xs = [float(a.get("x", 0)) for a in atoms]
    ys = [float(a.get("y", 0)) for a in atoms]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    sx = (max_x - min_x) or 1
    sy = (max_y - min_y) or 1
    inner_w = width - pad * 2
    inner_h = height - pad * 2
    scale = min(inner_w / sx, inner_h / sy)
    ox = pad + (inner_w - sx * scale) / 2
    oy = pad + (inner_h - sy * scale) / 2

    def _X(a):
        return ox + (float(a.get("x", 0)) - min_x) * scale

    def _Y(a):
        return oy + (float(a.get("y", 0)) - min_y) * scale

    by_id = {a.get("id"): a for a in atoms}

    def _show_label(a):
        if a.get("el") != "C":
            return True
        if a.get("charge"):
            return True
        return not any(b.get("a") == a.get("id") or b.get("b") == a.get("id") for b in bonds)

    def _el_color(el):
        return _CHEM_EL_COLOR.get(el, "var(--ink)")

    parts: list[str] = []
    if label:
        parts.append(f'<div class="b-mol-label">{_esc(label)}</div>')
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-mol-svg" width="100%" '
        f'style="max-width:{width}px">'
    )
    parts.append(
        '<defs>'
        '<marker id="curly-arr" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/></marker>'
        '<marker id="curly-arr-half" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M 0 0 L 10 5" fill="none" stroke="var(--accent)" stroke-width="1.5"/></marker>'
        '</defs>'
    )

    # ── Bonds ────────────────────────────────────────────────
    import math
    for i, b in enumerate(bonds):
        A = by_id.get(b.get("a"))
        B = by_id.get(b.get("b"))
        if A is None or B is None:
            continue
        x1, y1 = _X(A), _Y(A)
        x2, y2 = _X(B), _Y(B)
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy) or 1
        ux, uy = dx / length, dy / length
        sA = 12 if _show_label(A) else 0
        sB = 12 if _show_label(B) else 0
        ax = x1 + ux * sA
        ay = y1 + uy * sA
        bx = x2 - ux * sB
        by = y2 - uy * sB
        hi = i in highlights
        stroke = "var(--accent)" if hi else "var(--ink)"
        sw = 2.4 if hi else 1.6

        kind = b.get("kind")
        order = b.get("order", 1)
        if kind == "wedge":
            px, py = -uy, ux
            w = 5
            parts.append(
                f'<polygon points="{ax},{ay} {bx + px*w},{by + py*w} '
                f'{bx - px*w},{by - py*w}" fill="{stroke}"/>'
            )
        elif kind == "dash":
            px, py = -uy, ux
            N = 6
            for k in range(N):
                t = (k + 1) / (N + 1)
                cx0 = ax + (bx - ax) * t
                cy0 = ay + (by - ay) * t
                w = 1 + 4 * t
                parts.append(
                    f'<line x1="{cx0 + px*w}" y1="{cy0 + py*w}" '
                    f'x2="{cx0 - px*w}" y2="{cy0 - py*w}" '
                    f'stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round"/>'
                )
        elif order == 2:
            px, py = -uy * 3, ux * 3
            parts.append(
                f'<line x1="{ax+px}" y1="{ay+py}" x2="{bx+px}" y2="{by+py}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
                f'<line x1="{ax-px}" y1="{ay-py}" x2="{bx-px}" y2="{by-py}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
            )
        elif order == 3:
            px, py = -uy * 4, ux * 4
            parts.append(
                f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
                f'<line x1="{ax+px}" y1="{ay+py}" x2="{bx+px}" y2="{by+py}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
                f'<line x1="{ax-px}" y1="{ay-py}" x2="{bx-px}" y2="{by-py}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
            )
        else:
            parts.append(
                f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" '
                f'stroke="{stroke}" stroke-width="{sw}"/>'
            )

    # ── Atom labels ───────────────────────────────────────────
    import math as _math
    for a in atoms:
        if not _show_label(a):
            continue
        hi = a.get("id") in highlights
        c = "var(--accent)" if hi else _el_color(a.get("el"))
        x, y = _X(a), _Y(a)
        el = _esc(str(a.get("el", "")))
        parts.append(
            f'<rect x="{x-13}" y="{y-11}" width="26" height="22" '
            f'fill="var(--paper-hi)" rx="3"/>'
            f'<text x="{x}" y="{y+5}" text-anchor="middle" '
            f'class="b-mol-atom" fill="{c}">{el}</text>'
        )
        charge = a.get("charge", 0) or 0
        if charge:
            sign = "+" if charge > 0 else "−"
            mag = abs(charge)
            txt = f"{sign}{mag}" if mag > 1 else sign
            parts.append(
                f'<text x="{x+13}" y="{y-5}" class="b-mol-charge" fill="{c}">{txt}</text>'
            )
        lp = a.get("lonePairs", 0) or 0
        if lp:
            for k in range(lp):
                angle = -_math.pi / 2 + k * (2 * _math.pi / max(lp, 2))
                cx = x + _math.cos(angle) * 16
                cy = y + _math.sin(angle) * 16
                parts.append(
                    f'<circle cx="{cx-2}" cy="{cy}" r="1.4" fill="{c}"/>'
                    f'<circle cx="{cx+2}" cy="{cy}" r="1.4" fill="{c}"/>'
                )

    # ── Curly arrows ──────────────────────────────────────────
    def _resolve(ref):
        if isinstance(ref, list) and len(ref) == 2:
            A = by_id.get(ref[0]); B = by_id.get(ref[1])
            if A and B:
                return ((_X(A) + _X(B)) / 2, (_Y(A) + _Y(B)) / 2)
            return None
        if isinstance(ref, dict) and "x" in ref and "y" in ref:
            return (float(ref["x"]), float(ref["y"]))
        a = by_id.get(ref)
        return (_X(a), _Y(a)) if a else None

    for arr in arrows:
        a = _resolve(arr.get("from"))
        b = _resolve(arr.get("to"))
        if a is None or b is None:
            continue
        ax, ay = a
        bx, by_ = b
        mx = (ax + bx) / 2
        my = (ay + by_) / 2
        dx = bx - ax
        dy = by_ - ay
        c = float(arr.get("curve", 0.4))
        cx = mx - dy * c
        cy = my + dx * c
        is_half = arr.get("kind") == "half"
        marker = "url(#curly-arr-half)" if is_half else "url(#curly-arr)"
        dash = ' stroke-dasharray="4 3"' if is_half else ""
        parts.append(
            f'<path d="M {ax} {ay} Q {cx} {cy} {bx} {by_}" fill="none" '
            f'stroke="var(--accent)" stroke-width="1.6"{dash} marker-end="{marker}"/>'
        )

    parts.append("</svg>")
    body = "".join(parts)
    cap_html = fig_caption(caption) if caption else ""
    return f'<figure class="b-mol-diagram">{body}{cap_html}</figure>'


def reaction_equation(
    *,
    reactants: list[str],
    products: list[str],
    reagents: str = "",
    conditions: str = "",
    equilibrium: bool = False,
    label: str = "",
    caption: str = "",
) -> str:
    """<ReactionEquation> — reactants → products with reagents over arrow.
    `reactants` and `products` are pre-rendered HTML or plain text strings."""
    plus = '<span class="b-rxn-plus">+</span>'

    def _row(items: list[str]) -> str:
        out = []
        for i, item in enumerate(items):
            out.append(f'<div class="b-rxn-species">{_esc(item)}</div>')
            if i < len(items) - 1:
                out.append(plus)
        return "".join(out)

    if equilibrium:
        arrow_svg = (
            '<svg viewBox="0 0 120 12" class="b-rxn-arrow-svg">'
            '<path d="M 8 4 L 108 4 M 108 4 l -6 -3" stroke="currentColor" '
            'stroke-width="1.2" fill="none"/>'
            '<path d="M 112 8 L 12 8 M 12 8 l 6 3" stroke="currentColor" '
            'stroke-width="1.2" fill="none"/></svg>'
        )
    else:
        arrow_svg = (
            '<svg viewBox="0 0 120 12" class="b-rxn-arrow-svg">'
            '<path d="M 8 6 L 108 6 M 108 6 l -8 -4 M 108 6 l -8 4" '
            'stroke="currentColor" stroke-width="1.4" fill="none" stroke-linejoin="round"/></svg>'
        )

    parts = ['<figure class="b-rxn">']
    if label:
        parts.append(f'<div class="b-rxn-label">{_esc(label)}</div>')
    parts.append('<div class="b-rxn-eq">')
    parts.append(_row(reactants))
    parts.append('<div class="b-rxn-arrow">')
    if reagents:
        parts.append(f'<div class="b-rxn-reagents">{_esc(reagents)}</div>')
    parts.append(arrow_svg)
    if conditions:
        parts.append(f'<div class="b-rxn-conditions">{_esc(conditions)}</div>')
    parts.append("</div>")
    parts.append(_row(products))
    parts.append("</div>")
    if caption:
        parts.append(fig_caption(caption))
    parts.append("</figure>")
    return "".join(parts)


def arrow_pushing(*, steps: list[dict[str, Any]], title: str = "") -> str:
    """<ArrowPushing> — numbered mechanism step list.

    Each step: {label, body?, diagram?}. `diagram` is pre-rendered HTML
    (typically the output of `molecule_diagram(...)`).
    """
    parts: list[str] = ['<figure class="b-arrow-push">']
    if title:
        parts.append(f'<div class="b-arrow-push-title">{_esc(title)}</div>')
    parts.append("<ol>")
    for i, s in enumerate(steps):
        diagram = s.get("diagram", "")
        body_html = (
            f'<div class="b-arrow-push-body">{_esc(s["body"])}</div>'
            if s.get("body") else ""
        )
        parts.append(
            f'<li class="b-arrow-push-step">'
            f'<div class="b-arrow-push-num">{i+1}</div>'
            f'<div class="b-arrow-push-diagram">{diagram}</div>'
            f'<div class="b-arrow-push-text">'
            f'<div class="b-arrow-push-label">{_esc(s.get("label", ""))}</div>'
            f'{body_html}'
            f'</div></li>'
        )
    parts.append("</ol></figure>")
    return "".join(parts)


def energy_diagram(
    *,
    nodes: list[dict[str, Any]],
    title: str = "",
    delta_g: str = "",
    delta_g_dagger: str = "",
    caption: str = "",
) -> str:
    """<EnergyDiagram> — reaction-coordinate profile.

    `nodes`: [{label, energy, kind?}], kind in {"min", "ts"}.
    """
    if not nodes:
        return ""
    W, H, pad = 600, 280, 50
    energies = [float(n.get("energy", 0)) for n in nodes]
    e_min, e_max = min(energies), max(energies)
    e_span = (e_max - e_min) or 1
    n_count = max(len(nodes) - 1, 1)

    def _Y(e):
        return H - pad - ((e - e_min) / e_span) * (H - pad * 2)

    def _X(i):
        return pad + (i / n_count) * (W - pad * 2)

    pts = [(_X(i), _Y(float(n.get("energy", 0))), n) for i, n in enumerate(nodes)]
    # Smooth path
    path_segs: list[str] = [f"M {pts[0][0]} {pts[0][1]}"]
    for i in range(1, len(pts)):
        prev = pts[i - 1]
        cur = pts[i]
        cx = (prev[0] + cur[0]) / 2
        path_segs.append(f"C {cx} {prev[1]} {cx} {cur[1]} {cur[0]} {cur[1]}")
    path = " ".join(path_segs)

    parts: list[str] = ['<figure class="b-energy">']
    if title:
        parts.append(f'<div class="b-energy-title">{_esc(title)}</div>')
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-energy-svg">'
        f'<line x1="{pad}" y1="{H-pad}" x2="{W-pad}" y2="{H-pad}" '
        f'stroke="currentColor" stroke-width="1.2"/>'
        f'<line x1="{pad}" y1="{pad-10}" x2="{pad}" y2="{H-pad}" '
        f'stroke="currentColor" stroke-width="1.2"/>'
        f'<text x="{pad-8}" y="{pad-6}" text-anchor="end" class="b-energy-axis-label">E</text>'
        f'<text x="{W-pad+4}" y="{H-pad+4}" class="b-energy-axis-coord">reaction coord →</text>'
        f'<path d="{path}" stroke="var(--accent)" stroke-width="2.2" fill="none"/>'
    )
    for x, y, n in pts:
        is_ts = n.get("kind") == "ts"
        r = 5 if is_ts else 4
        fill = "var(--accent)" if is_ts else "currentColor"
        weight = "700" if is_ts else "500"
        offset = -14 if is_ts else 22
        label = _esc(str(n.get("label", "")))
        parts.append(
            f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>'
            f'<text x="{x}" y="{y + offset}" text-anchor="middle" '
            f'class="b-energy-node-label" font-weight="{weight}">{label}</text>'
        )
        if is_ts:
            parts.append(
                f'<text x="{x}" y="{y - 28}" text-anchor="middle" '
                f'class="b-energy-ts-glyph">‡</text>'
            )
    if delta_g_dagger and len(pts) >= 2:
        start = pts[0]
        ts = next((p for p in pts if p[2].get("kind") == "ts"), pts[1])
        parts.append(
            f'<line x1="{ts[0]+30}" y1="{start[1]}" x2="{ts[0]+30}" y2="{ts[1]}" '
            f'stroke="var(--accent-lo)" stroke-width="1" stroke-dasharray="3 3"/>'
            f'<text x="{ts[0]+38}" y="{(start[1]+ts[1])/2 + 4}" '
            f'class="b-energy-dg-dagger">ΔG‡ = {_esc(delta_g_dagger)}</text>'
        )
    if delta_g and len(pts) >= 2:
        start, end = pts[0], pts[-1]
        x_right = W - pad - 80
        parts.append(
            f'<line x1="{x_right}" y1="{start[1]}" x2="{x_right}" y2="{end[1]}" '
            f'stroke="var(--info)" stroke-width="1" stroke-dasharray="3 3"/>'
            f'<text x="{x_right+8}" y="{(start[1]+end[1])/2 + 4}" '
            f'class="b-energy-dg">ΔG = {_esc(delta_g)}</text>'
        )
    parts.append("</svg>")
    if caption:
        parts.append(fig_caption(caption))
    parts.append("</figure>")
    return "".join(parts)


def orbital_diagram(
    *,
    levels: list[dict[str, Any]],
    title: str = "",
    width: int = 360,
    height: int = 320,
    caption: str = "",
) -> str:
    """<OrbitalDiagram> — energy-level diagram with orbital boxes + electron arrows.

    `levels`: [{label, orbitals: [{electrons: 0|1|2}]}], bottom-to-top.
    """
    if not levels:
        return ""
    pad = 40
    inner_h = height - pad * 2
    y_step = inner_h / max(len(levels) - 1, 1)

    parts: list[str] = ['<figure class="b-orbital">']
    if title:
        parts.append(f'<div class="b-orbital-title">{_esc(title)}</div>')
    parts.append(
        f'<svg viewBox="0 0 {width} {height}" class="b-orbital-svg" '
        f'style="max-width:{width}px">'
        f'<line x1="{pad/2}" y1="{pad/2}" x2="{pad/2}" y2="{height-pad/2}" '
        f'stroke="currentColor" stroke-width="1.2"/>'
        f'<text x="{pad/2 - 4}" y="{pad/2 - 4}" text-anchor="end" '
        f'class="b-orbital-axis">E</text>'
    )
    for i, lv in enumerate(levels):
        y = height - pad - i * y_step
        orbs = lv.get("orbitals") or []
        total_w = len(orbs) * 36 + (max(len(orbs) - 1, 0)) * 4
        start_x = (width - total_w) / 2 + 10
        parts.append(
            f'<text x="{width - pad/2}" y="{y + 5}" text-anchor="end" '
            f'class="b-orbital-label">{_esc(str(lv.get("label", "")))}</text>'
        )
        for k, o in enumerate(orbs):
            x = start_x + k * 40
            electrons = int(o.get("electrons", 0) or 0)
            parts.append(
                f'<line x1="{x}" y1="{y}" x2="{x+32}" y2="{y}" '
                f'stroke="currentColor" stroke-width="2"/>'
            )
            if electrons >= 1:
                parts.append(
                    f'<path d="M {x+10} {y-2} L {x+10} {y-14} '
                    f'M {x+10} {y-14} l -3 4 M {x+10} {y-14} l 3 4" '
                    f'stroke="var(--accent)" stroke-width="1.5" '
                    f'fill="none" stroke-linecap="round"/>'
                )
            if electrons >= 2:
                parts.append(
                    f'<path d="M {x+22} {y-14} L {x+22} {y-2} '
                    f'M {x+22} {y-2} l -3 -4 M {x+22} {y-2} l 3 -4" '
                    f'stroke="var(--accent)" stroke-width="1.5" '
                    f'fill="none" stroke-linecap="round"/>'
                )
    parts.append("</svg>")
    if caption:
        parts.append(fig_caption(caption))
    parts.append("</figure>")
    return "".join(parts)


_PH_COLORS = [
    "#c93030", "#d96030", "#dc8820", "#d6a020", "#cdbb1f",
    "#a3b840", "#5e9a4a", "#4a8a78", "#3a7a90", "#3268a0",
    "#3050a4", "#3a40a0", "#5030a0", "#7030a0", "#a02080",
]


def ph_scale(*, points: list[dict[str, Any]] | None = None, title: str = "") -> str:
    """<pHScale> — labelled pH ladder 0-14. `points`: [{ph, label}]."""
    points = points or []
    W, H = 720, 140

    def _X(ph):
        return 40 + (float(ph) / 14) * (W - 80)

    parts: list[str] = ['<figure class="b-ph-scale">']
    if title:
        parts.append(f'<div class="b-ph-scale-title">{_esc(title)}</div>')
    parts.append(f'<svg viewBox="0 0 {W} {H}" class="b-ph-scale-svg">')
    parts.append('<defs><linearGradient id="b-ph-grad" x1="0" x2="1" y1="0" y2="0">')
    for i, c in enumerate(_PH_COLORS):
        parts.append(f'<stop offset="{i/(len(_PH_COLORS)-1)}" stop-color="{c}"/>')
    parts.append('</linearGradient></defs>')
    parts.append(
        f'<rect x="40" y="{H/2 - 14}" width="{W-80}" height="28" '
        f'fill="url(#b-ph-grad)" rx="4"/>'
    )
    for i in range(15):
        x = _X(i)
        parts.append(
            f'<line x1="{x}" y1="{H/2 - 18}" x2="{x}" y2="{H/2 + 18}" '
            f'stroke="currentColor" stroke-opacity="0.35" stroke-width="1"/>'
            f'<text x="{x}" y="{H/2 + 34}" text-anchor="middle" '
            f'class="b-ph-tick">{i}</text>'
        )
    parts.append(
        f'<text x="40" y="{H/2 - 22}" class="b-ph-band-label">ACIDIC</text>'
        f'<text x="{W/2}" y="{H/2 - 22}" text-anchor="middle" class="b-ph-band-label">NEUTRAL</text>'
        f'<text x="{W-40}" y="{H/2 - 22}" text-anchor="end" class="b-ph-band-label">BASIC</text>'
    )
    for i, p in enumerate(points):
        x = _X(p.get("ph", 7))
        ly = H/2 - 26 if i % 2 else H/2 + 58
        parts.append(
            f'<line x1="{x}" y1="{H/2 - 18}" x2="{x}" y2="{H/2 + 18}" '
            f'stroke="currentColor" stroke-width="2"/>'
            f'<circle cx="{x}" cy="{H/2}" r="5" fill="var(--paper)" '
            f'stroke="currentColor" stroke-width="2"/>'
            f'<text x="{x}" y="{ly}" text-anchor="middle" class="b-ph-pt-label">'
            f'{_esc(str(p.get("label", "")))}</text>'
        )
    parts.append("</svg></figure>")
    return "".join(parts)


_PERIODIC_ROWS = [
    (1,  "H",  "Hydrogen",   1,  1), (2,  "He", "Helium",     18, 1),
    (3,  "Li", "Lithium",    1,  2), (4,  "Be", "Beryllium",  2,  2),
    (5,  "B",  "Boron",      13, 2), (6,  "C",  "Carbon",     14, 2),
    (7,  "N",  "Nitrogen",   15, 2), (8,  "O",  "Oxygen",     16, 2),
    (9,  "F",  "Fluorine",   17, 2), (10, "Ne", "Neon",       18, 2),
    (11, "Na", "Sodium",     1,  3), (12, "Mg", "Magnesium",  2,  3),
    (13, "Al", "Aluminum",   13, 3), (14, "Si", "Silicon",    14, 3),
    (15, "P",  "Phosphorus", 15, 3), (16, "S",  "Sulfur",     16, 3),
    (17, "Cl", "Chlorine",   17, 3), (18, "Ar", "Argon",      18, 3),
]


def _group_color(g: int) -> str:
    if g == 1:
        return "#dc8830"
    if g == 2:
        return "#cdbb20"
    if g == 18:
        return "#7030a0"
    if 13 <= g <= 17:
        return "#3a7a90"
    return "var(--ink-mute)"


def periodic_snippet(
    *,
    highlight: list[str] | None = None,
    notes: dict[str, str] | None = None,
    title: str = "",
) -> str:
    """<PeriodicSnippet> — first-three-row periodic-table excerpt with
    optional highlight + handwritten margin notes per element."""
    highlight = highlight or []
    notes = notes or {}
    cell = 50
    W = 18 * cell + 40
    H = 3 * cell + 60

    parts: list[str] = ['<figure class="b-periodic">']
    if title:
        parts.append(f'<div class="b-periodic-title">{_esc(title)}</div>')
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" class="b-periodic-svg" style="max-width:{W}px">'
    )
    for i in range(18):
        x = 20 + i * cell + cell / 2
        parts.append(
            f'<text x="{x}" y="20" text-anchor="middle" class="b-periodic-group">'
            f'{i+1}</text>'
        )
    for Z, sym, name, g, p in _PERIODIC_ROWS:
        x = 20 + (g - 1) * cell
        y = 30 + (p - 1) * cell
        hi = sym in highlight
        bg = "var(--accent)" if hi else "var(--paper-hi)"
        stroke = "var(--accent-lo)" if hi else "var(--rule)"
        sw = 2 if hi else 1
        fg = "var(--paper)" if hi else "var(--ink)"
        sub_fg = "var(--paper)" if hi else _group_color(g)
        parts.append(
            f'<rect x="{x+2}" y="{y+2}" width="{cell-4}" height="{cell-4}" '
            f'fill="{bg}" stroke="{stroke}" stroke-width="{sw}" rx="3"/>'
            f'<text x="{x+6}" y="{y+12}" class="b-periodic-z" fill="{sub_fg}">{Z}</text>'
            f'<text x="{x+cell/2}" y="{y+30}" text-anchor="middle" '
            f'class="b-periodic-sym" fill="{fg}">{sym}</text>'
            f'<text x="{x+cell/2}" y="{y+42}" text-anchor="middle" '
            f'class="b-periodic-name" fill="{fg}">{name}</text>'
        )
        if sym in notes:
            parts.append(
                f'<text x="{x+cell-4}" y="{y+12}" text-anchor="end" '
                f'class="b-periodic-note">{_esc(notes[sym])}</text>'
            )
    parts.append("</svg></figure>")
    return "".join(parts)


def balance_equation(
    *,
    reactants: list[str],
    products: list[str],
    correct: list[int] | None = None,
) -> str:
    """<BalanceEquation> — coefficient steppers + live mass balance.

    `reactants` / `products`: chemical formula strings ("CH4", "O2", ...).
    `correct`: canonical coefficient sequence (R1, R2, ..., P1, P2, ...).
    All grading happens client-side in lib_blocks.js.
    """
    import json as _json
    correct_json = _json.dumps(list(correct or []))
    reactants_json = _json.dumps(reactants)
    products_json = _json.dumps(products)

    def _species_html(formulas: list[str], offset: int) -> str:
        parts = []
        for i, f in enumerate(formulas):
            idx = offset + i
            pretty = _pretty_formula(f)
            parts.append(
                f'<div class="b-balance-species" data-idx="{idx}">'
                f'<div class="b-balance-stepper">'
                f'<button class="b-balance-step" data-action="up" data-idx="{idx}">▲</button>'
                f'<button class="b-balance-step" data-action="down" data-idx="{idx}">▼</button>'
                f'</div>'
                f'<div class="b-balance-formula">'
                f'<span class="b-balance-coef" data-coef-idx="{idx}">1</span>'
                f'<span class="b-balance-pretty">{pretty}</span>'
                f'</div></div>'
            )
            if i < len(formulas) - 1:
                parts.append('<span class="b-balance-plus">+</span>')
        return "".join(parts)

    return (
        f'<div class="b-balance" data-balance '
        f'data-reactants=\'{_esc(reactants_json, quote=True)}\' '
        f'data-products=\'{_esc(products_json, quote=True)}\' '
        f'data-correct=\'{_esc(correct_json, quote=True)}\'>'
        f'<div class="b-balance-prompt">'
        f'<strong>Balance</strong>Adjust the coefficients so each element has the same count on both sides.'
        f'</div>'
        f'<div class="b-balance-eq">'
        f'{_species_html(reactants, 0)}'
        f'<span class="b-balance-arrow">→</span>'
        f'{_species_html(products, len(reactants))}'
        f'</div>'
        f'<table class="b-balance-tally" data-tally>'
        f'<thead><tr><th>Element</th><th>Left</th><th>Right</th><th>Δ</th></tr></thead>'
        f'<tbody></tbody></table>'
        f'<div class="b-balance-status" data-status>not yet balanced</div>'
        f'</div>'
    )


def _pretty_formula(f: str) -> str:
    """Pretty-print a chemical formula: digit runs become subscripts."""
    import re as _re
    out = []
    for m in _re.finditer(r"(\d+|[^\d]+)", f):
        chunk = m.group(0)
        if chunk.isdigit():
            out.append(f'<sub>{chunk}</sub>')
        else:
            out.append(_esc(chunk))
    return "".join(out)


def isomer_spotter(
    *,
    prompt: str,
    target: str = "",
    candidates: list[dict[str, Any]],
) -> str:
    """<IsomerSpotter> — click-to-select which structures match a target.

    `target`: pre-rendered HTML for the reference structure (typically
    a `molecule_diagram(...)` call).
    `candidates`: [{id, label, diagram, isMatch, reason?}].
    """
    target_html = (
        f'<div class="b-isomer-target">'
        f'<div class="b-isomer-target-label">Target</div>'
        f'{target}</div>' if target else ""
    )
    cards: list[str] = []
    for c in candidates:
        cid = _esc(str(c.get("id", "")), quote=True)
        is_match = "true" if c.get("isMatch") else "false"
        reason = (
            f'<div class="b-isomer-reason" hidden>{_esc(c.get("reason", ""))}</div>'
            if c.get("reason") else ""
        )
        cards.append(
            f'<button class="b-isomer-card" data-id="{cid}" data-match="{is_match}">'
            f'<div class="b-isomer-card-label">{_esc(c.get("label", ""))}</div>'
            f'<div class="b-isomer-card-diagram">{c.get("diagram", "")}</div>'
            f'{reason}'
            f'</button>'
        )
    return (
        '<div class="b-isomer" data-isomer>'
        f'<div class="b-isomer-prompt"><strong>Spot the isomers</strong>{_esc(prompt)}</div>'
        f'{target_html}'
        f'<div class="b-isomer-grid">{"".join(cards)}</div>'
        '<div class="b-isomer-actions">'
        '<button class="b-btn-primary" data-action="check">Check</button>'
        '<button class="b-btn-ghost" data-action="reset">Reset</button>'
        '<span class="b-isomer-status" data-status></span>'
        '</div></div>'
    )


def titration_curve(
    *,
    label: str = "Strong-acid / strong-base titration",
    acid_vol: float = 25,
    acid_conc: float = 0.1,
    base_conc: float = 0.1,
    pka: float | None = None,
    max_base: float = 50,
) -> str:
    """<TitrationCurve> — interactive titration with live pH calculation.

    Strong-acid/strong-base by default; pass `pka` for weak-acid + strong-base.
    Math runs client-side in lib_blocks.js.
    """
    pka_attr = f' data-pka="{pka}"' if pka is not None else ""
    return (
        '<div class="b-titration" data-titration '
        f'data-acid-vol="{acid_vol}" data-acid-conc="{acid_conc}" '
        f'data-base-conc="{base_conc}" data-max-base="{max_base}"'
        f'{pka_attr}>'
        f'<div class="b-titration-label">{_esc(label)}</div>'
        '<svg viewBox="0 0 600 280" class="b-titration-svg" data-svg></svg>'
        '<div class="b-titration-controls">'
        f'<input class="b-titration-input" type="range" min="0" max="{max_base}" '
        f'step="0.25" value="0" data-input>'
        '<div class="b-titration-readout">'
        '<span class="b-titration-vol">V<sub>base</sub> = <strong data-vol>0.0 mL</strong></span>'
        '<span class="b-titration-ph" data-ph>pH = —</span>'
        '</div></div></div>'
    )


def electron_config(
    *,
    element: str,
    atomic_number: int,
    correct_config: list[dict[str, Any]],
) -> str:
    """<ElectronConfig> — click-to-fill orbital boxes.

    `correct_config`: [{label, slots}] in fill order. `slots` is the
    orbital count (s=1, p=3, d=5, f=7).
    """
    import json as _json
    cfg_json = _json.dumps(correct_config)
    boxes: list[str] = []
    for oi, o in enumerate(correct_config):
        slots = int(o.get("slots", 1))
        slot_btns = "".join(
            f'<button class="b-config-slot" data-orbital="{oi}" data-slot="{si}" '
            f'data-electrons="0">'
            f'<span class="b-config-up" hidden>↑</span>'
            f'<span class="b-config-down" hidden>↓</span></button>'
            for si in range(slots)
        )
        boxes.append(
            f'<div class="b-config-orbital">'
            f'<div class="b-config-orbital-label">{_esc(str(o.get("label", "")))}</div>'
            f'<div class="b-config-orbital-row">{slot_btns}</div>'
            f'</div>'
        )
    return (
        '<div class="b-electron-config" data-electron-config '
        f'data-atomic-number="{atomic_number}" '
        f'data-config=\'{_esc(cfg_json, quote=True)}\'>'
        f'<div class="b-config-prompt">'
        f'<strong>Configure</strong>Place all '
        f'<span class="b-config-Z">{atomic_number}</span> electrons of <strong>{_esc(element)}</strong>.'
        f'<span class="b-config-counter" data-counter>placed: 0/{atomic_number}</span>'
        f'</div>'
        f'<div class="b-config-boxes">{"".join(boxes)}</div>'
        '<div class="b-config-actions">'
        '<button class="b-btn-primary" data-action="aufbau">Auto-fill (Aufbau)</button>'
        '<button class="b-btn-ghost" data-action="reset">Reset</button>'
        '</div>'
        '<div class="b-config-summary" data-summary></div>'
        '</div>'
    )

