"""Idempotent autofixes for the format audit.

Each ``_fix_*(html) -> (html, n)`` applies a safe, idempotent string
transform and reports how many repairs it made — restricted to changes that
cannot make a working page worse. ``_FIXES`` is the ordered registry; the
order is load-bearing (math-HTML-escape first, the empty-span cleanup last
among math fixes, etc. — see the inline notes on ``_FIXES``).

Internal to ``bart.render.audit``; ``render/packet.py`` also folds these into
the render pipeline so rerendered HTML is already clean.
"""
from __future__ import annotations

import re

from ._shared import (
    _DISPLAY_DOLLAR_RE,
    _DOUBLE_ENTITY,
    _DOUBLE_ESCAPED,
    _EMPTY_MATH_RE,
    _HTML_ENT_AMP,
    _HTML_ENT_BACKSLASH,
    _HTML_ENT_GT,
    _HTML_ENT_LT,
    _IMG_TAG,
    _INLINE_DOLLAR_RE,
    _JSON_UNICODE_ESC,
    _MATH_BODY_CHAR,
    _MATH_CONTENT_HINT,
    _MATH_SPAN_RE,
    _MISMATCHED_OPEN_BRACKET,
    _MISMATCHED_OPEN_PAREN,
    _RAW_TEX_CMD_RE,
    _apply_outside_protected,
    _strip_protected,
)


# ─── Individual fixes ────────────────────────────────────────────


def _fix_math_html_leak(html: str) -> tuple[str, int]:
    """Escape `<`, `>`, `&` inside math spans in *rendered* HTML.

    The page may already be live-broken — bogus tags ate `</div>`s and
    layout is collapsed. This fix:

      1. Finds every `\\(…\\)` / `\\[…\\]` span outside protected regions.
      2. HTML-escapes raw `<` / `>` / `&` inside each span.

    KaTeX decodes the entities at render time, so the math still renders
    correctly (`T_1<T_2` displays as expected). The class structure is
    repaired enough that subsequent layout doesn't horizontalize, even
    if a separate full rebuild from markdown isn't triggered. The
    rebuild path still runs from the corruption-fingerprint stage; this
    fix is the safety net for cases where rebuild isn't possible.
    """
    _ENT_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|amp|lt|gt|quot|apos|nbsp);")

    def _esc_inside(inner: str) -> str:
        sentinels: list[str] = []

        def _stash(m: re.Match) -> str:
            sentinels.append(m.group(0))
            return f"\x00E{len(sentinels)-1}\x00"

        masked = _ENT_RE.sub(_stash, inner)
        masked = masked.replace("&", "&amp;")
        masked = masked.replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(
            r"\x00E(\d+)\x00",
            lambda m: sentinels[int(m.group(1))],
            masked,
        )

    inline_re = re.compile(r"\\\(([^\n]*?)\\\)", re.DOTALL)
    block_re = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)

    def _on(chunk: str) -> tuple[str, int]:
        n = [0]

        def _wrap_inline(m: re.Match) -> str:
            inner = m.group(1)
            esc = _esc_inside(inner)
            if esc != inner:
                n[0] += 1
            return f"\\({esc}\\)"

        def _wrap_block(m: re.Match) -> str:
            inner = m.group(1)
            esc = _esc_inside(inner)
            if esc != inner:
                n[0] += 1
            return f"\\[{esc}\\]"

        chunk = inline_re.sub(_wrap_inline, chunk)
        chunk = block_re.sub(_wrap_block, chunk)
        return chunk, n[0]

    return _apply_outside_protected(html, _on)


