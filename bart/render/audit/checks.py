"""Read-only structural / typographic / accessibility checks.

Each ``_check_*(rel, html) -> list[AuditIssue]`` flags problems with file
path, best-effort line number, severity, and a one-line human summary — and
mutates nothing. ``_CHECKS`` is the ordered registry the driver walks.

Internal to ``bart.render.audit``.
"""
from __future__ import annotations

import re

from ._shared import (
    AuditIssue,
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
    _MATH_CONTENT_HINT,
    _MATH_SPAN_RE,
    _MISMATCHED_OPEN_BRACKET,
    _MISMATCHED_OPEN_PAREN,
    _RAW_TEX_CMD_RE,
    _line_of,
    _strip_protected,
)


# ─── Individual checks ───────────────────────────────────────────


def _check_doctype_and_lang(rel: str, html: str) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    if not re.match(r"^\s*<!doctype\s+html", html, re.IGNORECASE):
        issues.append(AuditIssue(rel, "missing_doctype", "page is missing <!doctype html>", "error"))
    if not re.search(r'<html\b[^>]*\blang=', html, re.IGNORECASE):
        issues.append(AuditIssue(rel, "missing_lang", "<html> tag has no lang= attribute", "warn"))
    if not re.search(r'<meta\s+charset=', html, re.IGNORECASE):
        issues.append(AuditIssue(rel, "missing_charset", "<meta charset> missing in <head>", "error"))
    if not re.search(r'<meta\s+name="viewport"', html, re.IGNORECASE):
        issues.append(AuditIssue(rel, "missing_viewport", "<meta name=\"viewport\"> missing", "warn"))
    return issues


def _check_title_present(rel: str, html: str) -> list[AuditIssue]:
    m = re.search(r"<title>([^<]*)</title>", html, re.IGNORECASE)
    if not m:
        return [AuditIssue(rel, "missing_title", "<title> tag missing", "error")]
    if not m.group(1).strip():
        return [AuditIssue(rel, "empty_title", "<title> tag is empty", "warn")]
    return []


def _check_katex_wired_when_math_present(rel: str, html: str) -> list[AuditIssue]:
    """If raw `\\(`/`\\[` or `class=\"arithmatex\"` shows up in the body, the
    head must load KaTeX. Otherwise the page ships unrendered LaTeX source."""
    body = html.split("<body", 1)[-1]
    has_math_marker = (
        'class="arithmatex"' in body
        or "\\(" in _strip_protected(body)
        or "\\[" in _strip_protected(body)
        or "\\ce{" in body
        or "\\pu{" in body
    )
    if not has_math_marker:
        return []
    issues: list[AuditIssue] = []
    if "katex.min.js" not in html:
        issues.append(AuditIssue(rel, "katex_not_loaded",
                                 "page contains math markers but katex.min.js was not injected",
                                 "error"))
    if "auto-render.min.js" not in html:
        issues.append(AuditIssue(rel, "autorender_not_loaded",
                                 "page contains math markers but auto-render.min.js was not injected",
                                 "error"))
    if ("\\ce{" in body or "\\pu{" in body) and "mhchem.min.js" not in html:
        issues.append(AuditIssue(rel, "mhchem_not_loaded",
                                 "page uses \\ce{} or \\pu{} but mhchem.min.js was not injected",
                                 "error"))
    return issues


_BLOCK_OPEN = re.compile(r"\\\[")


_BLOCK_CLOSE = re.compile(r"\\\]")


_INLINE_OPEN = re.compile(r"\\\(")


_INLINE_CLOSE = re.compile(r"\\\)")


