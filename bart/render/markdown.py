"""Markdown -> HTML rendering with strict extension config.

We use python-markdown with a curated set of pymdown-extensions. The
output is HTML5, with stable heading slugs (used as anchor targets in
the TOC and search index).

The renderer never raises on bad input — instead, it accumulates warnings
in a list returned alongside the HTML.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

import markdown
from markdown.extensions.toc import TocExtension


@dataclass
class RenderWarning:
    kind: str
    detail: str
    file: str = ""


# Extensions configuration — locked to specific options for predictability.
_EXTENSIONS = [
    "tables",
    "fenced_code",
    "attr_list",
    "def_list",
    "footnotes",
    "sane_lists",
    "smarty",
    TocExtension(permalink=False, toc_depth="2-4", slugify=lambda value, sep: _slugify(value, sep)),
    "pymdownx.superfences",
    "pymdownx.tasklist",
    "pymdownx.tilde",         # ~~strikethrough~~
    "pymdownx.caret",         # ^^underline^^
    "pymdownx.mark",          # ==highlight==
    "pymdownx.arithmatex",    # math: $$, $, \(, \[
]


_EXTENSION_CONFIGS = {
    "pymdownx.tasklist": {"custom_checkbox": True},
    "pymdownx.arithmatex": {
        # Generic mode wraps recognized math in <span class="arithmatex">…</span>
        # so the original delimiters are preserved for MathJax. We enable BOTH
        # backslash delimiters and dollar fallbacks so anything the agent
        # emits gets caught (sanitizer normalizes to backslash form first).
        "generic": True,
        "preview": False,
        "block_syntax":  ["dollar", "square"],   # $$…$$ and \[…\]
        "inline_syntax": ["dollar", "round"],    # $…$  and \(…\)
        "smart_dollar": True,
    },
    "pymdownx.superfences": {
        "css_class": "code-block",
    },
}


_SLUG_NON_ALNUM = re.compile(r"[^\w\s-]")
_SLUG_DASH = re.compile(r"[-\s]+")


def _slugify(value: str, sep: str) -> str:
    """Stable, lowercase, ascii-only-ish slugs."""
    value = value.strip().lower()
    value = _SLUG_NON_ALNUM.sub("", value)
    value = _SLUG_DASH.sub(sep, value)
    return value or "section"


def _new_md() -> markdown.Markdown:
    return markdown.Markdown(
        extensions=_EXTENSIONS,
        extension_configs=_EXTENSION_CONFIGS,
        output_format="html5",
        tab_length=4,
    )


def render(md_text: str) -> tuple[str, list[RenderWarning], dict]:
    """Render markdown to HTML body. Returns (html, warnings, meta).

    `meta` includes:
      - 'toc': list of {level, title, slug} entries
      - 'headings': flat list for search index
    """
    warnings: list[RenderWarning] = []
    md = _new_md()
    try:
        html = md.convert(md_text)
    except Exception as e:  # noqa: BLE001
        warnings.append(RenderWarning("markdown_exception", f"{type(e).__name__}: {e}"))
        # Three escalating recovery passes. Each strips more extensions until
        # one converts. The arithmatex tokenizer (math spans), smarty
        # (typography), and tilde/caret/mark (~~/^^/==) are the most common
        # throw sources on agent-generated content. The bare-bones pass keeps
        # only headings, lists, code, tables, fenced_code — enough to recover
        # 99% of pages.
        recovery_strategies = [
            ("no-arithmatex",
             [ext for ext in _EXTENSIONS
              if not (isinstance(ext, str) and "arithmatex" in ext)]),
            ("no-pymdownx",
             [ext for ext in _EXTENSIONS
              if not (isinstance(ext, str) and ext.startswith("pymdownx"))]),
            ("bare-bones",
             ["tables", "fenced_code", "attr_list", "sane_lists",
              TocExtension(permalink=False, toc_depth="2-4",
                           slugify=lambda v, sep: _slugify(v, sep))]),
        ]
        for label, ext_list in recovery_strategies:
            try:
                md_safe = markdown.Markdown(
                    extensions=ext_list,
                    extension_configs={
                        k: v for k, v in _EXTENSION_CONFIGS.items()
                        if not any(k.startswith(skip) for skip in ("pymdownx",))
                        or any(isinstance(e, str) and e.startswith(k) for e in ext_list)
                    },
                    output_format="html5",
                    tab_length=4,
                )
                html = md_safe.convert(md_text)
                warnings.append(RenderWarning(
                    "markdown_recovered",
                    f"rendered with `{label}` extension set after primary failure",
                ))
                toc_tokens = getattr(md_safe, "toc_tokens", []) or []
                toc_flat = _flatten_toc(toc_tokens)
                headings = [{"title": t["name"], "slug": t["id"], "level": t["level"]}
                            for t in toc_flat]
                return html, warnings, {"toc": toc_flat, "headings": headings}
            except Exception as e2:  # noqa: BLE001
                warnings.append(RenderWarning(
                    "markdown_recovery_attempt_failed",
                    f"{label}: {type(e2).__name__}: {e2}",
                ))
                continue
        # Final fallback: escape the raw markdown so the page at least loads.
        # We surface it as a visible error block (not a quietly-styled <pre>)
        # so the user knows the page didn't render properly.
        from html import escape as html_escape
        html = (
            f'<div class="render-fallback-error" '
            f'style="border:2px solid var(--accent,#c96442);'
            f'background:var(--warn-tint,#fdf3e3);padding:18px;border-radius:10px;'
            f'margin:24px 0;font-family:var(--font-body,system-ui),sans-serif">'
            f'<strong>This page failed to render.</strong> '
            f'The markdown source is shown below — likely a malformed library '
            f'block fence or unbalanced delimiter. Run <code>./run fix-patch '
            f'&lt;run_id&gt;</code> to attempt automatic recovery.'
            f'</div>'
            f'<pre class="render-fallback" '
            f'style="white-space:pre-wrap;word-break:break-word;background:'
            f'var(--paper-hi,#fbf9f4);padding:16px;border-radius:8px;'
            f'font-size:12px">{html_escape(md_text)}</pre>'
        )
        return html, warnings, {"toc": [], "headings": []}

    # TOC lookup — markdown extension stores the parsed TOC tokens
    toc_tokens = getattr(md, "toc_tokens", []) or []
    toc_flat = _flatten_toc(toc_tokens)

    # Headings for search index
    headings = [{"title": t["name"], "slug": t["id"], "level": t["level"]} for t in toc_flat]

    meta = {"toc": toc_flat, "headings": headings}

    # Post-render checks (non-fatal)
    warnings.extend(_check_anchors(html, headings))
    return html, warnings, meta


def _flatten_toc(tokens: list[dict], level: int | None = None) -> list[dict]:
    """Flatten the nested TOC tokens into a flat list with explicit levels."""
    out: list[dict] = []
    for t in tokens:
        out.append({"level": t["level"], "name": t["name"], "id": t["id"]})
        if t.get("children"):
            out.extend(_flatten_toc(t["children"]))
    return out


_HREF_HASH_RE = re.compile(r'href="#([^"]+)"')
_ID_RE = re.compile(r'\sid="([^"]+)"')


def _check_anchors(html: str, headings: list[dict]) -> list[RenderWarning]:
    """Verify every internal href="#x" resolves to an existing id."""
    warnings: list[RenderWarning] = []
    ids = {h["slug"] for h in headings} | set(_ID_RE.findall(html))
    for href in _HREF_HASH_RE.findall(html):
        if href not in ids:
            warnings.append(RenderWarning("broken_anchor", f"href=#{href} has no matching id"))
    return warnings