def _fix_double_escaped_entities(html: str) -> tuple[str, int]:
    """Collapse `&amp;amp;` → `&amp;`, `&amp;lt;` → `&lt;`, etc.

    Single-pass — re-running format will catch any deeper nesting.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = [0]
        def _repl(m: re.Match) -> str:
            n[0] += 1
            return "&" + m.group(0)[len("&amp;"):]
        new = _DOUBLE_ENTITY.sub(_repl, chunk)
        return new, n[0]
    return _apply_outside_protected(html, _on)


def _fix_json_unicode_escape(html: str) -> tuple[str, int]:
    """Decode literal `\\uXXXX` JSON escape sequences to actual Unicode chars.

    Handles the common rendering bug where a `bart-*` block's JSON string
    fields (e.g. `connect`, `contrast`, `apply`) reached the page without
    being JSON-decoded, leaving readers staring at `\\u03c4` and `\\u2014`
    instead of `τ` and `—`. Skips `<script>`, `<style>`, `<pre>`, `<code>`
    so legitimate `\\u…` literals in JS/CSS/source examples stay intact.

    Surrogate-pair codepoints (D800–DFFF) are decoded as a paired sequence
    when followed immediately by their low surrogate, otherwise the match
    is left untouched — `chr()` accepts the lone surrogate but it would
    produce malformed UTF-8 on round-trip through `path.write_text`.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        out: list[str] = []
        i = 0
        while i < len(chunk):
            m = _JSON_UNICODE_ESC.search(chunk, i)
            if not m:
                out.append(chunk[i:])
                break
            out.append(chunk[i:m.start()])
            cp = int(m.group(1), 16)
            if 0xD800 <= cp <= 0xDBFF:
                # Possible high surrogate — check for paired low surrogate.
                m2 = _JSON_UNICODE_ESC.match(chunk, m.end())
                if m2:
                    cp2 = int(m2.group(1), 16)
                    if 0xDC00 <= cp2 <= 0xDFFF:
                        combined = 0x10000 + ((cp - 0xD800) << 10) + (cp2 - 0xDC00)
                        out.append(chr(combined))
                        n += 1
                        i = m2.end()
                        continue
                out.append(m.group(0))
                i = m.end()
                continue
            if 0xDC00 <= cp <= 0xDFFF:
                # Lone low surrogate — leave as-is.
                out.append(m.group(0))
                i = m.end()
                continue
            out.append(chr(cp))
            n += 1
            i = m.end()
        return "".join(out), n
    return _apply_outside_protected(html, _on)


# Multi-escaped delimiter: any even count of backslashes (≥ 2) followed by
# a math delimiter — but NOT `\\[6pt]` / `\\[1em]` / etc. (the LaTeX `\\`
# linebreak with an optional vertical-space argument). Catches `\\\\(`,
# `\\\\\\(`, etc. emitted by pipelines that JSON-escaped twice or more.
_MULTI_ESCAPED = re.compile(
    r"(?:\\\\){2,}(\(|\)|\[(?!\d+\s*(?:pt|em|in|mm|cm|ex|sp|pc|bp|dd|cc)\b)|\])"
)


