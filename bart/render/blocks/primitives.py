"""Primitive bart-block renderers — the smallest design-system atoms.

``stamp`` / ``tag`` (little labels), ``key_term`` (inline term + tooltip),
``fig-caption``, ``paper-rule`` (a decorative divider), ``marginalia`` (a
side note), and ``highlight`` (a prose mark). Other families import a couple
of these (``stamp`` / ``fig_caption`` / ``highlight``).

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

from html import escape as _esc



# ─── Primitives ───────────────────────────────────────────────────


def highlight(text: str) -> str:
    """<Highlight> — yellow-marker treatment for inline text."""
    return f'<mark class="b-highlight">{_esc(text)}</mark>'


def stamp(label: str, tone: str = "accent") -> str:
    """<Stamp> — angled rubber-stamp badge. Tone: accent | warn | ok | info."""
    cls = "b-stamp"
    if tone in ("warn", "ok", "info"):
        cls += f" b-stamp-{tone}"
    return f'<span class="{cls}">{_esc(label)}</span>'


def tag(label: str, tone: str = "mute") -> str:
    """<Tag> — chip / metadata pill. Tone: mute | accent | warn | ok | info."""
    cls = "b-tag"
    if tone in ("accent", "warn", "ok", "info"):
        cls += f" b-tag-{tone}"
    return f'<span class="{cls}">{_esc(label)}</span>'


def key_term(text: str, definition: str = "") -> str:
    """<KeyTerm> — inline term with hover-tooltip definition."""
    if definition:
        return (
            f'<span class="b-keyterm" data-def="{_esc(definition, quote=True)}">'
            f"{_esc(text)}</span>"
        )
    return f'<span class="b-keyterm">{_esc(text)}</span>'


def fig_caption(text: str, number: int | str | None = None) -> str:
    """<FigureCaption> — italic caption under a visual."""
    num = (
        f'<span class="b-figcaption-num">FIG. {_esc(str(number))}.</span>'
        if number is not None else ""
    )
    return f'<figcaption class="b-figcaption">{num}{_esc(text)}</figcaption>'


def paper_rule(glyph: str = "") -> str:
    """<PaperRule> — wobbly hand-drawn divider, optional centered glyph."""
    wave_l = (
        '<svg viewBox="0 0 200 8" preserveAspectRatio="none">'
        '<path d="M 0 4 Q 50 2 100 4 T 200 4" stroke="currentColor" '
        'stroke-width="1" fill="none" opacity="0.4"/></svg>'
    )
    wave_r = (
        '<svg viewBox="0 0 200 8" preserveAspectRatio="none">'
        '<path d="M 0 4 Q 50 6 100 4 T 200 4" stroke="currentColor" '
        'stroke-width="1" fill="none" opacity="0.4"/></svg>'
    )
    if glyph:
        return (
            f'<div class="b-paper-rule">{wave_l}'
            f'<span class="b-paper-rule-glyph">{_esc(glyph)}</span>{wave_r}</div>'
        )
    return f'<div class="b-paper-rule">{wave_l}</div>'


def marginalia(text: str) -> str:
    """<Marginalia> — handwritten side-note styling. Inline-block by default."""
    return f'<aside class="b-marginalia">{_esc(text)}</aside>'

