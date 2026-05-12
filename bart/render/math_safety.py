r"""Make math HTML-safe *before* it reaches the markdown→HTML renderer.

The bug class this closes: bart asks the model to emit LaTeX inside JSON
inside a markdown code-fence, then HTML-escapes on top — a four-layer
escaping minefield. The two recurrent failures:

  * ``\(q>0\)`` / ``\(T_1<T_2\)`` — a bare ``<`` next to a letter makes the
    HTML parser treat ``<T_2\)`` as the start of a tag, which then eats
    characters (sometimes a whole ``</div>``) and the rest of the page
    renders nested inside whatever container was open.
  * ``$x$`` — the model emits dollar math even though we render with
    backslash delimiters; depending on config it either renders as literal
    text or collides with literal ``$`` in code/prices.
  * ``τ`` — a JSON-escaped unicode literal that, having been decoded
    once by ``json.loads`` and re-introduced by a sloppy re-emit, reaches
    the page as the seven characters ``τ`` instead of ``τ``.

``make_math_html_safe(markdown) -> markdown`` fixes all three, idempotently,
without touching fenced/inline code. ``check_math_html_safe(markdown) ->
list[Warning]`` is the defense-in-depth detector: if a ``$...$`` span or an
HTML-unsafe ``<``/``>`` inside math *survives* the sanitizer, it returns a
loud warning naming the span (it should normally return ``[]``).

The ``$...$`` → ``\(...\)`` conversion lives here so there's one
implementation; ``sanitize.py`` delegates to it.
"""
from __future__ import annotations

import re
from typing import List

from ..runrecord import Warning


# ─────────────────────────────────────────────────────────────────
# Code-vs-prose segmentation — we only transform math inside *prose*;
# fenced ``` blocks and inline `…` spans are passed through verbatim.
# (Copied from the same logic in sanitize.py — kept here so math_safety
# has no import cycle with sanitize; sanitize re-imports `_split_segments`
# from here so there's a single copy.)
# ─────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"(?ms)(^```.*?(?:\n```\s*$|\Z))")
_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")


def _split_segments(text: str) -> List[tuple[str, str]]:
    """Split markdown into [(kind, content)] where kind is 'code' or 'prose'.

    Conservative: ambiguous text is treated as prose so transforms still
    apply if the model forgot to close a fence.
    """
    segments: List[tuple[str, str]] = []
    cursor = 0
    for m in _FENCE_RE.finditer(text):
        if m.start() > cursor:
            segments.extend(_split_inline(text[cursor : m.start()]))
        segments.append(("code", m.group(0)))
        cursor = m.end()
    if cursor < len(text):
        segments.extend(_split_inline(text[cursor:]))
    return segments


def _split_inline(text: str) -> List[tuple[str, str]]:
    out: List[tuple[str, str]] = []
    cursor = 0
    for m in _INLINE_CODE_RE.finditer(text):
        if m.start() > cursor:
            out.append(("prose", text[cursor : m.start()]))
        out.append(("code", m.group(0)))
        cursor = m.end()
    if cursor < len(text):
        out.append(("prose", text[cursor:]))
    return out


# ─────────────────────────────────────────────────────────────────
# 1. $...$ / $$...$$  →  \(...\) / \[...\]
# ─────────────────────────────────────────────────────────────────

# Display math first ($$...$$). Allow newlines inside.
_DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# Inline math ($...$). Disallow newlines; don't catch the "$" if it's
# preceded by a backslash or word char (avoid `\$`, and prices like
# "$5 plus $7" — the second `$` follows a digit-then-space-then-`$`...
# the negative lookbehind for word/backslash + the "no space after open,
# no space before close, not followed by alnum" guards do most of it).
_INLINE_DOLLAR_RE = re.compile(
    r"(?<![\\\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w\d])"
)