def _fix_double_escaped_math(html: str) -> tuple[str, int]:
    """Collapse `\\\\(`, `\\\\[`, etc. — including triple/quadruple-escaped
    forms emitted by an over-eager JSON pipeline — back to the single-
    backslash form KaTeX expects.

    Skips `<script>`, `<style>`, `<pre>`, `<code>` regions: the JS auto-render
    config legitimately contains `'\\\\('` so JS string evaluation produces the
    literal `\\(` delimiter — collapsing that here would silently downgrade
    the delimiters to plain `(`/`)`/`[`/`]` and KaTeX would start eating
    parenthetical prose as inline math.

    Two passes per chunk: first the multi-escape regex (`\\\\\\\\(` etc.),
    then the standard double-escape regex. Both produce a single-backslash
    delimiter so a downstream second invocation is unnecessary.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        def _repl(m: re.Match) -> str:
            nonlocal n
            n += 1
            return "\\" + m.group(1)
        chunk = _MULTI_ESCAPED.sub(_repl, chunk)
        chunk = _DOUBLE_ESCAPED.sub(_repl, chunk)
        return chunk, n
    return _apply_outside_protected(html, _on)


def _fix_unbalanced_block_math(html: str) -> tuple[str, int]:
    """Close stray `\\[` blocks at the next blank line.

    Mirrors format_check._autofix_unbalanced_block_math but operates on the
    final HTML. Skips protected regions so the KaTeX loader's `\\[` /  `\\]`
    delimiter config (which is part of `<script>` content) doesn't get
    counted into the balance.
    """
    def _on(chunk: str) -> tuple[str, int]:
        fixed = 0
        for _ in range(8):
            n_open = chunk.count("\\[")
            n_close = chunk.count("\\]")
            if n_open == n_close:
                break
            out: list[str] = []
            i = 0
            repaired_pass = 0
            while i < len(chunk):
                j = chunk.find("\\[", i)
                if j < 0:
                    out.append(chunk[i:])
                    break
                out.append(chunk[i:j])
                close = chunk.find("\\]", j + 2)
                next_open = chunk.find("\\[", j + 2)
                if close < 0 or (next_open >= 0 and next_open < close):
                    blank = chunk.find("\n\n", j + 2)
                    end = blank if blank >= 0 else len(chunk)
                    out.append(chunk[j:end].rstrip())
                    out.append("\\]")
                    if blank >= 0:
                        out.append(chunk[end:end + 2])
                        i = end + 2
                    else:
                        i = len(chunk)
                    repaired_pass += 1
                else:
                    out.append(chunk[j:close + 2])
                    i = close + 2
            chunk = "".join(out)
            fixed += repaired_pass
            if repaired_pass == 0:
                break
        return chunk, fixed

    html, fixed = _apply_outside_protected(html, _on)
    # Final fallback: any leftover stray `\[` outside protected regions still
    # needs a synthetic close so KaTeX can't run away. Re-count outside
    # protected regions only.
    stripped = _strip_protected(html)
    leftover = stripped.count("\\[") - stripped.count("\\]")
    if leftover > 0:
        body_end = html.lower().rfind("</body>")
        patch = "\n" + ("\\]" * leftover) + "\n"
        if body_end >= 0:
            html = html[:body_end] + patch + html[body_end:]
        else:
            html = html + patch
        fixed += leftover
    return html, fixed


def _fix_inline_font_overrides(html: str) -> tuple[str, int]:
    """Strip `font-size:Xpx` from inline `style=` attributes (other rules
    in the style attribute are preserved)."""
    n = 0

    def _strip(m: re.Match) -> str:
        nonlocal n
        s = m.group(0)
        s2 = re.sub(r"font-size\s*:\s*[^;\"]+;?\s*", "", s)
        if s2 != s:
            n += 1
        return s2

    html = re.sub(r'style="[^"]*"', _strip, html)
    return html, n


def _fix_displaymath_inside_p(html: str) -> tuple[str, int]:
    """Unwrap `<p>…<div class="arithmatex">\\[…\\]</div>…</p>` so the display
    math stands alone. We only touch `<p>` whose only meaningful child is the
    math wrapper."""
    pattern = re.compile(
        r"<p>\s*(<div class=\"arithmatex\">\\\[.*?\\\]</div>)\s*</p>",
        re.DOTALL,
    )
    new, n = pattern.subn(lambda m: m.group(1), html)
    return new, n


_GREEK = "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"


_PROSE_MATH_TOKEN_RE = re.compile(
    rf"""(
        (?<![\w\\])
        (?:
            d\[[A-Za-z{_GREEK}]{{1,4}}\]/dt
          | \[[A-Za-z{_GREEK}]{{1,4}}\](?:_(?:[0-9A-Za-z]+|\{{[^}}]+\}}))?
          | [A-Za-z{_GREEK}]\*?_(?:\{{[^}}]+\}}|-?\d+|[A-Za-z])
        )
        (?![\w])
    )""",
    re.VERBOSE,
)


_DOUBLE_BS_LATEX_CMD = re.compile(r"\\\\([A-Za-z]+)")


_DOUBLE_BS_SPACING = re.compile(r"\\\\([;,:! ])")


def _fix_prose_math_wrap(html: str) -> tuple[str, int]:
    """Wrap unbracketed math tokens in `\\(...\\)` so KaTeX renders them.

    Targets the pattern where authors emit `k_{-1}`, `[I]`, `d[I]/dt` etc.
    inline in prose without delimiters — those slip past KaTeX and surface
    as raw source. Skips:
      - text already inside a math span (`\\(…\\)` or `\\[…\\]`)
      - text inside attribute values, code, scripts (handled by
        `_apply_outside_protected` plus an attribute-skip pass below)
    """
    def _on(chunk: str) -> tuple[str, int]:
        # First, mask out math spans so we don't double-wrap.
        sentinels: list[str] = []
        def _stash_span(m: re.Match) -> str:
            sentinels.append(m.group(0))
            return f"\x00MS{len(sentinels)-1}\x00"
        masked = _MATH_SPAN_RE.sub(_stash_span, chunk)

        # And mask HTML tags / attributes — we only want to touch text between
        # tags, never tag attributes.
        tag_holds: list[str] = []
        def _stash_tag(m: re.Match) -> str:
            tag_holds.append(m.group(0))
            return f"\x00T{len(tag_holds)-1}\x00"
        masked2 = re.sub(r"<[^>]+>", _stash_tag, masked)

        n = [0]
        def _wrap(m: re.Match) -> str:
            n[0] += 1
            return f"\\({m.group(0)}\\)"
        masked2 = _PROSE_MATH_TOKEN_RE.sub(_wrap, masked2)

        # Restore tags then math spans.
        masked2 = re.sub(r"\x00T(\d+)\x00", lambda m: tag_holds[int(m.group(1))], masked2)
        masked2 = re.sub(r"\x00MS(\d+)\x00", lambda m: sentinels[int(m.group(1))], masked2)
        return masked2, n[0]
    return _apply_outside_protected(html, _on)


def _fix_double_escaped_latex_commands(html: str) -> tuple[str, int]:
    """Collapse `\\\\chi`, `\\\\circ`, etc. AND `\\\\;`, `\\\\,`, `\\\\:`,
    `\\\\!`, `\\\\ ` inside math spans only.

    Same reasoning as the markdown-side fix: an over-eager JSON pipeline can
    leave commands at JSON-escape level (literal `\\\\chi`) which KaTeX
    misreads as "linebreak then chi". The visible failure mode for the
    spacing variants (`\\\\;`, `\\\\,`) is a vertical column of stray
    punctuation in formula cards (linebreak then literal `;` / `,`).
    Restricting the transform to math regions keeps real "\\\\" sequences
    in prose (Windows paths, hand-drawn linebreaks in `<pre>`) untouched.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = [0]
        def _scrub_span(m: re.Match) -> str:
            inner = m.group(1)
            new = _DOUBLE_BS_LATEX_CMD.sub(lambda mm: "\\" + mm.group(1), inner)
            new = _DOUBLE_BS_SPACING.sub(lambda mm: "\\" + mm.group(1), new)
            if new != inner:
                n[0] += 1
            return new
        new_chunk = _MATH_SPAN_RE.sub(_scrub_span, chunk)
        return new_chunk, n[0]
    return _apply_outside_protected(html, _on)


