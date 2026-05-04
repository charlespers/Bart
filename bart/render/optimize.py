"""Browser-load optimizations applied at packet build time.

Three wins:

1. Lazy-load MathJax — only inject the 1.2 MB script tag on pages that
   actually contain math. Pages with no equations skip it entirely.

2. Inline critical CSS — the small subset of styles needed for above-the-fold
   first paint is inlined in <head>; the full packet.css loads non-blocking
   via media swap.

3. Minify HTML — strip insignificant whitespace and comments. Saves ~25-35%
   on file size, which matters more for first-byte than total size on
   localhost but still meaningful when packets are emailed/archived.
"""
from __future__ import annotations

import re


# Critical CSS: the styles needed to make above-the-fold content render
# correctly without flash-of-unstyled. Kept tight — full styles load after.
CRITICAL_CSS = """
:root {
  --paper: #f4f1ea; --paper-hi: #fbf9f4; --paper-lo: #ebe6d8;
  --rule: #d9d3c4;
  --ink: #221f1b; --ink-soft: #3a342d; --ink-mute: #6d655a;
  --accent: #c96442; --accent-hi: #e88a6a; --accent-lo: #9a4628;
  --cream: var(--paper); --cream-hi: var(--paper-hi); --cream-lo: var(--rule);
  --border: var(--rule); --bg: var(--paper); --fg: var(--ink);
  --font-body: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-serif: "Source Serif 4", "Iowan Old Style", "Source Serif Pro", Georgia, serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --font-hand: "Caveat", "Bradley Hand", cursive;
}
@media (prefers-color-scheme: dark) {
  [data-theme="auto"] {
    --bg: #1a1815; --fg: #e6e1d6; --border: #322e29;
    --ink: #e6e1d6; --ink-soft: #c8c1b3; --paper-hi: rgba(255,255,255,0.025);
    --cream-hi: var(--paper-hi);
  }
}
[data-theme="dark"] {
  --bg: #1a1815; --fg: #e6e1d6; --border: #322e29;
  --ink: #e6e1d6; --ink-soft: #c8c1b3; --paper-hi: rgba(255,255,255,0.025);
  --cream-hi: var(--paper-hi);
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; background: var(--bg); color: var(--fg);
  font-family: var(--font-body); font-size: 21px; line-height: 1.75;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
}
.layout { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh; }
.sidebar {
  padding: 28px 22px 40px;
  border-right: 1px solid var(--border); background: var(--bg);
  position: sticky; top: 0; max-height: 100vh; overflow-y: auto;
}
.content { padding: 48px clamp(32px, 5vw, 96px) 96px; min-width: 0; }
.topbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 32px; border-bottom: 1px solid var(--border);
}
/* Body prose is set in serif so equations + prose share a typographic family,
   matching the design library's Source Serif 4 default. Headings stay sans
   for the editorial contrast pictured in library.html's PageHeader. */
article {
  max-width: 100%;
  margin: 0 auto;
  font-family: var(--font-serif);
  font-size: 21px;
  line-height: 1.75;
}
article p { max-width: 78ch; }
/* Display math may extend wider than the prose column without clipping —
   horizontal scroll only when the equation truly overflows the container. */
.katex-display { overflow-x: auto; overflow-y: hidden; padding: 4px 0; }
article p { margin: 0 0 1.4rem; text-wrap: pretty; }
article h1, article h2, article h3, article h4 {
  font-family: var(--font-body); font-weight: 700;
  letter-spacing: -0.015em; line-height: 1.25; color: var(--fg);
  scroll-margin-top: 80px;
}
article h1 { font-size: 44px; margin: 0 0 10px; letter-spacing: -0.028em; }
article h2 {
  font-size: 30px; margin: 60px 0 14px; padding-top: 30px;
  border-top: 1px solid var(--border);
}
article h2:first-of-type { border-top: 0; padding-top: 0; margin-top: 36px; }
article h3 { font-size: 23px; margin: 38px 0 10px; }
article h4 { font-size: 19px; margin: 26px 0 8px; color: var(--ink-soft); }
.hero { padding: 64px 0 48px; border-bottom: 1px solid var(--border); margin-bottom: 56px; }
.hero h1 { font-size: 60px; letter-spacing: -0.035em; }
.hero h1 .dot { color: var(--accent); }
/* Pre-load reservation for KaTeX-rendered display math: prevents the row
   collapsing to zero before the script runs, which would jump the page.
   Until KaTeX swaps in the rendered HTML, hide the raw `\[…\]` source so
   the reader never sees the garbled-looking pre-render flash. */
.katex-display { margin: 1.6em 0 !important; min-height: 2.4em; }
.arithmatex { color: transparent; }
html.katex-rendered .arithmatex { color: inherit; }
.katex .arithmatex, .katex-display .arithmatex { color: inherit; }
@media (max-width: 1100px) {
  article { font-size: 20px; }
}
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .content { padding: 28px 20px 80px; }
  article { font-size: 18px; }
}
"""