def convert_dollar_math(text: str) -> tuple[str, int, int]:
    """Convert ``$...$``→``\\(...\\)`` and ``$$...$$``→``\\[...\\]`` in prose
    segments only. Returns (text, n_display_converted, n_inline_converted).

    Display math is emitted on its own line with blank lines around it so
    the markdown parser treats it as a standalone block.
    """
    n_display = 0
    n_inline = 0
    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue

        def _disp_sub(m: re.Match) -> str:
            nonlocal n_display
            n_display += 1
            return f"\n\n\\[{m.group(1).strip()}\\]\n\n"

        def _inl_sub(m: re.Match) -> str:
            nonlocal n_inline
            n_inline += 1
            return f"\\({m.group(1).strip()}\\)"

        content = _DISPLAY_DOLLAR_RE.sub(_disp_sub, content)
        content = _INLINE_DOLLAR_RE.sub(_inl_sub, content)
        out_parts.append(content)
    return "".join(out_parts), n_display, n_inline


def _ensure_block_math_isolation(text: str) -> str:
    """Surround ``\\[...\\]`` block math with blank lines if missing, then
    collapse runs of >2 blank lines back to 2. Prose segments only."""
    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue
        content = re.sub(
            r"\\\[(.+?)\\\]",
            lambda m: f"\n\n{m.group(0)}\n\n",
            content,
            flags=re.DOTALL,
        )
        content = re.sub(r"\n{3,}", "\n\n", content)
        out_parts.append(content)
    return "".join(out_parts)


# ─────────────────────────────────────────────────────────────────
# 2. Bare <,> inside \(...\) / \[...\]  →  \lt , \gt
#
# We rewrite to the LaTeX macros (not HTML entities) so the result is
# HTML-safe regardless of how python-markdown / KaTeX treats entities,
# and stays valid math. We only touch a `<`/`>` that's *adjacent to an
# alphanumeric* — i.e. an inequality like `q>0` or `T_1<T_2`, not a
# stray ` < ` (which is HTML-harmless anyway) and not part of a LaTeX
# command (those contain no literal `<`/`>`).
# ─────────────────────────────────────────────────────────────────

_MATH_INLINE_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_MATH_BLOCK_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

# A bare `<` / `>` *adjacent to an alphanumeric* (allowing intervening
# spaces and a leading sign/brace/paren on the far side) — i.e. an
# inequality like `q>0`, `T_1<T_2`, `x>=1`, `x < y`. Not matched: a `<`
# right after a backslash (a `\<`-style macro) or as part of `<<`/`>>`.
# `\lt`/`\gt` themselves contain no `<`/`>` char, so this is idempotent.
_LT_IN_MATH = re.compile(
    r"(?<!\\)(?<![<>])\s*<\s*(?=[-+]?[\\{(]?\s*[A-Za-z0-9])"
    r"|(?<=[A-Za-z0-9}\)])\s*<(?![<>])"
)
_GT_IN_MATH = re.compile(
    r"(?<!\\)(?<![<>])\s*>\s*(?=[-+]?[\\{(]?\s*[A-Za-z0-9])"
    r"|(?<=[A-Za-z0-9}\)])\s*>(?![<>])"
)


def _rewrite_inequalities(inner: str) -> str:
    # `<` and `>` are handled independently; the macros they expand to
    # contain neither character, so re-running is a no-op.
    inner = _LT_IN_MATH.sub(r" \\lt ", inner)
    inner = _GT_IN_MATH.sub(r" \\gt ", inner)
    # Collapse the double-spaces our ` \lt ` / ` \gt ` insertion creates.
    inner = re.sub(r" {2,}", " ", inner)
    return inner.strip()


def _safe_inequalities_in_math(text: str) -> str:
    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue
        content = _MATH_INLINE_RE.sub(
            lambda m: f"\\({_rewrite_inequalities(m.group(1))}\\)", content
        )
        content = _MATH_BLOCK_RE.sub(
            lambda m: f"\\[{_rewrite_inequalities(m.group(1))}\\]", content
        )
        out_parts.append(content)
    return "".join(out_parts)


# ─────────────────────────────────────────────────────────────────
# 3. Decode `\uXXXX` literals to the actual character (inside math spans).
# A re-emitted JSON string sometimes carries a literal backslash-u-hex
# that never went through a JSON decoder — it reaches the page as text.
# ─────────────────────────────────────────────────────────────────

_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _decode_unicode_escapes_in_math(text: str) -> str:
    def _decode_inner(inner: str) -> str:
        return _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), inner)

    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue
        content = _MATH_INLINE_RE.sub(
            lambda m: f"\\({_decode_inner(m.group(1))}\\)", content
        )
        content = _MATH_BLOCK_RE.sub(
            lambda m: f"\\[{_decode_inner(m.group(1))}\\]", content
        )
        out_parts.append(content)
    return "".join(out_parts)


