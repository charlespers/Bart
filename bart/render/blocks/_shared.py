r"""Shared helpers for the bart-block renderers.

The 66 ``bart-<name>`` renderers split into one module per family
(``primitives`` / ``visuals`` / ``interactives`` / ``chem`` / ``packs``);
this module holds the helpers they all lean on — one copy, no duplication:

  * ``_esc`` — ``html.escape`` (re-exported for the family modules);
  * ``_attr(name, value)`` — render an HTML attribute (or "" for empty/None);
  * ``_esc_math_inner`` / ``_inline_math`` / ``_block_math`` — HTML-escape
    ``<`` / ``>`` / ``&`` *inside* a TeX string and wrap it for KaTeX (a bare
    ``<T_2`` in prose would be parsed as a bogus tag and eat the next ``</…>``);
  * ``_inline_md`` / ``_block_md`` — a tiny markdown subset (bold / italic /
    inline code / paragraphs) for the short text fields blocks carry, with
    math spans masked so ``*`` inside ``\(...\)`` isn't mistaken for emphasis.

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

import re as _re
import re as _re_local
from html import escape as _esc
from typing import Any


# ─── Helpers ──────────────────────────────────────────────────────


def _attr(name: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f' {name}="{_esc(str(value), quote=True)}"'


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


_INLINE_BOLD_RE = _re.compile(r"\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*")


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

