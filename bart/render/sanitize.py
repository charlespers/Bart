"""Markdown sanitization — normalize agent output before rendering.

Why this exists: agents can emit markdown that's *almost* right but contains
quirks that break HTML rendering. We catch them deterministically here so the
HTML packet is always clean. Each transform is idempotent and well-bounded.

Contract: `normalize_md(text) -> (clean_text, warnings)`. Never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class SanitizeWarning:
    kind: str           # e.g. "unbalanced_dollar", "stray_preamble"
    detail: str         # human-readable
    file: str = ""      # set by caller


# ─────────────────────────────────────────────────────────────────
# Helpers — segment markdown into "code" and "non-code" regions so we
# only touch math/prose, never code-fence content.
# ─────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"(?ms)(^```.*?(?:\n```\s*$|\Z))")
_INLINE_CODE_RE = re.compile(r"(`[^`\n]+`)")


def _split_segments(text: str) -> List[tuple[str, str]]:
    """Split markdown into [(kind, content)] where kind is 'code' or 'prose'.

    Code includes both fenced ``` blocks and inline `…` spans. Prose includes
    everything else, including math delimiters.

    Splitting is conservative: we err on the side of treating ambiguous text
    as prose so transformations still apply if the agent forgot to close a
    fence.
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
# Transform 1 — Strip stray AI preamble / coda lines
# ─────────────────────────────────────────────────────────────────

_PREAMBLE_PATTERNS = [
    re.compile(r"^(?:Sure[!,]?|Of course[!,]?|Certainly[!,]?|Here(?:'s| is) (?:the|a|your)\b)[^\n]*\n+", re.IGNORECASE),
    re.compile(r"^(?:I(?:'ll| will| have| ve)|Let me)\b[^\n]*\n+", re.IGNORECASE),
]
_CODA_PATTERNS = [
    re.compile(r"\n+(?:Let me know if [^\n]*|I hope this helps[^\n]*|Hope this (?:helps|is useful)[^\n]*|Feel free to [^\n]*)\s*$", re.IGNORECASE),
]


def _strip_ai_preamble(text: str) -> tuple[str, List[SanitizeWarning]]:
    warns: List[SanitizeWarning] = []
    for pat in _PREAMBLE_PATTERNS:
        new = pat.sub("", text, count=1)
        if new != text:
            warns.append(SanitizeWarning("stray_preamble", f"removed leading line matching {pat.pattern!r}"))
            text = new
    for pat in _CODA_PATTERNS:
        new = pat.sub("", text)
        if new != text:
            warns.append(SanitizeWarning("stray_coda", f"removed trailing line matching {pat.pattern!r}"))
            text = new
    return text.strip() + "\n", warns


# ─────────────────────────────────────────────────────────────────
# Transform 2 — Convert $...$ and $$...$$ to \(...\) and \[...\]
#
# We pick backslash delimiters because:
#   - MathJax can be configured to recognize ONLY them, eliminating any
#     conflict with literal $ in code blocks or prices.
#   - The conversion is unambiguous when done in non-code segments.
# ─────────────────────────────────────────────────────────────────

# Display math first ($$...$$). Allow newlines inside.
_DISPLAY_DOLLAR_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# Inline math ($...$). Disallow newlines, disallow the "$" being preceded by a
# digit + letter (avoid catching prices like "$5 plus $7"). Require non-space
# after opening and before closing.
_INLINE_DOLLAR_RE = re.compile(
    r"(?<![\\\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w\d])"
)


def _convert_math_delimiters(text: str) -> tuple[str, List[SanitizeWarning]]:
    warns: List[SanitizeWarning] = []
    n_display = 0
    n_inline = 0

    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue

        # Display math: emit on its own line with blank lines around so the
        # markdown parser treats it as a block (arithmatex recognition
        # requires display math to be standalone).
        def _disp_sub(m: re.Match) -> str:
            nonlocal n_display
            n_display += 1
            inner = m.group(1).strip()
            return f"\n\n\\[{inner}\\]\n\n"

        def _inl_sub(m: re.Match) -> str:
            nonlocal n_inline
            n_inline += 1
            return f"\\({m.group(1).strip()}\\)"

        content = _DISPLAY_DOLLAR_RE.sub(_disp_sub, content)
        content = _INLINE_DOLLAR_RE.sub(_inl_sub, content)
        out_parts.append(content)

    text = "".join(out_parts)

    # Also ensure existing \[...\] display blocks have blank lines around them
    text = _ensure_block_math_isolation(text)

    if n_display or n_inline:
        warns.append(
            SanitizeWarning(
                "math_delim_converted",
                f"converted {n_display} display + {n_inline} inline $-style math to backslash delims",
            )
        )
    return text, warns


# ─────────────────────────────────────────────────────────────────
# Transform — Escape stray `<`, `>`, `&` inside math spans
#
# A literal `<` next to a letter inside `\(…\)` (e.g. `\(T_1<T_2\)`) makes
# the HTML parser treat `<T_2\)` as the start of a tag. It then consumes
# characters as bogus attributes until the next `>`, which usually lives
# inside a closing `</div>` — silently eating that close tag and leaving
# every following section nested inside whatever container was open.
# In the wild this surfaces as horizontal-flow layout for the rest of the
# page plus raw `## H` / `---` markdown bleeding through (because the
# parser switched to "inside raw HTML block" mode and stopped processing
# markdown).
#
# KaTeX itself decodes `&lt;` / `&gt;` / `&amp;` at render time, so
# escaping is safe and inequalities still render correctly.
# ─────────────────────────────────────────────────────────────────

