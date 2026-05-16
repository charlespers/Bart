"""Page assembly — wraps rendered HTML body in the bart packet template.

Templates are simple Python f-strings with `${...}` style placeholders so
we don't need a templating engine. The HTML is otherwise hand-written and
audited.
"""
from __future__ import annotations

from html import escape as html_escape
from pathlib import Path
from typing import Iterable


# Math is rendered with KaTeX (per design spec: "real LaTeX rendering").
# KaTeX is significantly faster than MathJax and renders without a layout
# shift. We bundle it locally and CDN-fall-back at load time, identical
# to the MathJax pattern that came before.
_KATEX_AUTORENDER_CONFIG = """
(function () {
  // Polls for KaTeX to be ready, then runs auto-render across the body.
  // Polling is more robust than a single DOMContentLoaded listener because
  // we load katex/auto-render with `defer` AND fall back to the CDN via
  // <script onerror>; the CDN copy may arrive after DOMContentLoaded.
  var TRIES = 0, MAX = 200;
  function ready() {
    return window.katex && window.renderMathInElement;
  }
  function go() {
    try {
      window.renderMathInElement(document.body, {
        delimiters: [
          {left: '\\\\(', right: '\\\\)', display: false},
          {left: '\\\\[', right: '\\\\]', display: true},
          {left: '$$',     right: '$$',     display: true}
        ],
        // `pre`/`code`/`script` shouldn't be parsed as math. arithmatex
        // wraps math in <span class="arithmatex"> / <div class="arithmatex">
        // which auto-render walks INTO — that's intentional.
        ignoredTags: ['script','noscript','style','textarea','pre','code','tt'],
        ignoredClasses: ['no-katex'],
        throwOnError: false,
        errorColor: '#9a4628',
        strict: 'ignore',
        // 'html' is widest-supported (works on every browser); 'mathml' is
        // crisper on Safari but renders boxes on Chromium-without-fonts.
        // 'htmlAndMathml' is the safest default — KaTeX picks per-browser.
        output: 'htmlAndMathml',
        macros: {
          // Common shortcuts used by the design library + author prompts.
          '\\\\R': '\\\\mathbb{R}',
          '\\\\C': '\\\\mathbb{C}',
          '\\\\Z': '\\\\mathbb{Z}',
          '\\\\N': '\\\\mathbb{N}',
          '\\\\eps': '\\\\varepsilon',
          '\\\\half': '\\\\tfrac{1}{2}'
        }
      });
      // Reveal the arithmatex wrappers (we hide them in CSS to prevent the
      // raw `\[…\]` source from flashing before KaTeX swaps in the render).
      document.documentElement.classList.add('katex-rendered');
    } catch (e) {
      // never let a single bad equation crash the whole page render
      console && console.warn && console.warn('[bart] KaTeX render error:', e);
    }
  }
  function tick() {
    if (ready()) { go(); return; }
    if (++TRIES > MAX) return;
    setTimeout(tick, 25);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick);
  } else {
    tick();
  }
})();
"""

# Legacy MathJax constant kept for backward-compat in case external callers
# imported it; new pages use KaTeX.
_MATHJAX_CONFIG = """
window.MathJax = {
  tex: { inlineMath: [['\\\\(', '\\\\)']], displayMath: [['\\\\[', '\\\\]']] }
};
"""


_TOPBAR_TMPL = """\
<header class="topbar">
  <div class="topbar-crumbs">
    <a href="/app" title="back to bart">bart<span style="color:var(--accent)">.</span></a>
    <span class="sep">/</span>
    <span>{crumb_subject}</span>
    {extra_crumb}
  </div>
  <div class="topbar-actions">
    <a class="btn" href="{rel}/index.html" title="This packet's dashboard">Dashboard</a>
    <a class="btn" href="{rel}/download.zip" title="Download a copy of this packet as a zip" download>Download</a>
    <a class="btn btn-icon" href="{rel}/sandbox.html" id="sandbox-link" title="Sandbox — paste markdown, see live preview">⌗</a>
    <button class="btn btn-icon" id="search-btn" title="Search (⌘K)">⌕</button>
    <button class="btn btn-icon" id="theme-toggle" title="Theme">light</button>
  </div>
</header>
"""


