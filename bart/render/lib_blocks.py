"""Design-library blocks — server-rendered HTML for the components from
the design package's library.html (project/library/*.jsx).

The design tool used React; the packet is static, so we re-implement each
component as a Python function returning HTML strings. Every block
is **skeletal** (data-driven, no hardcoded subject content) and matches
the visual contract of its JSX counterpart.

Components are grouped:
  - Primitives:    highlight, stamp, tag, key_term, fig_caption, paper_rule, marginalia
  - Visuals:       formula_card, worked_example, mnemonic_card, trap_callout,
                   why_it_matters, flowchart
  - Interactives:  quick_check, multi_step_problem, multiple_choice, hotspots
                   (interactivity comes from native HTML <details> + small JS in lib_blocks.js)
  - Checkpoint:    checkpoint  (spaced-repetition deck)

Math: all functions accept LaTeX strings and wrap them in `\\(…\\)` /
`\\[…\\]` so KaTeX (loaded by the page shell) renders them at view time.
"""
from __future__ import annotations

from html import escape as _esc
from typing import Any, Iterable


# ─── Helpers ──────────────────────────────────────────────────────


def _attr(name: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f' {name}="{_esc(str(value), quote=True)}"'


_ENT_RE = _re.compile(
    r"&(?:#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos|nbsp);"
) if False else None
import re as _re_local
_ENT_RE = _re_local.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos|nbsp);")


def _esc_math_inner(tex: str) -> str:
    """HTML-escape `<`, `>`, `&` inside a math TeX string.

    KaTeX decodes `&lt;` / `&gt;` / `&amp;` at render time, so escaping
    here is safe and `\\(T_1<T_2\\)` still displays as the inequality.
    The HTML parser, however, treats a bare `<T_2` as the start of a tag
    and consumes characters until the next `>` (often inside a
    `</div>` somewhere downstream), eating closing tags and pushing the
    rest of the page into bogus nested layout. Escaping at the source —
    every place `_inline_math` / `_block_math` is used — closes that
    class of bug. Already-encoded entities (`&lt;`, numeric refs) pass
    through untouched.
    """
    sentinels: list[str] = []

    def _stash(m):
        sentinels.append(m.group(0))
        return f"\x00E{len(sentinels) - 1}\x00"

    masked = _ENT_RE.sub(_stash, tex)
    masked = masked.replace("&", "&amp;")
    masked = masked.replace("<", "&lt;").replace(">", "&gt;")
    return _re_local.sub(
        r"\x00E(\d+)\x00",
        lambda m: sentinels[int(m.group(1))],
        masked,
    )


def _inline_math(tex: str) -> str:
    """Wrap a LaTeX string for KaTeX inline rendering. HTML-escapes
    `<`/`>`/`&` inside the TeX so a bare `<T_2` can't be parsed as a
    bogus tag."""
    return f"\\({_esc_math_inner(tex)}\\)"


def _block_math(tex: str) -> str:
    return f"\\[{_esc_math_inner(tex)}\\]"


import re as _re

_INLINE_BOLD_RE = _re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")
# Italic must hug its content (no whitespace immediately inside the *…* pair)
# AND must not abut a word character on either flank — otherwise stray
# asterisks in prose ("the * is a label.") swallow the surrounding text up to
# the next asterisk.
_INLINE_ITAL_RE = _re.compile(
    r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\*\w])"
)
_INLINE_CODE_RE = _re.compile(r"`([^`\n]+?)`")


def _inline_md(text: str) -> str:
    """Render the most common inline markdown patterns (`**bold**`, `*italic*`,
    `` `code` ``) inside library-block body strings.

    Library blocks are emitted as raw HTML *before* the markdown pass, so any
    `**bold**` syntax in their body fields would otherwise reach the page
    literally (the symptom: "**Wrong intuition:**" leaking into trap-callouts
    in the Misconceptions section). We apply HTML-escape first, then run the
    inline regexes so the output is safe to interpolate. Math spans (`\\(…\\)`,
    `\\[…\\]`) are preserved verbatim — KaTeX needs them untouched.
    """
    # Stash math spans first so we don't HTML-escape backslashes inside them.
    holds: list[str] = []
    def _stash(m: _re.Match) -> str:
        holds.append(m.group(0))
        return f"\x00M{len(holds)-1}\x00"
    masked = _re.sub(r"\\\([^\)]*?\\\)|\\\[[\s\S]*?\\\]", _stash, text)
    masked = _esc(masked)
    masked = _INLINE_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", masked)
    masked = _INLINE_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", masked)
    masked = _INLINE_ITAL_RE.sub(lambda m: f"<em>{m.group(1)}</em>", masked)
    return _re.sub(r"\x00M(\d+)\x00", lambda m: holds[int(m.group(1))], masked)