_MATH_INLINE_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_MATH_BLOCK_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _escape_html_in_math(text: str) -> tuple[str, list["SanitizeWarning"]]:
    """Replace `<` / `>` / `&` with HTML entities inside `\\(…\\)` and
    `\\[…\\]` math spans. Operates only on prose segments — code spans
    are skipped by `_split_segments`.

    Bare `&` is escaped to `&amp;`, but already-escaped sequences
    (`&lt;`, `&gt;`, `&amp;`, numeric refs) are left alone so we don't
    double-encode.
    """
    warns: list[SanitizeWarning] = []
    n_inline = 0
    n_block = 0

    _ENT_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos|nbsp);")

    def _esc(s: str) -> str:
        # Protect existing entities, escape bare special chars, restore.
        sentinels: list[str] = []

        def _stash(m: re.Match) -> str:
            sentinels.append(m.group(0))
            return f"\x00E{len(sentinels)-1}\x00"

        masked = _ENT_RE.sub(_stash, s)
        masked = masked.replace("&", "&amp;")
        masked = masked.replace("<", "&lt;").replace(">", "&gt;")
        masked = re.sub(
            r"\x00E(\d+)\x00",
            lambda m: sentinels[int(m.group(1))],
            masked,
        )
        return masked

    out_parts: list[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue

        def _on_inline(m: re.Match) -> str:
            nonlocal n_inline
            inner = m.group(1)
            esc = _esc(inner)
            if esc != inner:
                n_inline += 1
            return f"\\({esc}\\)"

        def _on_block(m: re.Match) -> str:
            nonlocal n_block
            inner = m.group(1)
            esc = _esc(inner)
            if esc != inner:
                n_block += 1
            return f"\\[{esc}\\]"

        content = _MATH_INLINE_RE.sub(_on_inline, content)
        content = _MATH_BLOCK_RE.sub(_on_block, content)
        out_parts.append(content)

    if n_inline or n_block:
        warns.append(SanitizeWarning(
            "math_html_escaped",
            f"escaped <,>,& in {n_inline} inline + {n_block} block math span(s) — "
            f"prevents bogus HTML tag parsing (e.g. <T_2\\) eating </div>)",
        ))
    return "".join(out_parts), warns


_BLOCK_MATH_RE = re.compile(r"(?<!\n\n)(\\\[.+?\\\])(?!\n\n)", re.DOTALL)


# Lines that start a numbered heading (`## 3.`, `### 2.1`) or a setext-style
# horizontal rule (`---`, `***`, `___`). When these abut a closing HTML tag or
# a `bart-*` fence with no blank line between them, python-markdown leaves the
# whole region as raw HTML and the heading / hr surfaces in the page as
# literal text. We pad such adjacencies with a blank line so the parser sees
# them as standalone block tokens, even if a streaming hiccup ate the original
# blank line.
_HEADING_OR_HR_LINE = re.compile(
    r"^(?:#{1,6}\s+\S|---+\s*$|\*\*\*+\s*$|___+\s*$)",
)
_HTML_BLOCK_BOUNDARY = re.compile(
    r"^(?:</?(?:div|figure|section|article|table|tr|td|th|tbody|thead|"
    r"tfoot|ul|ol|li|p|pre|details|summary|aside|header|footer|nav|"
    r"main|svg|g|path|rect|circle|ellipse|line|polygon|polyline|"
    r"text|defs|marker)\b|<!--|```bart-)"
)


def _pad_blocks_around_headings_and_rules(text: str) -> tuple[str, list["SanitizeWarning"]]:
    """Insert a blank line whenever a heading / HR is fused to an HTML block
    boundary (closing div, opening figure, library fence start/end, …).

    Concrete trigger: line ``</div>`` immediately followed by ``## 3. Foo``
    or ``---``. python-markdown bundles them into a single raw-HTML block;
    KaTeX never sees them; the page surfaces literal ``---`` or ``## 3. Foo``.

    We do NOT touch text inside fenced code blocks. We DO touch text inside
    library `bart-*` JSON fences? — no: those are still wrapped by the
    `_FENCE_RE` "code" segments and are skipped by `_split_segments`.
    """
    warns: list[SanitizeWarning] = []
    n_padded = 0
    out_parts: list[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue
        lines = content.split("\n")
        new_lines: list[str] = []
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            is_heading_or_hr = bool(_HEADING_OR_HR_LINE.match(stripped))
            if is_heading_or_hr:
                # Need a blank line before — but only if the preceding line
                # was an HTML block boundary AND the buffer doesn't already
                # end on a blank line.
                if new_lines and new_lines[-1].strip():
                    prev_stripped = new_lines[-1].lstrip()
                    if _HTML_BLOCK_BOUNDARY.match(prev_stripped) or prev_stripped.endswith(">"):
                        new_lines.append("")
                        n_padded += 1
                new_lines.append(line)
                # Need a blank line after — same conditions, looking forward.
                next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
                next_stripped = next_line.lstrip()
                if next_stripped and (
                    _HTML_BLOCK_BOUNDARY.match(next_stripped)
                    or next_stripped.startswith("<")
                ):
                    new_lines.append("")
                    n_padded += 1
                continue
            new_lines.append(line)
        out_parts.append("\n".join(new_lines))
    if n_padded:
        warns.append(SanitizeWarning(
            "padded_heading_or_hr",
            f"inserted {n_padded} blank line(s) around heading/HR adjacent to HTML",
        ))
    return "".join(out_parts), warns


def _ensure_block_math_isolation(text: str) -> str:
    """Surround \\[...\\] block math with blank lines if missing.

    Markdown parsers treat \\[ as an escaped [ unless the block is on its
    own line, separated by blank lines. This preprocessing makes recognition
    deterministic regardless of how the agent indented the original.
    """
    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
        else:
            # Find each \[...\] and ensure blank lines around it
            def _wrap(m: re.Match) -> str:
                inner = m.group(0)
                return f"\n\n{inner}\n\n"
            content = re.sub(r"\\\[(.+?)\\\]", _wrap, content, flags=re.DOTALL)
            # Collapse runs of >2 blank lines back down to 2
            content = re.sub(r"\n{3,}", "\n\n", content)
            out_parts.append(content)
    return "".join(out_parts)


# ─────────────────────────────────────────────────────────────────
# Transform 3 — Detect unbalanced math delimiters
# ─────────────────────────────────────────────────────────────────

def _repair_unbalanced_math(text: str) -> tuple[str, List[SanitizeWarning]]:
    """Close stray `\\[` blocks before they bleed into surrounding prose.

    A common author failure mode is opening a block-math segment with `\\[`
    and forgetting the matching `\\]`. The previous behavior logged a
    warning and let the broken markdown reach the renderer — KaTeX then
    treats every following character (sometimes whole paragraphs of prose)
    as math, which is exactly the "garbled" look the user reported.

    Strategy: walk the prose segments; whenever we see an open `\\[` whose
    next delimiter token is *not* a `\\]`, insert a synthetic `\\]` just
    before the next blank line (or end of doc). The same logic runs for
    inline `\\(...\\)`. We only repair stray *opens*; stray closes are
    left in place as text (KaTeX silently ignores them).
    """
    warns: List[SanitizeWarning] = []
    out_parts: List[str] = []
    n_repaired_block = 0
    n_repaired_inline = 0

    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
            continue

        # Block-level repair: scan for `\[` not followed by `\]`.
        # We rebuild the segment piece-by-piece so we can insert the close.
        i = 0
        buf: list[str] = []
        while i < len(content):
            j = content.find("\\[", i)
            if j < 0:
                buf.append(content[i:])
                break
            buf.append(content[i:j])
            close = content.find("\\]", j + 2)
            next_open = content.find("\\[", j + 2)
            # Stray if no close, OR close is past the next open.
            if close < 0 or (next_open >= 0 and next_open < close):
                # Insert close at the next blank line, or end of segment.
                blank = content.find("\n\n", j + 2)
                end = blank if blank >= 0 else len(content)
                buf.append(content[j:end].rstrip())
                buf.append("\\]")
                buf.append(content[end:end + 2])  # preserve the blank line
                i = end + 2 if blank >= 0 else len(content)
                n_repaired_block += 1
            else:
                buf.append(content[j:close + 2])
                i = close + 2
        repaired = "".join(buf)

        # Inline repair: a stray `\(` on a single line (no `\)` before EOL).
        def _close_stray_inline(m: re.Match) -> str:
            nonlocal n_repaired_inline
            line = m.group(0)
            n_open = line.count("\\(")
            n_close = line.count("\\)")
            if n_open > n_close:
                n_repaired_inline += (n_open - n_close)
                return line + ("\\)" * (n_open - n_close))
            return line

        repaired = re.sub(r"[^\n]*\\\([^\n]*", _close_stray_inline, repaired)

        out_parts.append(repaired)

    if n_repaired_block:
        warns.append(SanitizeWarning(
            "math_repaired_block",
            f"auto-closed {n_repaired_block} stray '\\[' block(s) at the next blank line",
        ))
    if n_repaired_inline:
        warns.append(SanitizeWarning(
            "math_repaired_inline",
            f"auto-closed {n_repaired_inline} stray '\\(' inline span(s) at line end",
        ))
    return "".join(out_parts), warns


def _check_math_balance(text: str) -> List[SanitizeWarning]:
    """Count opens vs closes of \\(…\\) and \\[…\\]. Mismatch -> warning.

    Runs AFTER `_repair_unbalanced_math`, so any remaining mismatch is
    something the repair couldn't fix (e.g. a stray `\\]` with no opener).
    Severity stays at warn rather than error since the repair already
    prevented the visible "garbled" failure mode.
    """
    warns: List[SanitizeWarning] = []
    prose = "".join(c for k, c in _split_segments(text) if k == "prose")
    n_open_inline = prose.count("\\(")
    n_close_inline = prose.count("\\)")
    n_open_block = prose.count("\\[")
    n_close_block = prose.count("\\]")
    if n_open_inline != n_close_inline:
        warns.append(
            SanitizeWarning(
                "unbalanced_math_inline",
                f"\\( count={n_open_inline} but \\) count={n_close_inline} (post-repair)",
            )
        )
    if n_open_block != n_close_block:
        warns.append(
            SanitizeWarning(
                "unbalanced_math_block",
                f"\\[ count={n_open_block} but \\] count={n_close_block} (post-repair)",
            )
        )
    # Also detect leftover bare $ pairs (might indicate failed conversion)
    # Counting $ in prose only.
    n_dollar = prose.count("$")
    if n_dollar % 2 == 1:
        warns.append(
            SanitizeWarning(
                "stray_dollar",
                f"odd number ({n_dollar}) of literal '$' in prose — possible math typo",
            )
        )
    return warns


# ─────────────────────────────────────────────────────────────────
# Transform 4 — Normalize quote characters
#   straighten "smart quotes" to ASCII quotes within code spans only;
#   leave prose smart quotes alone (they're prettier).
# ─────────────────────────────────────────────────────────────────

_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


def _normalize_quotes(text: str) -> str:
    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content.translate(_SMART_QUOTES))
        else:
            out_parts.append(content)
    return "".join(out_parts)


# ─────────────────────────────────────────────────────────────────
# Transform 5 — Strip emoji from prose (kept for brand consistency).
# Keep ASCII arrows and box-drawing characters.
# ─────────────────────────────────────────────────────────────────

_EMOJI_RE = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U0001F600-\U0001F64F" "\U0001F680-\U0001F6FF" "\U0001F900-\U0001F9FF" "☀-➿" "]",
    flags=re.UNICODE,
)
# Keep these specific ones — they're load-bearing in prose
_KEEP_EMOJI = {"✓", "✗", "→", "←", "↔", "⇒", "∈", "∉", "∩", "∪"}


