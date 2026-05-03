"""Page assembly — wraps rendered HTML body in the bart packet template.

Templates are simple Python f-strings with `${...}` style placeholders so
we don't need a templating engine. The HTML is otherwise hand-written and
audited.
"""
from __future__ import annotations

from html import escape as html_escape
from pathlib import Path
from typing import Iterable


# MathJax 3 configuration. Crucial: only \(...\) and \[...\] are recognized,
# never $...$ or $$...$$. This eliminates the entire class of "MathJax ate
# my dollar sign" bugs.
_MATHJAX_CONFIG = """
window.MathJax = {
  tex: {
    inlineMath: [['\\\\(', '\\\\)']],
    displayMath: [['\\\\[', '\\\\]']],
    processEscapes: true,
    processEnvironments: true,
    packages: {'[+]': ['ams', 'noerrors', 'noundefined']}
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
    ignoreHtmlClass: 'no-mathjax'
  },
  startup: {
    typeset: true
  },
  loader: {
    load: ['[tex]/ams', '[tex]/noerrors', '[tex]/noundefined']
  }
};
"""


_TOPBAR_TMPL = """\
<header class="topbar">
  <div class="topbar-crumbs">
    <a href="{rel}/index.html">bart<span style="color:var(--accent)">.</span></a>
    <span class="sep">/</span>
    <span>{crumb_subject}</span>
    {extra_crumb}
  </div>
  <div class="topbar-actions">
    <button class="btn btn-icon" id="search-btn" title="Search (⌘K)">⌕</button>
    <button class="btn btn-icon" id="theme-toggle" title="Theme">auto</button>
  </div>
</header>
"""


_SIDEBAR_TMPL = """\
<aside class="sidebar">
  <a class="sidebar-brand" href="{rel}/index.html">
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
<html lang="en" data-theme="auto" data-rel-root="{rel}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="generator" content="bart packet renderer">
  <link rel="icon" href="{rel}/assets/bart-loaf.svg">
  <style>{critical_css}</style>
  {lazy_stylesheet}
  {mathjax_block}
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
</body>
</html>
"""


def _toc_to_html(toc: list[dict]) -> str:
    """Build the sidebar TOC from a flat list of {level, name, id}."""
    if not toc:
        return ""
    parts: list[str] = ['<nav><h3>on this page</h3><ul>']
    for entry in toc:
        level = entry.get("level", 2)
        # Only show levels 2-4 in the sidebar; deeper headings clutter
        if level < 2 or level > 4:
            continue
        cls = f"toc-l{level}"
        slug = html_escape(entry["id"])
        name = html_escape(entry["name"])
        parts.append(f'<li class="{cls}"><a href="#{slug}">{name}</a></li>')
    parts.append("</ul></nav>")
    return "".join(parts)


def _packet_nav_html(packet_nav: list[dict], current_url: str) -> str:
    """Build the packet-level nav (visible on every page) from a list of
    {url, label, kind: 'top'|'day', day_num?}."""
    if not packet_nav:
        return ""
    top = [n for n in packet_nav if n.get("kind") == "top"]
    days = [n for n in packet_nav if n.get("kind") == "day"]
    parts: list[str] = []
    if top:
        parts.append('<nav><h3>packet</h3><ul>')
        for n in top:
            active = " active" if n["url"] == current_url else ""
            parts.append(f'<li><a class="{active}" href="{html_escape(n["url"])}">{html_escape(n["label"])}</a></li>')
        parts.append("</ul></nav>")
    if days:
        parts.append('<nav><h3>daily lessons</h3><ul>')
        for n in days:
            active = " active" if n["url"] == current_url else ""
            parts.append(
                f'<li><a class="{active}" href="{html_escape(n["url"])}">'
                f'{html_escape(n["label"])}</a></li>'
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
) -> str:
    """Wrap the rendered body in the full template.

    `needs_math`: if False, skip injecting the MathJax loader script. Saves
    ~1.2 MB of blocking-ish JS on math-free pages (notably the index, the
    short study guide, and many whimsy pages).
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
        mathjax_block = (
            f"<script>{_MATHJAX_CONFIG}</script>"
            f'<script src="{rel_root}/lib/mathjax/tex-chtml.js" defer></script>'
        )
    else:
        mathjax_block = "<!-- no math on this page; MathJax skipped -->"

    return _BASE_TMPL.format(
        title=html_escape(title),
        rel=rel_root,
        critical_css=CRITICAL_CSS,
        lazy_stylesheet=LAZY_STYLESHEET.format(href=f"{rel_root}/packet.css"),
        mathjax_block=mathjax_block,
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