_MATH_BODY_RE = re.compile(rf"[{_MATH_BODY_CHAR}]")


def _fix_raw_latex_leak(html: str) -> tuple[str, int]:
    """Wrap raw LaTeX commands in `\\(...\\)` so KaTeX renders them.

    Targets two leak patterns:
      1. Table cells like `<td>y(t) = \\int_{-\\infty}^{t} x(\\tau)d\\tau</td>`
         where the entire cell content is math but has no math delimiters.
      2. Inline math runs in prose like `... responds to a\\,x_1+b\\,x_2, not ...`
         where a TeX command + neighboring math chars is buried in prose.

    Conservative: only triggers on a curated list of TeX commands that
    don't appear in legitimate English. Skips text inside existing math
    spans, code/script regions, and HTML attribute values.
    """
    def _on(chunk: str) -> tuple[str, int]:
        sentinels: list[str] = []
        def _stash_span(m: re.Match) -> str:
            sentinels.append(m.group(0))
            return f"\x00MS{len(sentinels)-1}\x00"
        masked = _MATH_SPAN_RE.sub(_stash_span, chunk)

        tag_holds: list[str] = []
        def _stash_tag(m: re.Match) -> str:
            tag_holds.append(m.group(0))
            return f"\x00T{len(tag_holds)-1}\x00"
        masked2 = re.sub(r"<[^>]+>", _stash_tag, masked)

        n = 0
        out_parts: list[str] = []
        i = 0
        while i < len(masked2):
            m = _RAW_TEX_CMD_RE.search(masked2, i)
            if not m:
                out_parts.append(masked2[i:])
                break

            # Expand left: math chars and single spaces between math chars.
            # Multi-letter runs going leftward mean we crossed into an English
            # word — back off when a 2+ letter run is detected.
            start = m.start()
            letter_run = 0
            letter_run_origin = start
            while start > i:
                ch = masked2[start - 1]
                if ch == "\x00":
                    break
                if ch.isalpha():
                    if letter_run == 0:
                        letter_run_origin = start
                    letter_run += 1
                    if letter_run >= 2:
                        # English word boundary — back off the entire letter run
                        # and any space immediately to the right of it.
                        start = letter_run_origin
                        if start < m.start() and masked2[start:start + 1] == " ":
                            start += 1
                        break
                    start -= 1
                    continue
                letter_run = 0
                if _MATH_BODY_RE.match(ch) or ch == "\\":
                    start -= 1
                    continue
                if ch == " " and start - 1 > i:
                    prev = masked2[start - 2]
                    if _MATH_BODY_RE.match(prev) or prev in ")}]":
                        start -= 1
                        continue
                break
            # Expand right: math chars, additional TeX commands, and single
            # spaces between math chars (but not into English prose).
            end = m.end()
            while end < len(masked2):
                ch = masked2[end]
                if ch == "\x00":
                    break
                if _MATH_BODY_RE.match(ch):
                    end += 1
                    continue
                if ch == "\\":
                    sub = re.match(r"\\[A-Za-z]+|\\[,;:!]", masked2[end:])
                    if sub:
                        end += sub.end()
                        continue
                    break
                if ch == " " and end + 1 < len(masked2):
                    nxt = masked2[end + 1]
                    if nxt == "\\":
                        end += 1
                        continue
                    if _MATH_BODY_RE.match(nxt):
                        # If the next non-space starts a 3+ letter ASCII word
                        # followed by a non-math char, that's an English-word
                        # boundary — stop here.
                        if re.match(r"[A-Za-z]{3,}(?:[^A-Za-z0-9_^{}]|$)",
                                    masked2[end + 1:]):
                            break
                        end += 1
                        continue
                break
            # Trim trailing punctuation that isn't math-meaningful — but
            # never strip a `.` or `,` that's preceded by `\` (it's part of
            # a TeX spacing command like `\,` or `\.` and stripping it
            # would break the math run).
            while end > start and masked2[end - 1] in " .,":
                if (
                    masked2[end - 1] in ".,"
                    and end - 2 >= start
                    and masked2[end - 2] == "\\"
                ):
                    break
                end -= 1
            run = masked2[start:end]
            if run.strip() and "\\" in run:
                out_parts.append(masked2[i:start])
                out_parts.append("\\(" + run + "\\)")
                n += 1
                i = end
            else:
                out_parts.append(masked2[i:m.end()])
                i = m.end()

        new_chunk = "".join(out_parts)
        # Restore tags, then math spans.
        new_chunk = re.sub(r"\x00T(\d+)\x00", lambda m: tag_holds[int(m.group(1))], new_chunk)
        new_chunk = re.sub(r"\x00MS(\d+)\x00", lambda m: sentinels[int(m.group(1))], new_chunk)
        return new_chunk, n
    return _apply_outside_protected(html, _on)