def _strip_emoji(text: str) -> tuple[str, List[SanitizeWarning]]:
    warns: List[SanitizeWarning] = []
    n_stripped = 0

    def _repl(m: re.Match) -> str:
        nonlocal n_stripped
        ch = m.group(0)
        if ch in _KEEP_EMOJI:
            return ch
        n_stripped += 1
        return ""

    out_parts: List[str] = []
    for kind, content in _split_segments(text):
        if kind == "code":
            out_parts.append(content)
        else:
            out_parts.append(_EMOJI_RE.sub(_repl, content))
    if n_stripped:
        warns.append(SanitizeWarning("stripped_emoji", f"removed {n_stripped} decorative emoji"))
    return "".join(out_parts), warns


# ─────────────────────────────────────────────────────────────────
# Transform 6 — Convert <details>/<summary> Quick Check pattern
# into a custom div the renderer can target with class="qc-collapsible".
# We do this at the markdown layer (not HTML post-process) so the
# markdown extension processes the inner content as markdown.
# ─────────────────────────────────────────────────────────────────

_QC_BLOCK_RE = re.compile(
    r"<details>\s*<summary>(?P<summary>.*?)</summary>\s*"
    r"(?P<body>.*?)"
    r"\s*</details>",
    re.DOTALL,
)


