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
  --cream: #f4f1ea; --cream-hi: #fbf9f4; --cream-lo: #d9d3c4;
  --ink: #221f1b; --ink-soft: #3a342d; --ink-mute: #6d655a;
  --accent: #c96442; --accent-hi: #e88a6a; --accent-lo: #9a4628;
  --border: var(--cream-lo); --bg: var(--cream); --fg: var(--ink);
  --font-body: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  [data-theme="auto"] {
    --bg: #1a1815; --fg: #e6e1d6; --border: #322e29;
    --ink: #e6e1d6; --ink-soft: #c8c1b3; --cream-hi: rgba(255,255,255,0.025);
  }
}
[data-theme="dark"] {
  --bg: #1a1815; --fg: #e6e1d6; --border: #322e29;
  --ink: #e6e1d6; --ink-soft: #c8c1b3; --cream-hi: rgba(255,255,255,0.025);
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; background: var(--bg); color: var(--fg);
  font-family: var(--font-body); font-size: 17px; line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}
.layout { display: grid; grid-template-columns: 280px minmax(0,1fr); min-height: 100vh; }
.sidebar { padding: 28px 22px; border-right: 1px solid var(--border); }
.content { padding: 48px 64px 96px; }
.topbar { display: flex; justify-content: space-between; padding: 12px 32px;
  border-bottom: 1px solid var(--border); }
article { max-width: 720px; margin: 0 auto; }
article h1 { font-size: 38px; font-weight: 700; letter-spacing: -0.025em; margin: 0 0 8px; }
article h2 { font-size: 26px; font-weight: 700; margin: 56px 0 12px; padding-top: 28px; border-top: 1px solid var(--border); }
article h2:first-of-type { border-top: 0; padding-top: 0; }
article p { margin: 0 0 1.4rem; }
.hero { padding: 72px 0 56px; border-bottom: 1px solid var(--border); margin-bottom: 56px; }
.hero h1 { font-size: 56px; }
.hero h1 .dot { color: var(--accent); }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .sidebar { display: none; } }
"""


# Lazy-load technique for the full stylesheet:
#   media="print" onload swap to "all" — non-blocking, broadly supported.
LAZY_STYLESHEET = (
    '<link rel="preload" href="{href}" as="style">'
    '<link rel="stylesheet" href="{href}" media="print" onload="this.media=\'all\'">'
    '<noscript><link rel="stylesheet" href="{href}"></noscript>'
)


def has_math(html: str) -> bool:
    """Cheap detection of math content in rendered HTML."""
    # arithmatex emits <span class="arithmatex"> for inline and <div class="arithmatex"> for block.
    return 'class="arithmatex"' in html


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
