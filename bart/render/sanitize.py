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


_BLOCK_MATH_RE = re.compile(r"(?<!\n\n)(\\\[.+?\\\])(?!\n\n)", re.DOTALL)


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

def _check_math_balance(text: str) -> List[SanitizeWarning]:
    """Count opens vs closes of \\(…\\) and \\[…\\]. Mismatch -> warning."""
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
                f"\\( count={n_open_inline} but \\) count={n_close_inline}",
            )
        )
    if n_open_block != n_close_block:
        warns.append(
            SanitizeWarning(
                "unbalanced_math_block",
                f"\\[ count={n_open_block} but \\] count={n_close_block}",
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

    text, w = _strip_emoji(text)
    warnings.extend(w)

    text = _normalize_quotes(text)
    text = _tag_quickcheck_blocks(text)

    warnings.extend(_check_math_balance(text))
    return text, warnings