def _check_math_balance(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    n_bo = len(_BLOCK_OPEN.findall(body))
    n_bc = len(_BLOCK_CLOSE.findall(body))
    n_io = len(_INLINE_OPEN.findall(body))
    n_ic = len(_INLINE_CLOSE.findall(body))
    issues: list[AuditIssue] = []
    if n_bo != n_bc:
        issues.append(AuditIssue(
            rel, "math_unbalanced_block",
            f"\\[ count={n_bo} but \\] count={n_bc} — block math will leak into surrounding prose",
            "error",
        ))
    if n_io != n_ic:
        issues.append(AuditIssue(
            rel, "math_unbalanced_inline",
            f"\\( count={n_io} but \\) count={n_ic} — inline math will render as plain text",
            "warn",
        ))
    return issues


def _check_double_escaped_math(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    hits = list(_DOUBLE_ESCAPED.finditer(body))
    if not hits:
        return []
    return [AuditIssue(
        rel, "double_escaped_math",
        f"{len(hits)} double-escaped delimiter(s) (`\\\\(`, `\\\\[`, …) — KaTeX won't render these",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


def _check_dollar_math(rel: str, html: str) -> list[AuditIssue]:
    """Flag `$x$` / `$$x$$` math that KaTeX won't render under our config."""
    body = _strip_protected(html)
    body_no_spans = _MATH_SPAN_RE.sub("", body)
    body_no_tags = re.sub(r"<[^>]+>", "", body_no_spans)
    hits = 0
    for m in _INLINE_DOLLAR_RE.finditer(body_no_tags):
        if _MATH_CONTENT_HINT.search(m.group(1)):
            hits += 1
    for m in _DISPLAY_DOLLAR_RE.finditer(body_no_tags):
        if _MATH_CONTENT_HINT.search(m.group(1)):
            hits += 1
    if not hits:
        return []
    return [AuditIssue(
        rel, "dollar_math",
        f"{hits} `$…$` / `$$…$$` math span(s) — KaTeX is configured for "
        f"`\\(…\\)` and `\\[…\\]` delimiters",
        "error",
    )]


def _check_empty_math_span(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    hits = list(_EMPTY_MATH_RE.finditer(body))
    if not hits:
        return []
    return [AuditIssue(
        rel, "empty_math_span",
        f"{len(hits)} empty math span(s) — KaTeX prints a parse error in red",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


def _check_html_entity_in_math(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    hits = 0
    for m in _MATH_SPAN_RE.finditer(body):
        s = m.group(0)
        if _HTML_ENT_BACKSLASH.search(s) or _HTML_ENT_AMP.search(s) \
                or _HTML_ENT_LT.search(s) or _HTML_ENT_GT.search(s):
            hits += 1
    if not hits:
        return []
    return [AuditIssue(
        rel, "html_entity_in_math",
        f"{hits} math span(s) contain HTML entities (`&amp;`, `&#x5C;`, …) — "
        f"KaTeX won't decode these",
        "error",
    )]


def _check_mismatched_math_delim(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    hits = 0
    for m in _MISMATCHED_OPEN_PAREN.finditer(body):
        if len(m.group(1)) <= 200 and "\\(" not in m.group(1) and "\\[" not in m.group(1):
            hits += 1
    for m in _MISMATCHED_OPEN_BRACKET.finditer(body):
        if len(m.group(1)) <= 400 and "\\[" not in m.group(1) and "\\(" not in m.group(1):
            hits += 1
    if not hits:
        return []
    return [AuditIssue(
        rel, "mismatched_math_delim",
        f"{hits} math span(s) with mismatched delimiters (`\\(…\\]` or `\\[…\\)`)",
        "error",
    )]


def _check_raw_latex_leak(rel: str, html: str) -> list[AuditIssue]:
    """Flag pages where a TeX command appears outside any math span.

    The fix `_fix_raw_latex_leak` will wrap these on `--fix`, but the
    audit alone surfaces the count so users know the page has the leak.
    """
    body_no_spans = _MATH_SPAN_RE.sub("", _strip_protected(html))
    body_no_tags = re.sub(r"<[^>]+>", "", body_no_spans)
    hits = list(_RAW_TEX_CMD_RE.finditer(body_no_tags))
    if not hits:
        return []
    return [AuditIssue(
        rel, "raw_latex_leak",
        f"{len(hits)} raw LaTeX command(s) (e.g. `\\int`, `\\tau`) outside math delimiters — "
        f"will render as source text",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


_STRAY_DOLLAR = re.compile(r"(?<![\\\d])\$\d|\d\$(?!\d)")  # very loose


def _check_stray_dollar(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    n = body.count("$")
    if n == 0:
        return []
    if n % 2 == 1:
        return [AuditIssue(rel, "stray_dollar",
                           f"odd ({n}) literal '$' in prose — author may have meant $math$",
                           "warn")]
    return []


_RENDER_FALLBACK = re.compile(
    r'<pre class=[\'"]render-fallback[\'"]>|<div class=[\'"]render-fallback-error[\'"]'
)


def _check_render_fallback(rel: str, html: str) -> list[AuditIssue]:
    """Render-fallback means the markdown converter raised AND the recovery
    pass without `arithmatex` also failed. The page now shows a visible
    error block + the raw markdown source. Surface as an error so it's
    obvious — and so the corruption-trigger fires the rebuild path."""
    if _RENDER_FALLBACK.search(html):
        return [AuditIssue(
            rel, "render_fallback_present",
            "page contains a render-fallback block — markdown failed to "
            "render and recovery couldn't repair it. Inspect the source "
            "for malformed `bart-*` JSON or unbalanced delimiters.",
            "error",
        )]
    return []


_BART_BLOCK_ERROR = re.compile(r'<div class="bart-block-error"', re.IGNORECASE)


_BART_BLOCK_ERROR_DETAIL = re.compile(
    r'<div class="bart-block-error"[^>]*>\s*<strong>([^<]+)</strong>\s*failed:\s*([^<]{0,200})</div>',
    re.IGNORECASE | re.DOTALL,
)


def _check_block_error(rel: str, html: str) -> list[AuditIssue]:
    hits = list(_BART_BLOCK_ERROR.finditer(html))
    if not hits:
        return []
    detail_hits = list(_BART_BLOCK_ERROR_DETAIL.finditer(html))
    issues: list[AuditIssue] = []
    if detail_hits:
        # Surface up to the first 3 specific failures with block name + reason.
        for m in detail_hits[:3]:
            block_name = m.group(1).strip()
            reason = m.group(2).strip()
            issues.append(AuditIssue(
                rel, "block_error_present",
                f"`{block_name}` failed: {reason} — fix the source markdown's "
                f"JSON payload, then rerender",
                "error",
                line=_line_of(html, m.start()),
            ))
        if len(hits) > len(issues):
            issues.append(AuditIssue(
                rel, "block_error_present",
                f"{len(hits) - len(issues)} more library block(s) also failed (see HTML)",
                "error",
            ))
    else:
        issues.append(AuditIssue(
            rel, "block_error_present",
            f"{len(hits)} library block(s) failed to render (visible yellow error stub)",
            "error",
            line=_line_of(html, hits[0].start()),
        ))
    return issues


_RAW_FENCE_LEAK = re.compile(r"```bart-[a-z\-]+", re.IGNORECASE)


def _check_raw_fence_leak(rel: str, html: str) -> list[AuditIssue]:
    """`` ```bart-foo `` should always be expanded by block_expand. If we see
    one in the final HTML, the expander missed it.

    The sandbox page legitimately displays `bart-*` fence syntax in its
    sample-text placeholder, so it's exempt from this check."""
    if rel.endswith("sandbox.html"):
        return []
    if _RAW_FENCE_LEAK.search(html):
        return [AuditIssue(rel, "raw_bart_fence",
                           "raw ```bart-* code fence reached final HTML (block_expand missed it)",
                           "error")]
    return []


_RAW_TRIPLE_BACKTICK = re.compile(r"```")


def _check_unclosed_code_fence(rel: str, html: str) -> list[AuditIssue]:
    """An unclosed ``` fence in the source markdown leaves the literal
    triple-backtick text in a `<p>` paragraph (python-markdown gives up on
    the fence and dumps the body as prose), so the page renders as one
    continuous chunk of markdown-looking text instead of a code block.

    Detect: any literal ``` reaching the final HTML *outside* of <pre>,
    <code>, <script>, <style>. Those regions legitimately contain
    triple-backticks (sample source, KaTeX delim config). The sandbox page
    is exempt because it displays sample fence syntax verbatim."""
    if rel.endswith("sandbox.html"):
        return []
    body = _strip_protected(html.split("<body", 1)[-1])
    hits = list(_RAW_TRIPLE_BACKTICK.finditer(body))
    if not hits:
        return []
    return [AuditIssue(
        rel, "unclosed_code_fence",
        f"{len(hits)} literal ``` in HTML body — source markdown has an "
        f"unclosed fence; needs a closing ``` + re-render from source",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


_PYGMENTS_GH_TOKEN = re.compile(r'<span class="gh">')


def _check_llm_wrapper_fence(rel: str, html: str) -> list[AuditIssue]:
    """Source markdown started with ``` ```markdown ``` (or similar) so the
    document head renders as one giant Pygments-highlighted code block."""
    if rel.endswith("sandbox.html"):
        return []
    body = html.split("<body", 1)[-1]
    if _PYGMENTS_GH_TOKEN.search(body):
        return [AuditIssue(
            rel, "llm_wrapper_fence",
            "page contains a Pygments-highlighted markdown heading "
            "(`<span class=\"gh\">`) — source `.md` opened with a "
            "```markdown wrapper fence; strip + re-render from source",
            "error",
        )]
    return []


_MD_LINK_LEAK = re.compile(r"(?<![\"'`])\[[^\]\n]+\]\([^)\n]+\)")


def _check_markdown_leak(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html)
    # ignore the parts that arithmatex wraps; KaTeX will re-process those
    body = re.sub(r'<(?:span|div) class="arithmatex">.*?</(?:span|div)>', "",
                  body, flags=re.DOTALL)
    if _MD_LINK_LEAK.search(body):
        return [AuditIssue(rel, "markdown_leak",
                           "markdown `[label](url)` syntax leaked into HTML — link wasn't converted",
                           "warn")]
    return []


_BROKEN_ANCHOR_HREF = re.compile(r'href="#([^"]+)"')


_HTML_ID = re.compile(r'\sid="([^"]+)"')


def _check_anchors(rel: str, html: str) -> list[AuditIssue]:
    ids = set(_HTML_ID.findall(html))
    issues: list[AuditIssue] = []
    seen: set[str] = set()
    for h in _BROKEN_ANCHOR_HREF.findall(html):
        if h in ids or h in seen:
            continue
        seen.add(h)
        issues.append(AuditIssue(rel, "broken_anchor",
                                 f'href="#{h}" has no matching element id',
                                 "warn"))
    return issues


def _check_images(rel: str, html: str) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    for m in _IMG_TAG.finditer(html):
        attrs = m.group(1)
        if not re.search(r'\salt=', attrs, re.IGNORECASE):
            issues.append(AuditIssue(rel, "img_missing_alt",
                                     "<img> without alt= attribute",
                                     "warn",
                                     line=_line_of(html, m.start())))
    return issues


_INLINE_FONT_SIZE = re.compile(r'style="[^"]*font-size\s*:\s*\d', re.IGNORECASE)


def _check_inline_font_overrides(rel: str, html: str) -> list[AuditIssue]:
    hits = list(_INLINE_FONT_SIZE.finditer(html))
    if not hits:
        return []
    return [AuditIssue(rel, "inline_font_size_override",
                       f"{len(hits)} inline `font-size:` style(s) — fights the global type scale",
                       "warn",
                       line=_line_of(html, hits[0].start()))]


_LIB_BLOCK_RE = re.compile(
    r'<div class="(b-formula-card|b-worked-example|b-concept-build|'
    r'b-trap-callout|b-mnemonic-card|b-why-it-matters|b-flowchart|'
    r'b-fib|b-mp|b-checkpoint)\b'
)


def _check_library_block_density(rel: str, html: str) -> list[AuditIssue]:
    """Daily lessons should have a healthy mix of library blocks; if a lesson
    page renders zero, the markdown likely lost its block fences."""
    if "/lessons/" not in rel and "lesson" not in rel and "day_" not in rel:
        return []
    if _LIB_BLOCK_RE.search(html):
        return []
    return [AuditIssue(rel, "no_library_blocks",
                       "lesson page contains zero library blocks (formula-card / "
                       "worked-example / trap-callout / …) — likely a markdown extraction bug",
                       "warn")]


_PRE_LATEX = re.compile(r'<pre\b[^>]*>([^<]{0,400})</pre>', re.IGNORECASE)


def _check_pre_holds_latex(rel: str, html: str) -> list[AuditIssue]:
    """If a `<pre>` block contains raw `\\[…\\]` it means the renderer escaped
    a math segment as code — KaTeX won't touch it."""
    issues: list[AuditIssue] = []
    for m in _PRE_LATEX.finditer(html):
        body = m.group(1)
        if "\\[" in body or "\\(" in body or "\\frac" in body:
            issues.append(AuditIssue(
                rel, "latex_inside_pre",
                "<pre> block contains raw LaTeX (will not be rendered by KaTeX)",
                "warn",
                line=_line_of(html, m.start()),
            ))
            break
    return issues


_NBSP_SOUP = re.compile(r"(?:&nbsp;){5,}")


def _check_nbsp_soup(rel: str, html: str) -> list[AuditIssue]:
    if _NBSP_SOUP.search(html):
        return [AuditIssue(rel, "nbsp_soup",
                           "5+ consecutive &nbsp; — author used spaces for layout, breaks reflow",
                           "warn")]
    return []


_DEPRECATED_TAG = re.compile(r"<(font|center|marquee|blink)\b", re.IGNORECASE)


def _check_deprecated_tags(rel: str, html: str) -> list[AuditIssue]:
    m = _DEPRECATED_TAG.search(html)
    if m:
        return [AuditIssue(rel, "deprecated_tag",
                           f"deprecated/banned tag <{m.group(1)}> in markup",
                           "warn",
                           line=_line_of(html, m.start()))]
    return []


_MOJIBAKE = re.compile(r"[ÃâÂ]{2,}")  # cheap heuristic for utf8-as-cp1252


def _check_mojibake(rel: str, html: str) -> list[AuditIssue]:
    if _MOJIBAKE.search(_strip_protected(html)):
        return [AuditIssue(rel, "mojibake_run",
                           "suspected encoding mojibake (utf-8 displayed as cp1252)",
                           "warn")]
    return []


_KATEX_DELIM_CONFIG_RE = re.compile(
    r"renderMathInElement\b[\s\S]{0,400}?delimiters\s*:\s*\[([\s\S]{0,400}?)\]",
    re.IGNORECASE,
)


def _check_katex_delimiter_config(rel: str, html: str) -> list[AuditIssue]:
    """Verify the auto-render JS still uses correctly-escaped delimiters.

    The KaTeX loader script must contain delimiter pairs whose JS source
    literals are `'\\\\('`, `'\\\\)'`, `'\\\\['`, `'\\\\]'` (two literal
    backslashes + the paren/bracket inside the single quotes). JS string
    evaluation collapses each to `\\(`, `\\)`, `\\[`, `\\]` — the actual
    KaTeX delimiters that match the literal `\\[…\\]` in our HTML body.

    A previous regression (`_fix_double_escaped_math`) walked into the
    `<script>` region and collapsed `\\\\(` → `\\(`, which JS string-evaluates
    to plain `(`. KaTeX then matched every parenthesised aside in the prose
    as inline math, every concentration `[A]` as a standalone display
    block, and every `\\[…\\]` block as broken garbage. This check exists
    to catch that silent breakage if it ever happens again.
    """
    m = _KATEX_DELIM_CONFIG_RE.search(html)
    if not m:
        return []
    config = m.group(1)
    # Smoking-gun: `'\(', '\)', '\[', '\]'` in JS source means delimiters
    # degraded to plain paren/bracket. Build the pattern with re.escape so
    # there's no way to accidentally re-introduce regex-syntax bugs.
    bad_left = "'" + "\\" + "("
    bad_right = "'" + "\\" + ")"
    if (re.escape(bad_left) in re.escape(config)
            or "'" + "\\(" in config
            or "'" + "\\[" in config):
        # Now distinguish degraded (`'\('`) from healthy (`'\\('`).
        # Healthy form must contain `'\\(` (apostrophe + 2 backslashes + paren).
        healthy = "'" + "\\\\" + "("
        if healthy not in config:
            return [AuditIssue(
                rel, "katex_delimiters_collapsed",
                "auto-render JS has single-backslash delimiters; JS string-"
                "evaluates them to plain `(`/`[` so KaTeX matches every "
                "parenthesis in prose as inline math. Re-render the page.",
                "error",
            )]
    return []


_KATEX_DISPLAY_INSIDE_P = re.compile(
    r"<p\b[^>]*>\s*<(?:div|span) class=\"arithmatex\">\\\[",
    re.IGNORECASE,
)


_RAW_MD_HR_LINE = re.compile(r"(?:^|>|\n)\s*(?:-{3,}|_{3,}|\*{3,})\s*\n")


_RAW_MD_HEADING = re.compile(
    r"(?:>|\n)\s*(#{2,6})\s+([0-9]+(?:\.[0-9]+)?[\.\)]?\s+[^\n<]{1,200})"
)


def _check_raw_md_hr_leak(rel: str, html: str) -> list[AuditIssue]:
    """`---` on its own line that did NOT become an `<hr>`.

    Restricts to the body and ignores `<pre>`, `<code>`, `<script>`. The
    `_strip_protected` pass also masks out delim-config strings inside
    auto-render JS so we don't false-positive on `'\\['`/`'\\]'`.
    """
    body = _strip_protected(html.split("<body", 1)[-1])
    hits = list(_RAW_MD_HR_LINE.finditer(body))
    if not hits:
        return []
    return [AuditIssue(
        rel, "raw_md_hr_leak",
        f"{len(hits)} raw `---` / `***` line(s) in HTML body — markdown HR "
        f"failed to convert to <hr>; likely a streaming-truncation in the source",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


def _check_raw_md_heading_leak(rel: str, html: str) -> list[AuditIssue]:
    body = _strip_protected(html.split("<body", 1)[-1])
    hits = list(_RAW_MD_HEADING.finditer(body))
    if not hits:
        return []
    sample = hits[0].group(0).strip().splitlines()[-1][:80]
    return [AuditIssue(
        rel, "raw_md_heading_leak",
        f"{len(hits)} raw markdown heading(s) in HTML body (e.g. '{sample}')"
        f" — likely streaming-truncation; needs re-render from source markdown",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


_BROKEN_CLASS_ATTR = re.compile(
    r'<\w+\s+(?:cls|clas|claas|clan|clab|classs|cass)\b[^>]*>',
    re.IGNORECASE,
)


_EMPTY_TAG = re.compile(r"<\s*>|<<\w|</\s*>")


_ORPHAN_QUOTE_IN_TAG = re.compile(r'<\w+\s+[A-Za-z][\w\-]*[A-Za-z0-9]"')


_LEADING_HYPHEN_ATTR = re.compile(r'<\w+\s+-\w[\w\-]*"')


_TAGNAME_WITH_EQUALS = re.compile(
    r'<(?:style|script|html|body|head|meta|link|br|hr|p|div|span|ul|ol|'
    r'li|h[1-6]|table|tr|td|th|tbody|thead|tfoot|figure|section|article|'
    r'header|footer|nav|main|aside|button|input|label|svg|path|g|rect|'
    r'circle|line|text|defs|marker)=',
    re.IGNORECASE,
)


def _check_broken_attrs(rel: str, html: str) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    m = _BROKEN_CLASS_ATTR.search(html)
    if m:
        issues.append(AuditIssue(
            rel, "broken_class_attr",
            f"streaming-corrupted attribute near '{m.group(0)[:60]}…' — "
            f"page needs re-render or regeneration",
            "error",
            line=_line_of(html, m.start()),
        ))
    m2 = _EMPTY_TAG.search(html)
    if m2:
        issues.append(AuditIssue(
            rel, "empty_tag",
            f"empty/orphan tag '{m2.group(0)}' — likely streaming-corrupted",
            "warn",
            line=_line_of(html, m2.start()),
        ))
    m3 = _ORPHAN_QUOTE_IN_TAG.search(html)
    if m3:
        issues.append(AuditIssue(
            rel, "orphan_quote_in_tag",
            f"attribute missing `=` near '{m3.group(0)[:60]}…' — "
            f"streaming-corrupted; page needs rebuild from markdown",
            "error",
            line=_line_of(html, m3.start()),
        ))
    m4 = _LEADING_HYPHEN_ATTR.search(html)
    if m4:
        issues.append(AuditIssue(
            rel, "leading_hyphen_attr",
            f"tag attribute starts with `-` ('{m4.group(0)[:60]}…') — "
            f"likely a character-drop in the attribute name",
            "error",
            line=_line_of(html, m4.start()),
        ))
    m5 = _TAGNAME_WITH_EQUALS.search(html)
    if m5:
        issues.append(AuditIssue(
            rel, "tagname_with_equals",
            f"tag name fused with `=` ('{m5.group(0)[:30]}…') — the "
            f"`class` / `id` keyword got dropped from the attribute",
            "error",
            line=_line_of(html, m5.start()),
        ))
    return issues


def _check_double_escaped_entities(rel: str, html: str) -> list[AuditIssue]:
    """`&amp;amp;` / `&amp;lt;` etc. — entity got HTML-escaped twice.

    Surfaces in TOC labels and prose when the markdown sanitizer feeds an
    already-escaped string to the renderer. Cosmetic but ugly.
    """
    body = _strip_protected(html)
    hits = list(_DOUBLE_ENTITY.finditer(body))
    if not hits:
        return []
    return [AuditIssue(
        rel, "double_escaped_entity",
        f"{len(hits)} double-escaped HTML entit(y/ies) (e.g. '{hits[0].group(0)}')",
        "warn",
        line=_line_of(html, hits[0].start()),
    )]


def _check_json_unicode_escape(rel: str, html: str) -> list[AuditIssue]:
    """Literal `\\uXXXX` JSON escape sequences that survived into rendered HTML.

    Surfaces when a `bart-*` block's JSON payload was emitted as text without
    its strings being decoded — readers see `\\u03c4` instead of `τ`,
    `\\u2014` instead of `—`, etc. Cosmetic but very visible.
    """
    body = _strip_protected(html)
    hits = list(_JSON_UNICODE_ESC.finditer(body))
    if not hits:
        return []
    sample = hits[0].group(0)
    return [AuditIssue(
        rel, "json_unicode_escape",
        f"{len(hits)} literal `\\uXXXX` JSON escape(s) (e.g. '{sample}') — "
        f"should decode to the actual Unicode character",
        "warn",
        line=_line_of(html, hits[0].start()),
    )]


_MATH_LT_LEAK = re.compile(r"\\\([^\\\)<>\n]*<\w[^\\\)<>\n]*\\\)")


_MATH_GT_LEAK = re.compile(r"\\\([^\\\)<>\n]*\w>[^\\\)<>\n]*\\\)")


_MATHBLOCK_LT_LEAK = re.compile(
    r"\\\[(?:[^\\]|\\(?!\]))*?<\w(?:[^\\]|\\(?!\]))*?\\\]"
)


def _check_math_html_leak(rel: str, html: str) -> list[AuditIssue]:
    """A literal `<` next to a letter inside `\\(…\\)` makes the HTML
    parser treat math as a tag, eating the next `</div>`. Surfaces as
    horizontal-flow layout + raw markdown leak after the affected block.
    """
    body = _strip_protected(html.split("<body", 1)[-1])
    hits = (
        list(_MATH_LT_LEAK.finditer(body))
        + list(_MATH_GT_LEAK.finditer(body))
        + list(_MATHBLOCK_LT_LEAK.finditer(body))
    )
    if not hits:
        return []
    sample = hits[0].group(0)[:50]
    return [AuditIssue(
        rel, "math_html_leak",
        f"{len(hits)} math span(s) contain raw `<`/`>` next to a letter "
        f"(e.g. `{sample}`) — HTML parser will eat the next closing tag",
        "error",
        line=_line_of(html, hits[0].start()),
    )]


def _check_displaymath_inside_p(rel: str, html: str) -> list[AuditIssue]:
    """Display math wrapped in <p> means the markdown parser treated `\\[…\\]`
    as inline. Auto-render still works, but typography (centering, vertical
    rhythm) is broken."""
    if _KATEX_DISPLAY_INSIDE_P.search(html):
        return [AuditIssue(rel, "displaymath_in_paragraph",
                           "display math (\\[…\\]) is wrapped in <p>; KaTeX will misrender alignment",
                           "warn")]
    return []


_CHECKS: list[Callable[[str, str], list[AuditIssue]]] = [
    _check_doctype_and_lang,
    _check_title_present,
    _check_katex_wired_when_math_present,
    _check_katex_delimiter_config,
    _check_math_balance,
    _check_double_escaped_math,
    _check_dollar_math,
    _check_empty_math_span,
    _check_html_entity_in_math,
    _check_mismatched_math_delim,
    _check_raw_latex_leak,
    _check_stray_dollar,
    _check_render_fallback,
    _check_block_error,
    _check_raw_fence_leak,
    _check_unclosed_code_fence,
    _check_llm_wrapper_fence,
    _check_markdown_leak,
    _check_raw_md_hr_leak,
    _check_raw_md_heading_leak,
    _check_broken_attrs,
    _check_math_html_leak,
    _check_double_escaped_entities,
    _check_json_unicode_escape,
    _check_anchors,
    _check_images,
    _check_inline_font_overrides,
    _check_library_block_density,
    _check_pre_holds_latex,
    _check_nbsp_soup,
    _check_deprecated_tags,
    _check_mojibake,
    _check_displaymath_inside_p,
]