_SIDEBAR_TMPL = """\
<aside class="sidebar">
  <a class="sidebar-brand" href="/app" title="back to bart">
    <img src="{rel}/assets/bart-loaf.svg" alt="bart" />
    <span class="wordmark">bart<span class="dot">.</span></span>
  </a>
  {nav_block}
</aside>
"""


_SEARCH_OVERLAY = """\
<div class="search-overlay" id="search-overlay">
  <div class="search-modal" role="dialog" aria-modal="true">
    <input type="text" class="search-input" id="search-input"
           placeholder="search the packet…" autocomplete="off" />
    <ul class="search-results" id="search-results"></ul>
  </div>
</div>
"""


_BASE_TMPL = """\
<!doctype html>
<html lang="en" data-theme="light" data-rel-root="{rel}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="generator" content="bart packet renderer">
  <link rel="icon" href="{rel}/assets/bart-loaf.svg">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,500;0,8..60,600;1,8..60,400&family=JetBrains+Mono:wght@400;500;600&family=Caveat:wght@500;600&display=swap" rel="stylesheet">
  <style>{critical_css}</style>
  {lazy_stylesheet}
  <link rel="preload" href="{rel}/blocks.css" as="style">
  <link rel="stylesheet" href="{rel}/blocks.css" media="print" onload="this.media='all'">
  <noscript><link rel="stylesheet" href="{rel}/blocks.css"></noscript>
  {math_block}
  <link rel="prefetch" href="{rel}/search-index.json" as="fetch" crossorigin>
</head>
<body>
  <div class="layout">
    {sidebar}
    <div>
      {topbar}
      <main class="content">
        <article>
          {body}
        </article>
        {pager}
      </main>
    </div>
  </div>
  {search_overlay}
  <script src="{rel}/packet.js" defer></script>
  <script src="{rel}/lib_blocks.js" defer></script>
</body>
</html>
"""


def _toc_to_html(toc: list[dict]) -> str:
    """Build the sidebar TOC from a flat list of {level, name, id}.

    Visualizes depth (h2 vs h3 vs h4) with progressive indentation + a
    left-rule that highlights the active section. Renders an `aria-current`
    attribute the JS can flip without disturbing the markup.
    """
    if not toc:
        return ""
    parts: list[str] = ['<nav class="toc-nav"><h3>on this page</h3><ul class="toc-list">']
    for entry in toc:
        level = entry.get("level", 2)
        # Only show levels 2-4 in the sidebar; deeper headings clutter
        if level < 2 or level > 4:
            continue
        cls = f"toc-l{level}"
        slug = html_escape(entry["id"])
        # python-markdown's TOC extension already HTML-escapes entry["name"]
        # (so a heading containing `&` arrives as `&amp;`). Re-escaping here
        # produces `&amp;amp;` — the double-escaped-entity bug surfaced by
        # `format --fix` every rerender. Use the name as-is.
        name = entry["name"]
        parts.append(f'<li class="{cls}"><a href="#{slug}">{name}</a></li>')
    parts.append("</ul></nav>")
    return "".join(parts)


