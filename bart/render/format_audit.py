"""Post-render formatting audit + auto-repair.

Powers `./run format`. Walks every HTML file under `<run_dir>/` and runs a
battery of structural / typographic / accessibility checks. Each check has
two halves:

  * an *audit* — produces zero or more `AuditIssue`s with file path, line
    number when known, severity, and a one-line human summary;
  * an *autofix* — when the user passes `--fix`, applies a safe, idempotent
    string transform on the HTML and rewrites the file.

The audit is deliberately read-only by default. Autofixes are restricted to
transformations that cannot make a working page worse:
  - normalize raw `\\(`/`\\[` sequences that ended up double-escaped;
  - balance stray block-math delimiters at the next blank line;
  - inject `loading="lazy"` on offscreen images;
  - tighten `<p>` runs around standalone display-math `<div class="arithmatex">`
    so KaTeX gets standalone blocks (matches sanitize.py's invariant);
  - drop `style="font-size:..."` overrides authors injected that fight
    the global type scale.

If a check finds something the autofix can't safely repair, it stays as a
warning and the user must intervene by hand. The goal: a clean run yields
zero non-info issues across every page in the packet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.table import Table


# ─── Issue model ─────────────────────────────────────────────────


@dataclass
class AuditIssue:
    file: str          # path relative to run_dir
    kind: str          # short identifier — used to dedupe + group
    detail: str        # human-readable, one line
    severity: str = "warn"   # "info" | "warn" | "error"
    line: int | None = None  # best-effort line number where applicable
    fix_applied: bool = False


@dataclass
class AuditResult:
    files_scanned: int = 0
    issues: list[AuditIssue] = field(default_factory=list)
    fixes_applied: int = 0

    @property
    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.severity] = out.get(i.severity, 0) + 1
        return out

    @property
    def errors(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "warn"]


# ─── Helpers ─────────────────────────────────────────────────────


def _line_of(text: str, offset: int) -> int:
    """Best-effort 1-indexed line number for a string offset."""
    return text.count("\n", 0, offset) + 1


def _strip_protected(html: str) -> str:
    """Remove `<pre>`, `<code>`, `<script>`, `<style>` content for checks
    that should ignore those regions (math balance, dollar leak, etc.)."""
    html = re.sub(r"<pre\b[^>]*>.*?</pre>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<code\b[^>]*>.*?</code>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


_PROTECTED_RE = re.compile(
    r"<(pre|code|script|style)\b[^>]*>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)


def _apply_outside_protected(
    html: str, transform: Callable[[str], tuple[str, int]]
) -> tuple[str, int]:
    """Run `transform` on every span of HTML *outside* `<pre>`, `<code>`,
    `<script>`, `<style>` regions, and stitch the result back together.

    Critical for autofixes that pattern-match on backslashes / brackets /
    angle brackets — those characters are load-bearing inside `<script>`
    (KaTeX delimiter config, mhchem loader heuristics) and inside `<pre>`
    (literal source examples). Touching them outside-the-tag-boundary was
    the source of the infamous "format --fix re-introduces the rendering
    bug it just fixed" regression.

    Returns the rebuilt HTML and a count of fixes applied across all
    non-protected spans.
    """
    out: list[str] = []
    cursor = 0
    total = 0
    for m in _PROTECTED_RE.finditer(html):
        chunk = html[cursor : m.start()]
        new, n = transform(chunk)
        out.append(new)
        total += n
        out.append(m.group(0))  # protected region untouched
        cursor = m.end()
    if cursor < len(html):
        new, n = transform(html[cursor:])
        out.append(new)
        total += n
    return "".join(out), total


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


# Match `\\(`, `\\)`, `\\[`, `\\]` — but NOT `\\[6pt]` / `\\[1em]` / etc.,
# which is the LaTeX `\\` (linebreak) followed by an optional vertical-space
# argument `[Npt]`. Collapsing those would smash `\\[6pt]\text{1 order}` into
# `\[6pt]\text{1 order}`, and KaTeX then treats the `\[` as a brand-new
# display-math opener — splitting the equation onto multiple visual lines and
# leaving stray `\[6pt]` source in the page.
_DOUBLE_ESCAPED = re.compile(
    r"\\\\(\(|\)|\[(?!\d+\s*(?:pt|em|in|mm|cm|ex|sp|pc|bp|dd|cc)\b)|\])"
)


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


_RENDER_FALLBACK = re.compile(r'<pre class=[\'"]render-fallback[\'"]>')


def _check_render_fallback(rel: str, html: str) -> list[AuditIssue]:
    if _RENDER_FALLBACK.search(html):
        return [AuditIssue(rel, "render_fallback_present",
                           "page contains a <pre class='render-fallback'> — markdown failed to render",
                           "error")]
    return []


_BART_BLOCK_ERROR = re.compile(r'<div class="bart-block-error"', re.IGNORECASE)


def _check_block_error(rel: str, html: str) -> list[AuditIssue]:
    hits = list(_BART_BLOCK_ERROR.finditer(html))
    if not hits:
        return []
    return [AuditIssue(rel, "block_error_present",
                       f"{len(hits)} library block(s) failed to render (visible yellow error stub)",
                       "error",
                       line=_line_of(html, hits[0].start()))]


_RAW_FENCE_LEAK = re.compile(r"```bart-[a-z\-]+", re.IGNORECASE)


def _check_raw_fence_leak(rel: str, html: str) -> list[AuditIssue]:
    """`` ```bart-foo `` should always be expanded by block_expand. If we see
    one in the final HTML, the expander missed it."""
    if _RAW_FENCE_LEAK.search(html):
        return [AuditIssue(rel, "raw_bart_fence",
                           "raw ```bart-* code fence reached final HTML (block_expand missed it)",
                           "error")]
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