def _block_md(text: str) -> str:
    """Render multi-line markdown including fenced code blocks, lists,
    sub-headings, and the inline patterns `_inline_md` handles.

    Used for library-block content fields that may carry full markdown — a
    ``` ```java … ``` ``` code block in a multiple-choice question, a
    `## sub-heading` inside a multi-step solution, an indented list in a
    concept-build rung body. Without full block-level rendering the field
    text reaches the page literally and the wrapping `</div>` ends up
    on the same line as the closing ` ``` `, breaking python-markdown's
    fence matcher (the fence stays "open" and silently swallows every
    subsequent library-block expansion until the next ` ``` ` line).

    Math spans (`\\(…\\)`, `\\[…\\]`) are stashed before the markdown
    pass and restored after so KaTeX sees the raw TeX.
    """
    if not text:
        return ""
    # Short-circuit: single-line, no fence → cheap inline path. This keeps
    # one-line labels rendering inline (no wrapping `<p>` that would add
    # vertical padding inside buttons / pills / chips).
    if "\n" not in text and "```" not in text:
        return _inline_md(text)
    holds: list[str] = []
    def _stash(m: _re.Match) -> str:
        holds.append(m.group(0))
        return f"\x00M{len(holds)-1}\x00"
    masked = _re.sub(r"\\\([^\)]*?\\\)|\\\[[\s\S]*?\\\]", _stash, text)
    import markdown as _md_mod
    md = _md_mod.Markdown(
        extensions=["fenced_code", "tables", "sane_lists"],
        output_format="html5",
    )
    html = md.convert(masked)
    return _re.sub(r"\x00M(\d+)\x00", lambda m: holds[int(m.group(1))], html)


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


# ─── Checkpoint (spaced repetition) ───────────────────────────────


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


# ═════════════════════════════════════════════════════════════════
#  EXTRA VISUALS  (timeline, concept_map, comparison_matrix,
#                  process_ribbon, anatomy_diagram, number_line,
#                  proof_ladder, annotated_quote)
# ═════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════
#  EXTRA INTERACTIVES (drag_order, fill_in_blank, match_pairs,
#                      parameter_slider, build_equation, estimate_range,
#                      confidence_poll)
# ═════════════════════════════════════════════════════════════════


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


# ═════════════════════════════════════════════════════════════════
#  CHEMISTRY blocks (chem-visuals + chem-interactives)
#  Ported from project/library/chem-{visuals,interactives}.jsx.
# ═════════════════════════════════════════════════════════════════


# CPK-ish element colors muted for the paper palette.
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


# ─── Public registry — for tests + introspection ──────────────────


__all__ = [
    # Primitives
    "highlight", "stamp", "tag", "key_term", "fig_caption", "paper_rule",
    "marginalia",
    # Visuals (original)
    "formula_card", "worked_example", "mnemonic_card", "trap_callout",
    "why_it_matters", "flowchart", "concept_build",
    # Visuals (extra)
    "timeline", "concept_map", "comparison_matrix", "process_ribbon",
    "anatomy_diagram", "number_line", "proof_ladder", "annotated_quote",
    # Interactives (original)
    "quick_check", "multi_step_problem", "multiple_choice", "hotspots",
    "checkpoint",
    # Interactives (extra)
    "drag_order", "fill_in_blank", "match_pairs", "parameter_slider",
    "build_equation", "estimate_range", "confidence_poll",
    # Chem visuals + interactives
    "molecule_diagram", "reaction_equation", "arrow_pushing",
    "energy_diagram", "orbital_diagram", "ph_scale", "periodic_snippet",
    "balance_equation", "isomer_spotter", "titration_curve", "electron_config",
]