# Sidebar nav icons — small inline SVGs picked by label keyword. Each is a
# 16×16 stroke-based icon so they inherit `currentColor` and stay legible
# in dark mode. Keep the set tiny and recognizable — the goal is wayfinding
# at a glance, not decoration.
_NAV_ICONS: dict[str, str] = {
    "index":          '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M2 3h12M2 8h12M2 13h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    "master_plan":    '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M3 2h7l3 3v9H3V2zM10 2v3h3" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linejoin="round"/></svg>',
    "schematics":     '<svg viewBox="0 0 16 16" class="nav-ico"><circle cx="4" cy="4" r="1.6" stroke="currentColor" fill="none" stroke-width="1.3"/><circle cx="12" cy="4" r="1.6" stroke="currentColor" fill="none" stroke-width="1.3"/><circle cx="8" cy="12" r="1.6" stroke="currentColor" fill="none" stroke-width="1.3"/><path d="M5 5l3 5M11 5l-3 5" stroke="currentColor" stroke-width="1.2"/></svg>',
    "whimsical":      '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M3 6c1-3 4-3 5-1s4 2 5-1M3 10c1 3 4 3 5 1s4-2 5 1" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>',
    "short_guide":    '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M3 3h10v10H3zM5 6h6M5 9h6M5 12h4" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "practice_exam":  '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M3 2h8l2 2v10H3V2z" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linejoin="round"/><path d="M5 7l2 2 4-4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "day":            '<svg viewBox="0 0 16 16" class="nav-ico"><circle cx="8" cy="8" r="3" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.5 3.5l1.5 1.5M11 11l1.5 1.5M3.5 12.5L5 11M11 5l1.5-1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>',
    "review":         '<svg viewBox="0 0 16 16" class="nav-ico"><path d="M2 8a6 6 0 1011-3.5M13 2v4h-4" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "mock":           '<svg viewBox="0 0 16 16" class="nav-ico"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M8 4v4l2.5 2.5" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>',
}


def _icon_for(entry: dict) -> str:
    """Pick an icon based on URL keyword. Returns empty string when none fits."""
    url = entry.get("url", "").lower()
    label = entry.get("label", "").lower()
    kind = entry.get("kind", "")
    if "index" in url or label in ("home", "start"):
        return _NAV_ICONS["index"]
    if "master_plan" in url:
        return _NAV_ICONS["master_plan"]
    if "schematic" in url:
        return _NAV_ICONS["schematics"]
    if "whim" in url:
        return _NAV_ICONS["whimsical"]
    if "short_study" in url or "short_guide" in url:
        return _NAV_ICONS["short_guide"]
    if "practice_exam" in url:
        return _NAV_ICONS["practice_exam"]
    if kind == "day":
        if "review" in label or "review" in url:
            return _NAV_ICONS["review"]
        if "mock" in label or "mock" in url:
            return _NAV_ICONS["mock"]
        return _NAV_ICONS["day"]
    return ""


def _packet_nav_html(packet_nav: list[dict], current_url: str) -> str:
    """Build the packet-level nav (visible on every page) from a list of
    {url, label, kind: 'top'|'day', day_num?}.

    Each entry gets an inline SVG icon picked by URL/label so users can
    tell artifact types apart at a glance.
    """
    if not packet_nav:
        return ""
    top = [n for n in packet_nav if n.get("kind") == "top"]
    days = [n for n in packet_nav if n.get("kind") == "day"]
    parts: list[str] = []
    if top:
        parts.append('<nav class="packet-nav"><h3>packet</h3><ul>')
        for n in top:
            active = " active" if n["url"] == current_url else ""
            icon = _icon_for(n)
            parts.append(
                f'<li><a class="{active}" href="{html_escape(n["url"])}">'
                f'{icon}<span>{html_escape(n["label"])}</span></a></li>'
            )
        parts.append("</ul></nav>")
    if days:
        parts.append('<nav class="packet-nav"><h3>daily lessons</h3><ul>')
        for n in days:
            active = " active" if n["url"] == current_url else ""
            icon = _icon_for(n)
            parts.append(
                f'<li><a class="{active}" href="{html_escape(n["url"])}">'
                f'{icon}<span>{html_escape(n["label"])}</span></a></li>'
            )
        parts.append("</ul></nav>")
    return "".join(parts)