_IMG_TAG = re.compile(r"<img\b([^>]*)>", re.IGNORECASE)


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


# ─── Streaming-corruption detectors ───────────────────────────────
# When the agent's output is truncated mid-stream (subscription mode hiccup,
# transient network blip, or a `bart-*` fence that ate its closing ```), three
# fingerprints appear in the final HTML:
#   1. A naked `## 3. Heading` line surfaces as text inside the `<article>`.
#   2. A naked `---` line surfaces as a horizontal-rule string instead of <hr>.
#   3. Tag attributes get character-dropped: `class="…"` → `cls="…"`,
#      `class="b-…"` → `clab-…"`, `<button class="…"` → `<button clan …"`.
# We detect each in the final HTML; the only safe autofix is to re-render the
# page from its sibling markdown source.

# A line that is JUST whitespace + `---` / `***` / `___` (markdown HR token).
_RAW_MD_HR_LINE = re.compile(r"(?:^|>|\n)\s*(?:-{3,}|_{3,}|\*{3,})\s*\n")
# Lines like `## 3. Foo` or `### 2.1 Bar` floating in HTML body where a real
# heading would be wrapped in <h2>/<h3>. We require the "## " token to be
# adjacent to a tag-close `>` (or newline) so we don't false-positive on
# inline ` ## ` strings that actually live inside prose.
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


# Tag attributes whose value never closes (`class="b-…"\n` instead of
# `class="b-…">`), or whose name was character-dropped (`cls=`, `clas=`,
# `clan `, `claas=`). We pattern-match the most-frequent garblings rather
# than try to be exhaustive — a single hit is enough to flag the page.
_BROKEN_CLASS_ATTR = re.compile(
    r'<\w+\s+(?:cls|clas|claas|clan|clab|classs|cass)\b[^>]*>',
    re.IGNORECASE,
)
# Empty / orphan opening tags (`<>`, `<<div>`, `</>`).
_EMPTY_TAG = re.compile(r"<\s*>|<<\w|</\s*>")

# Inside an opening tag, a `\w` token followed directly by `"` and no `=` —
# the `=` got dropped, leaving `<div c-formula-card-title">` (the attribute
# value's leading char is gone) or `<div class-node">`. We require the `"`
# to immediately follow a non-`=` word char.
_ORPHAN_QUOTE_IN_TAG = re.compile(r'<\w+\s+[A-Za-z][\w\-]*[A-Za-z0-9]"')
# A tag where an attribute name starts with a non-letter char that's typical
# of a character-drop (`<div -node">` — the `class=` got eaten leaving the
# value with a leading hyphen).
_LEADING_HYPHEN_ATTR = re.compile(r'<\w+\s+-\w[\w\-]*"')
# An equals sign attached to the tag NAME instead of an attribute name —
# `<style="b-ribbon-beats">` should be `<div class="b-ribbon-beats">` but
# the leading tag word + `class` got conflated. Match a known void / non-
# attribute-bearing element name immediately followed by `=`.
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