_HTML_DOUBLE_SUPER_RE = re.compile(
    r"(\\[A-Za-z]+(?:_\{[^}]*\})?)\^\*_\{([^}]+)\}\^(\d+|\{[^}]+\})"
)


def _fix_double_superscript(html: str) -> tuple[str, int]:
    """Bracket bare-double-superscript sequences (`\\sigma^*_{2s}^2`) so
    KaTeX accepts them as `{\\sigma^*_{2s}}^2`."""
    def _on(chunk: str) -> tuple[str, int]:
        n = [0]
        def _wrap(m: re.Match) -> str:
            n[0] += 1
            base, sub, sup = m.group(1), m.group(2), m.group(3)
            return f"{{{base}^*_{{{sub}}}}}^{sup}"
        new = _HTML_DOUBLE_SUPER_RE.sub(_wrap, chunk)
        return new, n[0]
    return _apply_outside_protected(html, _on)


def _ensure_katex_loaded(html: str) -> tuple[str, int]:
    """Inject the KaTeX loader bundle when the autofix introduces math markers
    onto a page that didn't have any at render time.

    Sequence-of-events matters here: the renderer decides whether to inject
    `<script>` tags for KaTeX based on `has_math(html)` at render time. If the
    page contained zero math (e.g. a whimsical-notes page), KaTeX is omitted.
    A later autofix pass (`_fix_prose_math_wrap`) may then wrap `[A]` /
    `k_{-1}` / `\\(T_d\\)` patterns it found in prose — which puts new math
    markers into the HTML *after* the renderer's decision. Without re-injecting
    KaTeX, those markers stay as raw `\\(T_d\\)` source on screen.
    """
    body_start = html.find("<body")
    if body_start < 0:
        return html, 0
    body = html[body_start:]
    needs_math = (
        'class="arithmatex"' in body
        or "\\(" in _strip_protected(body)
        or "\\[" in _strip_protected(body)
        or "\\ce{" in body
        or "\\pu{" in body
    )
    if not needs_math:
        return html, 0
    if "katex.min.js" in html and "auto-render.min.js" in html:
        return html, 0
    # Figure out the rel-root the page already uses (data-rel-root attribute)
    m = re.search(r'data-rel-root="([^"]*)"', html)
    rel = m.group(1) if m else "."
    needs_chem = "\\ce{" in body or "\\pu{" in body
    from .assets import (
        KATEX_CSS_CDN, KATEX_JS_CDN, KATEX_AUTORENDER_CDN, KATEX_MHCHEM_CDN,
    )
    from .page import _KATEX_AUTORENDER_CONFIG
    chem_script = (
        f'<script defer src="{rel}/lib/katex/mhchem.min.js" '
        f'onerror="(function(){{var s=document.createElement(\'script\');'
        f's.src=\'{KATEX_MHCHEM_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
    ) if needs_chem else ""
    block = (
        f'<link rel="stylesheet" href="{rel}/lib/katex/katex.min.css" '
        f'onerror="this.onerror=null;this.href=\'{KATEX_CSS_CDN}\';">'
        f'<script defer src="{rel}/lib/katex/katex.min.js" '
        f'onerror="(function(){{var s=document.createElement(\'script\');'
        f's.src=\'{KATEX_JS_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
        f'{chem_script}'
        f'<script defer src="{rel}/lib/katex/auto-render.min.js" '
        f'onerror="(function(){{var s=document.createElement(\'script\');'
        f's.src=\'{KATEX_AUTORENDER_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
        f'<script defer>{_KATEX_AUTORENDER_CONFIG}</script>'
    )
    head_end = html.lower().find("</head>")
    if head_end < 0:
        return html, 0
    return html[:head_end] + block + html[head_end:], 1