# Lazy-load technique for the full stylesheet:
#   media="print" onload swap to "all" — non-blocking, broadly supported.
LAZY_STYLESHEET = (
    '<link rel="preload" href="{href}" as="style">'
    '<link rel="stylesheet" href="{href}" media="print" onload="this.media=\'all\'">'
    '<noscript><link rel="stylesheet" href="{href}"></noscript>'
)


def has_math(html: str) -> bool:
    """Cheap detection of math content in rendered HTML.

    Two emission paths produce math: (1) python-markdown's arithmatex extension
    wraps `$…$` / `$$…$$` in <span class="arithmatex">, and (2) the design-library
    blocks (formula_card, worked_example, etc.) emit raw `\\(…\\)` / `\\[…\\]`
    LaTeX directly. We must load KaTeX whenever EITHER appears, otherwise the
    custom-block equations ship as unrendered source. `\\ce{` catches mhchem
    chemistry equations so they trigger the same loader.
    """
    if 'class="arithmatex"' in html:
        return True
    if "\\(" in html or "\\[" in html:
        return True
    if "\\ce{" in html or "\\pu{" in html:
        return True
    return False


def has_chem(html: str) -> bool:
    """True if the HTML contains mhchem-style chemistry expressions.

    Triggers loading the mhchem KaTeX plugin alongside the base bundle.
    `\\ce{…}` is chemical-equation notation; `\\pu{…}` is physical-unit
    notation — both are mhchem macros and will throw if the plugin is absent.
    """
    return "\\ce{" in html or "\\pu{" in html


_HTML_COMMENT_RE = re.compile(r"<!--(?!\[).*?-->", re.DOTALL)
_BETWEEN_TAGS_WS_RE = re.compile(r">\s+<")
_MULTISPACE_RE = re.compile(r"  +")


def minify_html(html: str) -> str:
    """Lightweight HTML minification — safe, no DOM parsing.

    - Remove comments (preserve conditional comments)
    - Collapse whitespace between tags
    - Collapse multiple spaces to one
    - Preserve content of <pre>, <code>, <textarea>, <script>, <style>

    Saves ~25-35% on typical bart pages.
    """
    # Carve out preserved blocks
    preserved: list[str] = []

    def _stash(m: re.Match) -> str:
        preserved.append(m.group(0))
        return f"\x00P{len(preserved) - 1}\x00"

    PRESERVE_RE = re.compile(
        r"<(pre|code|textarea|script|style)\b[^>]*>.*?</\1>",
        re.DOTALL | re.IGNORECASE,
    )
    out = PRESERVE_RE.sub(_stash, html)

    # Strip comments
    out = _HTML_COMMENT_RE.sub("", out)
    # Collapse whitespace between tags
    out = _BETWEEN_TAGS_WS_RE.sub("><", out)
    # Collapse runs of spaces (but keep single spaces)
    out = _MULTISPACE_RE.sub(" ", out)
    # Drop leading/trailing whitespace per line
    out = "\n".join(line.strip() for line in out.splitlines() if line.strip())

    # Restore preserved blocks
    def _unstash(m: re.Match) -> str:
        return preserved[int(m.group(1))]

    out = re.sub(r"\x00P(\d+)\x00", _unstash, out)
    return out