def _tag_quickcheck_blocks(text: str) -> str:
    """Mark <details> blocks so the post-HTML pass can wrap them with our class.

    We don't replace them with custom HTML here because then markdown extensions
    won't process the body. Instead we leave them but ensure the structure is
    canonical (single newline after summary, body indented).
    """
    def _repl(m: re.Match) -> str:
        summary = m.group("summary").strip()
        body = m.group("body").strip()
        # Re-emit canonical form — markdown library can handle this;
        # the post-HTML pass will then add classes.
        return f"<details class=\"qc-collapsible\">\n<summary>{summary}</summary>\n\n{body}\n\n</details>"

    return _QC_BLOCK_RE.sub(_repl, text)


# ─────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────

def normalize_md(text: str) -> tuple[str, List[SanitizeWarning]]:
    """Run all transforms in order. Returns (clean_text, warnings)."""
    warnings: List[SanitizeWarning] = []

    text, w = _strip_ai_preamble(text)
    warnings.extend(w)

    text, w = _convert_math_delimiters(text)
    warnings.extend(w)

    # CRITICAL: escape `<`/`>`/`&` inside math spans BEFORE any further
    # transformation. A bare `<` next to a letter inside `\(…\)` (e.g.
    # `\(T_1<T_2\)`) makes python-markdown's HTML parser treat the math
    # as the start of a `<T_2\)` tag, which then consumes characters
    # until the next `>` (often inside a `</div>`). Escaping early closes
    # the entire class of bugs.
    text, w = _escape_html_in_math(text)
    warnings.extend(w)

    text, w = _pad_blocks_around_headings_and_rules(text)
    warnings.extend(w)

    text, w = _strip_emoji(text)
    warnings.extend(w)

    text = _normalize_quotes(text)
    text = _tag_quickcheck_blocks(text)

    # Repair stray `\[` / `\(` BEFORE the balance check so the post-repair
    # warning correctly reflects what KaTeX will see, not what the author
    # originally wrote.
    text, w = _repair_unbalanced_math(text)
    warnings.extend(w)

    warnings.extend(_check_math_balance(text))
    return text, warnings