def _fix_dollar_math(html: str) -> tuple[str, int]:
    """Convert `$x$` and `$$x$$` math to `\\(x\\)` and `\\[x\\]`.

    Conservative: requires the content to look like math (contains a TeX
    command, subscript, or superscript). Currency / shell-prompt prose is
    left alone.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        def _wrap_display(m: re.Match) -> str:
            nonlocal n
            inner = m.group(1)
            if _MATH_CONTENT_HINT.search(inner):
                n += 1
                return f"\\[{inner}\\]"
            return m.group(0)
        def _wrap_inline(m: re.Match) -> str:
            nonlocal n
            inner = m.group(1)
            if _MATH_CONTENT_HINT.search(inner):
                n += 1
                return f"\\({inner}\\)"
            return m.group(0)
        chunk = _DISPLAY_DOLLAR_RE.sub(_wrap_display, chunk)
        chunk = _INLINE_DOLLAR_RE.sub(_wrap_inline, chunk)
        return chunk, n
    return _apply_outside_protected(html, _on)


def _fix_empty_math_span(html: str) -> tuple[str, int]:
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        def _strip(m: re.Match) -> str:
            nonlocal n
            n += 1
            return ""
        return _EMPTY_MATH_RE.sub(_strip, chunk), n
    return _apply_outside_protected(html, _on)


def _fix_html_entity_in_math(html: str) -> tuple[str, int]:
    """Decode HTML entities that crept inside `\\(...\\)` / `\\[...\\]` spans
    and would otherwise reach KaTeX as literal `&amp;` / `&#x5C;` text."""
    def _on(chunk: str) -> tuple[str, int]:
        n = [0]
        def _decode_span(m: re.Match) -> str:
            inner = m.group(0)
            new = _HTML_ENT_BACKSLASH.sub("\\\\", inner)
            new = _HTML_ENT_AMP.sub("&", new)
            new = _HTML_ENT_LT.sub("<", new)
            new = _HTML_ENT_GT.sub(">", new)
            if new != inner:
                n[0] += 1
            return new
        new_chunk = _MATH_SPAN_RE.sub(_decode_span, chunk)
        return new_chunk, n[0]
    return _apply_outside_protected(html, _on)


def _fix_mismatched_math_delim(html: str) -> tuple[str, int]:
    """Repair `\\(...\\]` → `\\(...\\)` and `\\[...\\)` → `\\[...\\]`.

    Conservative: only triggers when the inner content is short (≤ 200
    chars) and contains no second math-delimiter, so we don't accidentally
    bridge two unrelated math spans.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        def _fix_paren(m: re.Match) -> str:
            nonlocal n
            inner = m.group(1)
            if len(inner) > 200 or "\\(" in inner or "\\[" in inner:
                return m.group(0)
            n += 1
            return f"\\({inner}\\)"
        def _fix_bracket(m: re.Match) -> str:
            nonlocal n
            inner = m.group(1)
            if len(inner) > 400 or "\\[" in inner or "\\(" in inner:
                return m.group(0)
            n += 1
            return f"\\[{inner}\\]"
        chunk = _MISMATCHED_OPEN_PAREN.sub(_fix_paren, chunk)
        chunk = _MISMATCHED_OPEN_BRACKET.sub(_fix_bracket, chunk)
        return chunk, n
    return _apply_outside_protected(html, _on)