_DOUBLE_ENTITY = re.compile(r"&amp;(?:amp|lt|gt|quot|apos|nbsp|#\d+);")


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


# Re-render-from-source autofix. When a page has a streaming-corruption
# fingerprint, the only safe repair is to rebuild it from the matching
# markdown file in the same run directory. The format command is the only
# place that knows the run_dir, so we attach the re-render closure dynamically
# in `audit()` (one closure per page that has a sibling .md).
_CORRUPTION_KINDS = frozenset({
    "raw_md_hr_leak",
    "raw_md_heading_leak",
    "broken_class_attr",
    "empty_tag",
})


def _markdown_sibling_for(html_path: Path, run_dir: Path) -> Path | None:
    """Return the markdown file whose render produced this HTML page, or None.

    bart's renderer pairs `01_schematics.html` with `01_SCHEMATICS.md`,
    `lessons/day_03.html` with `daily_lessons/Day_03_<date>.md`, etc. We try
    a few naming conventions and accept the first match we can verify.
    """
    name = html_path.stem
    candidates: list[Path] = []
    # Top-level artifacts (uppercase + same stem).
    candidates.append(run_dir / f"{name.upper()}.md")
    candidates.append(run_dir / f"{name}.md")
    # Daily lessons: lessons/day_03.html → daily_lessons/Day_03_*.md
    if html_path.parent.name == "lessons" and name.lower().startswith("day_"):
        day_num = name.split("_", 1)[1]
        for p in (run_dir / "daily_lessons").glob(f"Day_{day_num}_*.md"):
            candidates.append(p)
    for p in candidates:
        if p.exists():
            return p
    return None


def _check_displaymath_inside_p(rel: str, html: str) -> list[AuditIssue]:
    """Display math wrapped in <p> means the markdown parser treated `\\[…\\]`
    as inline. Auto-render still works, but typography (centering, vertical
    rhythm) is broken."""
    if _KATEX_DISPLAY_INSIDE_P.search(html):
        return [AuditIssue(rel, "displaymath_in_paragraph",
                           "display math (\\[…\\]) is wrapped in <p>; KaTeX will misrender alignment",
                           "warn")]
    return []


# ─── Autofixes ───────────────────────────────────────────────────