# ─────────────────────────────────────────────────────────────────
# Public — aggressive sanitizer + loud checker
# ─────────────────────────────────────────────────────────────────

def make_math_html_safe(md: str, *, convert_dollars: bool = True) -> str:
    """Return ``md`` with math made HTML-safe. Idempotent.

    1. (when ``convert_dollars``) ``$...$``→``\\(...\\)``, ``$$...$$``→
       ``\\[...\\]`` (display gets blank lines around it).
    2. Bare ``<``/``>`` adjacent to an alphanumeric inside ``\\(...\\)`` /
       ``\\[...\\]`` → ``\\lt``/``\\gt``.
    3. ``\\uXXXX`` literals inside math spans → the actual character.

    Fenced/inline code is never touched. Pass ``convert_dollars=False`` when
    running over already-block-expanded content (raw HTML blobs may contain a
    literal ``$`` — e.g. shell code in a ``bart-code-block`` — that must not
    be misread as inline math); blocks emit their math as ``\\(...\\)`` so
    only steps 2 and 3 are relevant there.
    """
    if not md:
        return md
    if convert_dollars:
        md, _, _ = convert_dollar_math(md)
    md = _safe_inequalities_in_math(md)
    md = _decode_unicode_escapes_in_math(md)
    return md


# What HTML treats as the start of a tag: `<` *immediately* followed by a
# letter, `!`, `/`, or `?`. That — and only that — is the genuinely
# dangerous shape inside a math span (`<T_2` eats the next close-tag);
# `q>0`, `\Delta P < 0` (space-separated `<`), etc. are HTML-harmless even
# though `make_math_html_safe` rewrites them for tidiness/robustness.
_HTML_TAGISH = re.compile(r"<[A-Za-z!/?]")


def check_math_html_safe(md: str) -> List[Warning]:
    """Defense-in-depth: return one ``error``-severity Warning per residual
    *genuinely* HTML-unsafe math construct that ``make_math_html_safe``
    somehow didn't fix.

    Flags: a surviving ``$...$``/``$$...$$`` span (in prose, not code); a
    ``<tag``-shaped sequence (``<`` immediately followed by a letter / ``!``
    / ``/`` / ``?``) inside a ``\\(...\\)``/``\\[...\\]`` span; a leftover
    ``\\uXXXX`` literal inside a math span. Does **not** flag a space-padded
    ``<`` or any ``>`` — those are HTML-harmless. Source is ``"math"`` so
    callers can route it.
    """
    warns: List[Warning] = []
    prose = "".join(c for k, c in _split_segments(md) if k == "prose")

    # Residual $-math.
    if _DISPLAY_DOLLAR_RE.search(prose):
        m = _DISPLAY_DOLLAR_RE.search(prose)
        warns.append(Warning(
            "error", "math",
            f"residual $$...$$ display math survived the sanitizer: "
            f"{_snippet(m.group(0))}",
        ))
    if _INLINE_DOLLAR_RE.search(prose):
        m = _INLINE_DOLLAR_RE.search(prose)
        warns.append(Warning(
            "error", "math",
            f"residual $...$ inline math survived the sanitizer: "
            f"{_snippet(m.group(0))}",
        ))

    # Residual HTML-tag-shaped `<` inside math spans.
    for span_re, kind_label in ((_MATH_INLINE_RE, "inline"), (_MATH_BLOCK_RE, "block")):
        for m in span_re.finditer(prose):
            inner = m.group(1)
            if _HTML_TAGISH.search(inner):
                warns.append(Warning(
                    "error", "math",
                    f"HTML-tag-shaped '<' inside {kind_label} math span "
                    f"survived the sanitizer: {_snippet(m.group(0))}",
                ))
            if _UNICODE_ESCAPE_RE.search(inner):
                warns.append(Warning(
                    "error", "math",
                    rf"literal \uXXXX escape inside {kind_label} math span "
                    rf"survived the sanitizer: {_snippet(m.group(0))}",
                ))
    return warns


def _snippet(s: str, n: int = 60) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