def assemble_page(
    *,
    body: str,
    title: str,
    subject: str,
    rel_root: str,
    page_toc: list[dict],
    packet_nav: list[dict],
    current_url: str,
    extra_crumb: str = "",
    pager_html: str = "",
    needs_math: bool = False,
    needs_chem: bool = False,
) -> str:
    """Wrap the rendered body in the full template.

    `needs_math`: if False, skip injecting the KaTeX loader script. Saves
    ~280 KB of CSS+JS on math-free pages (notably the index and many whimsy
    pages).

    `needs_chem`: if True, additionally load the KaTeX mhchem extension so
    `\\ce{H2SO4}` and `\\pu{1.5 J}` render as proper chemical-equation /
    physical-unit notation. Needed only when the body actually uses these
    macros — adds ~30 KB.
    """
    from .optimize import CRITICAL_CSS, LAZY_STYLESHEET

    sidebar_inner = _packet_nav_html(packet_nav, current_url) + _toc_to_html(page_toc)
    sidebar = _SIDEBAR_TMPL.format(rel=rel_root, nav_block=sidebar_inner)

    topbar = _TOPBAR_TMPL.format(
        rel=rel_root,
        crumb_subject=html_escape(subject),
        extra_crumb=extra_crumb or "",
    )

    if needs_math:
        from .assets import (
            KATEX_CSS_CDN, KATEX_JS_CDN, KATEX_AUTORENDER_CDN,
            KATEX_MHCHEM_CDN,
        )
        # KaTeX: stylesheet + main JS + (optional mhchem) + auto-render extension.
        # Order is load-bearing: mhchem must register itself on `window.katex`
        # AFTER katex.min.js but BEFORE auto-render parses the document, or
        # `\ce{}` macros will fall through and stay as raw source.
        # Each <script> has an onerror that swaps to the CDN if the local
        # bundle is missing or was a fetch-failure stub.
        chem_script = (
            f'<script defer src="{rel_root}/lib/katex/mhchem.min.js" '
            f'onerror="(function(){{var s=document.createElement(\'script\');'
            f's.src=\'{KATEX_MHCHEM_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
        ) if needs_chem else ""
        math_block = (
            f'<link rel="stylesheet" href="{rel_root}/lib/katex/katex.min.css" '
            f'onerror="this.onerror=null;this.href=\'{KATEX_CSS_CDN}\';">'
            f'<script defer src="{rel_root}/lib/katex/katex.min.js" '
            f'onerror="(function(){{var s=document.createElement(\'script\');'
            f's.src=\'{KATEX_JS_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
            f'{chem_script}'
            f'<script defer src="{rel_root}/lib/katex/auto-render.min.js" '
            f'onerror="(function(){{var s=document.createElement(\'script\');'
            f's.src=\'{KATEX_AUTORENDER_CDN}\';s.defer=true;document.head.appendChild(s);}})();"></script>'
            f'<script defer>{_KATEX_AUTORENDER_CONFIG}</script>'
        )
    else:
        math_block = "<!-- no math on this page; KaTeX skipped -->"

    return _BASE_TMPL.format(
        title=html_escape(title),
        rel=rel_root,
        critical_css=CRITICAL_CSS,
        lazy_stylesheet=LAZY_STYLESHEET.format(href=f"{rel_root}/packet.css"),
        math_block=math_block,
        sidebar=sidebar,
        topbar=topbar,
        body=body,
        pager=pager_html,
        search_overlay=_SEARCH_OVERLAY,
    )


def assemble_pager(prev_url: str | None, prev_label: str | None,
                   next_url: str | None, next_label: str | None) -> str:
    """Build the prev/next pager for daily lessons."""
    if not prev_url and not next_url:
        return ""
    parts = ['<nav class="day-pager">']
    if prev_url:
        parts.append(
            f'<a class="prev" href="{html_escape(prev_url)}">'
            f'<span class="pager-label">← previous</span>'
            f'<span class="pager-title">{html_escape(prev_label or "")}</span></a>'
        )
    else:
        parts.append('<span></span>')
    if next_url:
        parts.append(
            f'<a class="next" href="{html_escape(next_url)}">'
            f'<span class="pager-label">next →</span>'
            f'<span class="pager-title">{html_escape(next_label or "")}</span></a>'
        )
    else:
        parts.append('<span></span>')
    parts.append('</nav>')
    return "".join(parts)