def _fix_double_escaped_math(html: str) -> tuple[str, int]:
    """Collapse `\\\\(`, `\\\\[`, etc. emitted by an over-eager JSON pipeline
    back to the single-backslash form KaTeX expects.

    Skips `<script>`, `<style>`, `<pre>`, `<code>` regions: the JS auto-render
    config legitimately contains `'\\\\('` so JS string evaluation produces the
    literal `\\(` delimiter — collapsing that here would silently downgrade
    the delimiters to plain `(`/`)`/`[`/`]` and KaTeX would start eating
    parenthetical prose as inline math.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = 0
        def _repl(m: re.Match) -> str:
            nonlocal n
            n += 1
            return "\\" + m.group(1)
        return _DOUBLE_ESCAPED.sub(_repl, chunk), n
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


# Plain-prose math tokens that should be inline math:
#   k_1, k_2, k_{-1}, K_M, t_{1/2}        — subscripted rate / equilibrium constants
#   σ_{2p}, π_{2p}, σ*_{2s}                — Greek-base subscripts in MO discussions
#   [A], [B], [I], [ES], [E]_total, [E]_T — bracketed concentrations (with optional underscore tail)
#   d[X]/dt                               — time derivative shorthand
#
# Conservative on purpose — only triggers on tokens that read as math, not on
# words like "C_program" or sentence-bracketed annotations like "[hint]". The
# `[A-Za-zα-ωΑ-Ωσπμλεαβγδθω]` flank guards prevent matching the middle of a
# regular word like "key_value".
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
_MATH_SPAN_RE = re.compile(r"(\\\(.+?\\\)|\\\[.+?\\\])", re.DOTALL)


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
    """Collapse `\\\\chi`, `\\\\circ`, etc. inside math spans only.

    Same reasoning as the markdown-side fix: an over-eager JSON pipeline can
    leave commands at JSON-escape level (literal `\\\\chi`) which KaTeX
    misreads as "linebreak then chi" — usually surfacing as
    `Got function '\\\\' with no arguments as superscript`. Restricting the
    transform to math regions keeps real "\\\\" sequences in prose (Windows
    paths, hand-drawn linebreaks in `<pre>`) untouched.
    """
    def _on(chunk: str) -> tuple[str, int]:
        n = [0]
        def _scrub_span(m: re.Match) -> str:
            inner = m.group(1)
            new = _DOUBLE_BS_LATEX_CMD.sub(lambda mm: "\\" + mm.group(1), inner)
            if new != inner:
                n[0] += 1
            return new
        new_chunk = _MATH_SPAN_RE.sub(_scrub_span, chunk)
        return new_chunk, n[0]
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


# ─── Driver ──────────────────────────────────────────────────────


_CHECKS: list[Callable[[str, str], list[AuditIssue]]] = [
    _check_doctype_and_lang,
    _check_title_present,
    _check_katex_wired_when_math_present,
    _check_katex_delimiter_config,
    _check_math_balance,
    _check_double_escaped_math,
    _check_stray_dollar,
    _check_render_fallback,
    _check_block_error,
    _check_raw_fence_leak,
    _check_markdown_leak,
    _check_raw_md_hr_leak,
    _check_raw_md_heading_leak,
    _check_broken_attrs,
    _check_double_escaped_entities,
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


_FIXES: list[tuple[str, Callable[[str], tuple[str, int]]]] = [
    ("double_escaped_math",          _fix_double_escaped_math),
    ("double_escaped_latex_command", _fix_double_escaped_latex_commands),
    ("double_superscript",           _fix_double_superscript),
    ("math_unbalanced_block",        _fix_unbalanced_block_math),
    ("prose_math_wrap",              _fix_prose_math_wrap),
    # Re-inject the KaTeX bundle if a previous fix introduced new math markers.
    ("katex_loader_injected",        _ensure_katex_loaded),
    ("inline_font_size_override",    _fix_inline_font_overrides),
    ("displaymath_in_paragraph",     _fix_displaymath_inside_p),
    ("double_escaped_entity",        _fix_double_escaped_entities),
    ("img_missing_lazy_load",        _fix_lazy_load_images),
]


def _iter_html_files(run_dir: Path) -> list[Path]:
    """Every HTML page under run_dir, sorted for determinism."""
    return sorted(p for p in run_dir.rglob("*.html") if p.is_file())


def audit(run_dir: Path, *, apply_fixes: bool = False) -> AuditResult:
    """Run all checks; optionally apply autofixes in place.

    Two-stage autofix:

      1. **String-level fixes** — collapse double-escaped math, balance stray
         `\\[`, drop inline `font-size:` overrides, lazy-load offscreen
         images. Cheap and idempotent.
      2. **Structural rebuild** — when *any* page exhibits a streaming-
         corruption fingerprint (raw `---` / `## H`, broken `class=` attrs,
         empty tags), invoke `build_packet` once for the whole run. The
         markdown source on disk is the source of truth; this is the only
         fix that can synthesize the structure that streaming dropped.
         Costs no API tokens (no agent calls).

    The two-stage order matters: stage 1 might silence cosmetic issues
    that would otherwise mask the corruption signal. We re-detect after
    stage 1 to decide whether stage 2 is needed.
    """
    result = AuditResult()
    file_paths = _iter_html_files(run_dir)

    # ── Stage 1: per-file string fixes + initial detection ────────
    file_html: dict[Path, str] = {}
    file_original: dict[Path, str] = {}
    needs_rerender = False
    rerender_kinds_total = 0

    for path in file_paths:
        rel = str(path.relative_to(run_dir))
        try:
            html = path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            result.issues.append(AuditIssue(rel, "read_error", str(e), "error"))
            continue
        result.files_scanned += 1
        file_original[path] = html

        if apply_fixes:
            for kind, fix in _FIXES:
                html, n = fix(html)
                if n:
                    result.fixes_applied += n
                    result.issues.append(AuditIssue(
                        rel, f"fixed:{kind}", f"applied {n} repair(s)",
                        "info", fix_applied=True,
                    ))

        # Detect corruption fingerprints to decide re-render eligibility.
        corruption_hits: list[AuditIssue] = []
        for check in (_check_raw_md_hr_leak, _check_raw_md_heading_leak,
                      _check_broken_attrs):
            try:
                corruption_hits.extend(check(rel, html))
            except Exception:
                pass
        if corruption_hits:
            needs_rerender = True
            rerender_kinds_total += len(corruption_hits)

        file_html[path] = html

    # ── Stage 2: full-packet rebuild when corruption was detected ──
    if apply_fixes and needs_rerender:
        rebuilt = _rebuild_packet_from_markdown(run_dir)
        if rebuilt:
            # Re-read every file post-rebuild so the regular check pass
            # below sees the freshly-rendered HTML, not the corrupted copy.
            for path in file_paths:
                try:
                    file_html[path] = path.read_text(encoding="utf-8")
                except Exception:
                    pass
            result.fixes_applied += rerender_kinds_total
            result.issues.append(AuditIssue(
                "<run>", "fixed:packet_rerendered_from_markdown",
                f"detected {rerender_kinds_total} streaming-corruption "
                f"fingerprint(s); re-rendered the packet from sibling "
                f"markdown sources (no API cost)",
                "info", fix_applied=True,
            ))

    # ── Stage 3: regular check pass over (possibly rebuilt) HTML ──
    for path in file_paths:
        if path not in file_html:
            continue
        rel = str(path.relative_to(run_dir))
        html = file_html[path]
        for check in _CHECKS:
            try:
                result.issues.extend(check(rel, html))
            except Exception as e:  # noqa: BLE001
                result.issues.append(AuditIssue(
                    rel, "check_crash", f"{check.__name__}: {e}", "warn",
                ))
        if apply_fixes and html != file_original.get(path, html):
            try:
                path.write_text(html, encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                result.issues.append(AuditIssue(rel, "write_error", str(e), "error"))

    return result


def _rebuild_packet_from_markdown(run_dir: Path) -> bool:
    """Trigger a full packet rebuild from sibling markdown — no API cost.

    Mirrors what `./run render` does, but invoked inline from `format --fix`
    when a streaming-corruption fingerprint was detected. Returns True iff
    the rebuild ran without raising. We swallow exceptions to keep the
    audit driver going — the next pass of checks will still report any
    issue the rebuild didn't actually fix.
    """
    try:
        import json as _json
        from .packet import build_packet
        manifest_path = run_dir / "manifest.json"
        manifest = (
            _json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        build_packet(run_dir, manifest)
        return True
    except Exception:
        return False


# ─── Reporting ───────────────────────────────────────────────────


def render_report(console: Console, result: AuditResult, run_dir: Path) -> None:
    if not result.issues and not result.files_scanned:
        console.print("[red]✗[/red] no HTML files in run.")
        return

    by_sev = result.by_severity
    summary_bits = []
    for sev, color in (("error", "red"), ("warn", "yellow"), ("info", "dim")):
        if by_sev.get(sev):
            summary_bits.append(f"[{color}]{by_sev[sev]} {sev}[/{color}]")
    summary = "  ".join(summary_bits) if summary_bits else "[green]clean[/green]"

    console.print()
    console.print(
        f"[bold #c96442]▸ format[/bold #c96442]  {result.files_scanned} file(s)  ·  {summary}"
    )
    if result.fixes_applied:
        console.print(f"  [green]✓[/green] applied {result.fixes_applied} auto-repair(s)")

    actionable = [i for i in result.issues if i.severity != "info" or i.fix_applied]
    if not actionable:
        console.print("  [green]✓[/green] every page passes every check")
        return

    # Group by file for a compact tree-style print.
    by_file: dict[str, list[AuditIssue]] = {}
    for i in actionable:
        by_file.setdefault(i.file, []).append(i)

    for fname in sorted(by_file):
        items = by_file[fname]
        console.print(f"\n  [bold]{fname}[/bold]")
        for i in items:
            color = {"error": "red", "warn": "yellow", "info": "dim"}.get(i.severity, "white")
            tag = i.kind if not i.fix_applied else f"fixed:{i.kind.split(':')[-1]}"
            line = f":{i.line}" if i.line else ""
            console.print(f"    [{color}]•[/{color}] [{color}]{tag}[/{color}]{line}  {i.detail}")

    # Persistent JSON report
    out_path = run_dir / "format_audit.json"
    payload = {
        "run_id": run_dir.name,
        "files_scanned": result.files_scanned,
        "fixes_applied": result.fixes_applied,
        "by_severity": by_sev,
        "issues": [
            {
                "file": i.file, "kind": i.kind, "detail": i.detail,
                "severity": i.severity, "line": i.line, "fix_applied": i.fix_applied,
            }
            for i in result.issues
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(f"\n  [dim]full report:[/dim] {out_path}")