def _fix_lazy_load_images(html: str) -> tuple[str, int]:
    """Add `loading="lazy"` to `<img>` tags missing it (skips images already
    above the fold — a heuristic; we just skip the first 2 images)."""
    n = 0
    seen = 0

    def _patch(m: re.Match) -> str:
        nonlocal n, seen
        attrs = m.group(1)
        seen += 1
        if seen <= 2:
            return m.group(0)
        if re.search(r"\bloading=", attrs, re.IGNORECASE):
            return m.group(0)
        n += 1
        return f"<img{attrs} loading=\"lazy\">"

    html = _IMG_TAG.sub(_patch, html)
    return html, n


_FIXES: list[tuple[str, Callable[[str], tuple[str, int]]]] = [
    # Run math-HTML-escape FIRST: leaving `<` next to a letter inside `\(…\)`
    # would let the autorender helper treat math as HTML and eat closing tags
    # again at view time. Escaping early also stops downstream prose-math
    # detection from picking up the raw `<` as new math.
    ("math_html_leak",               _fix_math_html_leak),
    # Multi-escape collapse (now handles double, triple, quadruple, …).
    ("double_escaped_math",          _fix_double_escaped_math),
    # Mismatched delimiters BEFORE prose-math-wrap so the wrap doesn't
    # walk into a half-open math span and produce more breakage.
    ("mismatched_math_delim",        _fix_mismatched_math_delim),
    # Dollar-math conversion runs early so the resulting `\(…\)` spans get
    # the same downstream protection as native delimiters.
    ("dollar_math",                  _fix_dollar_math),
    ("double_escaped_latex_command", _fix_double_escaped_latex_commands),
    ("html_entity_in_math",          _fix_html_entity_in_math),
    ("double_superscript",           _fix_double_superscript),
    ("math_unbalanced_block",        _fix_unbalanced_block_math),
    # Raw LaTeX leak (e.g., `\int_{-\infty}^{t} x(\tau)d\tau` in a table cell
    # with no math delimiters). Run BEFORE prose_math_wrap so the inserted
    # `\(...\)` spans get respected by downstream passes.
    ("raw_latex_leak",               _fix_raw_latex_leak),
    ("prose_math_wrap",              _fix_prose_math_wrap),
    # Empty-span cleanup runs LAST among math fixes so any earlier pass
    # that accidentally left `\(\)` gets cleaned up before the page ships.
    ("empty_math_span",              _fix_empty_math_span),
    # Re-inject the KaTeX bundle if a previous fix introduced new math markers.
    ("katex_loader_injected",        _ensure_katex_loaded),
    ("inline_font_size_override",    _fix_inline_font_overrides),
    ("displaymath_in_paragraph",     _fix_displaymath_inside_p),
    ("double_escaped_entity",        _fix_double_escaped_entities),
    # JSON `\uXXXX` decode runs late so any earlier passes that emit
    # transient escape sequences (none currently do) still get cleaned up.
    ("json_unicode_escape",          _fix_json_unicode_escape),
    ("img_missing_lazy_load",        _fix_lazy_load_images),
]

