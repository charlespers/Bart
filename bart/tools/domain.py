"""Domain detection + shared rendering for reference tools.

Every domain tool (chem, cs, ece, ...) is a thin shell over a static
fact table. Zero LLM cost. The tool fires when its keyword set matches
the subject; users can also force-include via env var.

Why static facts and not generated content:
- They're CORRECT by construction (curated once, used forever).
- They're FREE (no tokens spent rederiving the periodic table each run).
- They're CONSISTENT across runs (no drift between two students of the
  same course).

The skeletal pattern: each tool defines `KEYWORDS` and `SECTIONS`. The
shared renderer walks SECTIONS into HTML using the packet's `ref-card`
block. Adding a new domain = adding one file with two module-level
constants.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable


@dataclass
class RefSection:
    """One section of a domain reference card.

    `title`: heading shown at the top of the card.
    `tag`: short label in the upper-right (e.g. "table", "formulas").
    `kind`: 'table' | 'formulas' | 'list' | 'html'.
    `data`:
      - for 'table': {"headers": [...], "rows": [[...], ...]}
      - for 'formulas': [{"name": ..., "formula": ..., "note": ...}]
      - for 'list': [{"term": ..., "explain": ...}]
      - for 'html': raw html string (escape upstream)
    """
    title: str
    tag: str
    kind: str
    data: object


def subject_matches(subject: str, keywords: Iterable[str]) -> bool:
    s = (subject or "").lower()
    return any(k in s for k in keywords)


def render_ref_page(subject: str, domain: str, sections: list[RefSection]) -> str:
    """Render a self-contained reference HTML page using the packet stylesheet.

    The page links to ../packet.css so it inherits the polished styles. It
    needs MathJax for formula rendering — the script tag follows the same
    local-bundle-with-CDN-fallback pattern as packet pages.
    """
    from ..render.assets import MATHJAX_CDN_URL

    body_parts: list[str] = [
        f'<h1>{escape(domain.upper())} reference card</h1>',
        '<div class="b-trap-callout b-trap-note" style="margin:18px 0">'
        '<span class="b-trap-callout-icon">i</span>'
        '<div class="b-trap-callout-body">'
        '<div class="b-trap-callout-title">Static reference</div>'
        '<div class="b-trap-callout-content">'
        f'This page is bart\'s built-in {escape(domain.upper())} reference — '
        'general facts curated once, identical for every user. It is NOT '
        f'derived from your uploaded {escape(subject)} corpus. Use it as a '
        'cheat-sheet alongside the corpus-grounded daily lessons.'
        '</div></div></div>',
    ]
    for sec in sections:
        body_parts.append(_render_section(sec))

    return f"""<!doctype html>
<html lang="en" data-theme="auto">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(subject)} — {escape(domain)} reference</title>
  <link rel="stylesheet" href="../packet.css">
  <link rel="stylesheet" href="../blocks.css">
  <script>
    window.MathJax = {{
      tex: {{ inlineMath: [['\\\\(','\\\\)']], displayMath: [['\\\\[','\\\\]']] }}
    }};
  </script>
  <script src="../lib/mathjax/tex-chtml.js" defer
    onerror="(function(){{var s=document.createElement('script');s.src='{MATHJAX_CDN_URL}';s.defer=true;document.head.appendChild(s);}})();"
  ></script>
</head>
<body>
  <div class="layout">
    <div></div>
    <div>
      <main class="content">
        <article>
          {''.join(body_parts)}
        </article>
      </main>
    </div>
  </div>
</body>
</html>
"""


def _render_section(sec: RefSection) -> str:
    header = (
        f'<div class="ref-card-header">'
        f'<h3>{escape(sec.title)}</h3>'
        f'<span class="ref-card-tag">{escape(sec.tag)}</span>'
        f'</div>'
    )
    body = _render_body(sec.kind, sec.data)
    return f'<div class="ref-card">{header}<div class="ref-card-body">{body}</div></div>'


def _render_body(kind: str, data: object) -> str:
    if kind == "table":
        return _render_table(data)
    if kind == "formulas":
        return _render_formulas(data)
    if kind == "list":
        return _render_list(data)
    if kind == "html":
        return str(data)
    return ""


def _render_table(data) -> str:
    if not isinstance(data, dict):
        return ""
    headers = data.get("headers") or []
    rows = data.get("rows") or []
    th = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(_render_cell(c) for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _render_cell(cell) -> str:
    """Allow dict cells with {"math": "..."} for raw LaTeX inline math."""
    if isinstance(cell, dict) and "math" in cell:
        return f"<td>\\({cell['math']}\\)</td>"
    if isinstance(cell, dict) and "code" in cell:
        return f"<td><code>{escape(str(cell['code']))}</code></td>"
    return f"<td>{escape(str(cell))}</td>"


def _render_formulas(data) -> str:
    if not isinstance(data, list):
        return ""
    parts: list[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = escape(str(entry.get("name", "")))
        formula = entry.get("formula", "")
        note = entry.get("note", "")
        parts.append(
            f'<div class="formula-card">'
            f'<span class="formula-label">{name}</span>'
            f'\\[{formula}\\]'
            + (f'<p style="margin:6px 0 0;font-size:14px;color:var(--fg-soft)">{escape(note)}</p>' if note else "")
            + "</div>"
        )
    return "".join(parts)


def _render_list(data) -> str:
    if not isinstance(data, list):
        return ""
    parts = ["<dl>"]
    for entry in data:
        if not isinstance(entry, dict):
            continue
        term = escape(str(entry.get("term", "")))
        explain = entry.get("explain", "")
        parts.append(f"<dt>{term}</dt><dd>{escape(str(explain))}</dd>")
    parts.append("</dl>")
    return "".join(parts)


def write_ref_page(packet_dir: Path, domain: str, subject: str, sections: list[RefSection]) -> Path:
    """Render and write a domain reference page. Returns the written path."""
    out_dir = packet_dir / "domain"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{domain}_reference.html"
    path.write_text(render_ref_page(subject, domain, sections), encoding="utf-8")
    return path
