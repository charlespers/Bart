"""Packet builder — orchestrates the markdown -> HTML packet build.

Public API: `build_packet(run_dir, manifest)`.

Pipeline (per artifact):
    raw .md -> sanitize -> markdown render -> page assemble -> validate -> write .html

Top-level outputs:
    index.html              landing page with day grid + artifact cards
    00_master_plan.html     etc.
    lessons/day_NN.html     daily lessons
    markdown/               originals preserved for portability/Obsidian
    packet.css / packet.js  / lib/mathjax/ / assets/
    search-index.json       used by the search modal
    render_warnings.json    accumulated warnings from sanitize+render+validate

The build is deterministic: same inputs -> same outputs.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from html import escape as html_escape
from pathlib import Path
from typing import List

from .assets import install_all
from .blocks import (
    ArtifactCardBlock,
    ArtifactGridBlock,
    DayCardBlock,
    DayGridBlock,
    HeroBlock,
    KpiGridBlock,
    PagerBlock,
)
from .compose import Page
from .block_expand import expand_blocks
from .markdown import RenderWarning, render
from .optimize import has_chem, has_math, minify_html
from .page import assemble_page
from .sanitize import SanitizeWarning, normalize_md


@dataclass
class Warning:
    file: str
    kind: str
    detail: str
    severity: str = "warn"  # "info" | "warn" | "error"


# Kinds that are informational only (successful normalization, not a problem).
_INFO_KINDS = {
    "math_delim_converted",
    "stray_preamble",
    "stray_coda",
    "stripped_emoji",
}


# ─────────────────────────────────────────────────────────────────
# Artifact discovery — what's in the run dir?
# ─────────────────────────────────────────────────────────────────

# Top-level artifacts in the order they appear on the index page.
_TOP_ARTIFACTS = [
    {"file": "00_MASTER_PLAN.md",       "html": "00_master_plan.html",
     "label": "Master Plan",            "blurb": "Topic allocation and pacing strategy."},
    {"file": "01_SCHEMATICS.md",        "html": "01_schematics.html",
     "label": "Schematics",             "blurb": "Diagrams, formula tables, common traps."},
    {"file": "02_WHIMSICAL_NOTES.md",   "html": "02_whimsical_notes.html",
     "label": "Whimsical Notes",        "blurb": "Mnemonics and analogies for sticky recall."},
    {"file": "03_SHORT_STUDY_GUIDE.md", "html": "03_short_guide.html",
     "label": "Short Study Guide",      "blurb": "The 60-minute panoramic version."},
    {"file": "04_PRACTICE_EXAM.md",     "html": "04_practice_exam.html",
     "label": "Practice Exam",          "blurb": "Full mock exam with answer key."},
]


_DAY_FILE_RE = re.compile(r"^Day_(\d+)_(\d{4}-\d{2}-\d{2})\.md$")


def _discover_days(run_dir: Path) -> list[dict]:
    daily_dir = run_dir / "daily_lessons"
    if not daily_dir.exists():
        return []
    out = []
    for p in sorted(daily_dir.glob("Day_*.md")):
        m = _DAY_FILE_RE.match(p.name)
        if not m:
            continue
        out.append({
            "src": p,
            "day_num": int(m.group(1)),
            "date": m.group(2),
            "html_name": f"day_{int(m.group(1)):02d}.html",
        })
    return out


# ─────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────

def _validate_html(html: str, file_label: str) -> list[Warning]:
    """Runs lightweight invariants on the produced HTML."""
    warnings: list[Warning] = []
    # 1. Parses as HTML (BeautifulSoup is forgiving but flags unrecoverable structures)
    try:
        from bs4 import BeautifulSoup
        BeautifulSoup(html, "html.parser")
    except Exception as e:  # noqa: BLE001
        warnings.append(Warning(file_label, "html_parse", f"{type(e).__name__}: {e}"))

    # 2. Internal anchor resolution
    ids = set(re.findall(r'\sid="([^"]+)"', html))
    for href in re.findall(r'href="#([^"]+)"', html):
        if href not in ids:
            warnings.append(Warning(file_label, "broken_anchor", f"#{href} has no target"))

    # 3. Stray $ outside <code> (would have been MathJax-eaten if MathJax saw it).
    # Strip <code> and <pre> regions before scanning.
    stripped = re.sub(r'<pre[\s\S]*?</pre>', '', html)
    stripped = re.sub(r'<code[\s\S]*?</code>', '', stripped)
    if "$" in stripped:
        # Count to check if it's odd (asymmetric — likely typo) vs even (probably literal cash).
        n = stripped.count("$")
        if n > 4:
            warnings.append(Warning(file_label, "stray_dollar", f"{n} literal '$' chars in prose (math may not have converted)"))

    # 4. Markdown link syntax leaking through.
    if re.search(r'\]\([^)]+\)', stripped):
        warnings.append(Warning(file_label, "markdown_leak", "[…](…) markdown link syntax appears unconverted"))

    return warnings


# ─────────────────────────────────────────────────────────────────
# Helpers — landing page composition
# ─────────────────────────────────────────────────────────────────

def _build_index_page(
    subject: str,
    generated_at: str,
    exam_date: str,
    present_top: list[dict],
    days: list[dict],
    topics_by_day: dict[int, str],
    days_focuses: dict[int, str],
    missing_day_nums: list[int] | None = None,
    run_id: str = "",
) -> str:
    """Compose the landing page from structural blocks."""
    page = Page()
    page.add(HeroBlock(
        subject=subject,
        generated_at=generated_at,
        exam_date=exam_date,
    ))

    # KPI strip — quick glance at the packet shape.
    kpis = []
    if days:
        kpis.append({"label": "Daily lessons", "value": str(len(days))})
    if present_top:
        kpis.append({"label": "Top-level artifacts", "value": str(len(present_top))})
    kpis.append({"label": "Exam date", "value": exam_date or "—"})
    if kpis:
        page.add(KpiGridBlock(kpis=kpis))

    # Top-level artifact cards
    if present_top:
        artifact_cards = [
            ArtifactCardBlock(title=a["label"], blurb=a["blurb"], href=a["html"])
            for a in present_top
        ]
        page.add(ArtifactGridBlock(cards=artifact_cards))

    # Day grid
    if days:
        day_cards = [
            DayCardBlock(
                day_num=d["day_num"],
                topic=topics_by_day.get(d["day_num"], ""),
                date=d["date"],
                focus=days_focuses.get(d["day_num"], "learn"),
                href=f"lessons/{d['html_name']}",
            )
            for d in days
        ]
        page.add(DayGridBlock(cards=day_cards))

    # Visible recovery banner when the planner asked for more days than the
    # orchestrator wrote — gives the reader a one-line action instead of
    # silently shipping a holey packet.
    if missing_day_nums:
        nums = ", ".join(str(n) for n in missing_day_nums)
        cmd = f"./run --resume {run_id}" if run_id else "./run --resume <run_id>"
        from .blocks.base import ProseBlock
        page.add(ProseBlock(html=(
            '<section class="missing-days-banner">'
            '<div class="missing-days-banner-icon">⚠</div>'
            '<div class="missing-days-banner-body">'
            f'<div class="missing-days-banner-title">{len(missing_day_nums)} day(s) did not generate</div>'
            f'<div class="missing-days-banner-text">'
            f'Missing day numbers: <strong>{nums}</strong>. '
            f'The planner expected them but the orchestrator did not write the file '
            f'(usually a transient API error in a parallel worker). '
            f'Recover with <code>{cmd}</code> — completed days are skipped, only '
            f'the missing ones are regenerated.'
            f'</div></div></section>'
        )))

    return page.render()


# ─────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────

def build_packet(run_dir: Path, manifest: dict) -> List[Warning]:
    """Render every markdown artifact in `run_dir` into a polished HTML packet.

    Idempotent: safe to re-run. Returns the accumulated list of warnings.
    """
    warnings: List[Warning] = []

    cfg = manifest.get("config", {}) or {}
    subject = cfg.get("subject", "Study Packet")
    exam_date = cfg.get("exam_date", "")
    generated_at = manifest.get("generated_at", "")

    # ── Move .md originals into markdown/ subdirectory (preserve, don't delete)
    md_dir = run_dir / "markdown"
    md_dir.mkdir(exist_ok=True)
    for art in _TOP_ARTIFACTS:
        src = run_dir / art["file"]
        if src.exists():
            shutil.copy2(src, md_dir / art["file"])
    daily_md_dir = md_dir / "daily_lessons"
    daily_md_dir.mkdir(exist_ok=True)
    for p in (run_dir / "daily_lessons").glob("Day_*.md") if (run_dir / "daily_lessons").exists() else []:
        shutil.copy2(p, daily_md_dir / p.name)

    # ── Install bundled assets
    install_all(run_dir)

    # ── Discover days + extract topic labels from manifest
    days = _discover_days(run_dir)
    topics_by_day = {d.get("day"): d.get("topic", "") for d in manifest.get("daily_lessons", [])}

    # ── Surface dropped days. The manifest is the source of truth for what
    # the planner intended to produce; the disk only has what the orchestrator
    # actually wrote. If they diverge, the run lost days (transient API
    # failure, rate-limit, OOM in a parallel worker) and the packet should
    # tell the reader exactly which days are missing instead of silently
    # shipping a holey table of contents. The manifest day numbers come
    # from the planner; disk numbers come from `_discover_days`.
    expected_day_nums = sorted({
        d.get("day") for d in manifest.get("daily_lessons", []) if d.get("day") is not None
    })
    on_disk_day_nums = {d["day_num"] for d in days}
    missing_day_nums = [n for n in expected_day_nums if n not in on_disk_day_nums]
    if missing_day_nums:
        detail = (
            f"manifest expected {len(expected_day_nums)} day(s) but disk has "
            f"{len(on_disk_day_nums)}. Missing: {missing_day_nums}. "
            f"Recover with: ./run --resume {run_dir.name}"
        )
        warnings.append(Warning("daily_lessons", "missing_days", detail))

    # ── Build the cross-page nav (sidebar "packet" section)
    packet_nav: list[dict] = []
    present_top: list[dict] = []
    for art in _TOP_ARTIFACTS:
        if (run_dir / art["file"]).exists():
            present_top.append(art)
            packet_nav.append({
                "kind": "top",
                "label": art["label"],
                "url": "{rel}/" + art["html"],
            })
    for d in days:
        topic = topics_by_day.get(d["day_num"], f"Day {d['day_num']}")
        label = f"Day {d['day_num']:02d}" + (f" · {topic}" if topic else "")
        packet_nav.append({
            "kind": "day",
            "label": label,
            "url": "{rel}/lessons/" + d["html_name"],
            "day_num": d["day_num"],
        })

    # ── Render top-level artifacts
    search_index: list[dict] = []

    for art in present_top:
        src = run_dir / art["file"]
        out_html = run_dir / art["html"]
        warnings.extend(_render_one(
            src=src,
            out_html=out_html,
            rel_root=".",
            title=f"{art['label']} · {subject}",
            subject=subject,
            packet_nav=_resolve_nav(packet_nav, "."),
            current_url=art["html"],
            search_index=search_index,
            search_source=art["label"],
            search_url=art["html"],
        ))

    # ── Render daily lessons
    lessons_dir = run_dir / "lessons"
    lessons_dir.mkdir(exist_ok=True)

    for i, d in enumerate(days):
        prev = days[i - 1] if i > 0 else None
        nxt = days[i + 1] if i < len(days) - 1 else None
        prev_url = f"{prev['html_name']}" if prev else None
        prev_label = (
            f"Day {prev['day_num']:02d} · {topics_by_day.get(prev['day_num'], '')}".strip(" ·")
            if prev else None
        )
        next_url = f"{nxt['html_name']}" if nxt else None
        next_label = (
            f"Day {nxt['day_num']:02d} · {topics_by_day.get(nxt['day_num'], '')}".strip(" ·")
            if nxt else None
        )
        pager = PagerBlock(
            prev_url=prev_url, prev_label=prev_label,
            next_url=next_url, next_label=next_label,
        ).render()

        topic = topics_by_day.get(d["day_num"], "")
        title_short = f"Day {d['day_num']:02d}" + (f" · {topic}" if topic else "")

        warnings.extend(_render_one(
            src=d["src"],
            out_html=lessons_dir / d["html_name"],
            rel_root="..",
            title=f"{title_short} · {subject}",
            subject=subject,
            packet_nav=_resolve_nav(packet_nav, ".."),
            current_url=f"lessons/{d['html_name']}",
            search_index=search_index,
            search_source=title_short,
            search_url=f"lessons/{d['html_name']}",
            extra_crumb=f'<span class="sep">/</span><span>Day {d["day_num"]:02d}</span>',
            pager_html=pager,
        ))

    # ── Build index.html using composable blocks
    days_focuses = {d.get("day"): d.get("focus", "learn") for d in manifest.get("daily_lessons", [])}
    body = _build_index_page(
        subject=subject,
        generated_at=generated_at,
        exam_date=exam_date,
        present_top=present_top,
        days=days,
        topics_by_day=topics_by_day,
        days_focuses=days_focuses,
        missing_day_nums=missing_day_nums,
        run_id=run_dir.name,
    )
    index_html = assemble_page(
        body=body,
        title=f"{subject} · bart packet",
        subject=subject,
        rel_root=".",
        page_toc=[],
        packet_nav=_resolve_nav(packet_nav, "."),
        current_url="index.html",
        needs_math=False,  # landing page never has math
    )
    warnings.extend([Warning("index.html", w.kind, w.detail) for w in _validate_html(index_html, "index.html")])
    (run_dir / "index.html").write_text(minify_html(index_html), encoding="utf-8")

    # ── Search index — write compact (no indent), no whitespace = smaller
    # transfer + faster parse. Anchors and titles are stripped of trailing
    # whitespace at construction.
    (run_dir / "search-index.json").write_text(
        json.dumps(search_index, separators=(",", ":")), encoding="utf-8"
    )

    # ── Warning manifest
    if warnings:
        (run_dir / "render_warnings.json").write_text(
            json.dumps([asdict(w) for w in warnings], indent=2), encoding="utf-8"
        )
    else:
        # Remove any stale warnings file from a previous run
        wf = run_dir / "render_warnings.json"
        if wf.exists():
            wf.unlink()

    return warnings


# ─────────────────────────────────────────────────────────────────
# Per-file render
# ─────────────────────────────────────────────────────────────────

def _resolve_nav(packet_nav: list[dict], rel_root: str) -> list[dict]:
    """Replace the {rel} placeholder in nav URLs with the actual rel path."""
    return [
        {**n, "url": n["url"].format(rel=rel_root)}
        for n in packet_nav
    ]


def _render_one(
    *,
    src: Path,
    out_html: Path,
    rel_root: str,
    title: str,
    subject: str,
    packet_nav: list[dict],
    current_url: str,
    search_index: list[dict],
    search_source: str,
    search_url: str,
    extra_crumb: str = "",
    pager_html: str = "",
) -> list[Warning]:
    warnings: list[Warning] = []
    raw = src.read_text(encoding="utf-8")

    sanitized, sanitize_warns = normalize_md(raw)
    for w in sanitize_warns:
        sev = "info" if w.kind in _INFO_KINDS else "warn"
        warnings.append(Warning(src.name, w.kind, w.detail, sev))

    # Expand bart-* fences into design-library HTML BEFORE markdown render.
    # This is what turns the agent's structured output (formula cards,
    # quick-checks, timelines, etc.) into the polished components.
    expanded = expand_blocks(sanitized)
    sanitized = expanded.text
    for w in expanded.warnings:
        warnings.append(Warning(src.name, w.kind, w.detail, "warn"))

    # Re-run the heading/HR padding now that bart fences have been replaced
    # with raw HTML. The pre-expansion pass can only see `\`\`\`bart-*` fences
    # as code-segments; once expand_blocks() has emitted `<figure>…</figure>`
    # blobs, a `---` or `## 3. Foo` line that the author wrote flush against
    # the next block is suddenly adjacent to a closing `</div>` in raw HTML
    # — exactly the fingerprint that makes python-markdown bundle them as a
    # single raw-HTML region and never convert the heading. This second pass
    # inserts the blank line that breaks that adjacency.
    from .sanitize import (
        _pad_blocks_around_headings_and_rules,
        _escape_html_in_math,
    )
    sanitized, pad_warns = _pad_blocks_around_headings_and_rules(sanitized)
    for w in pad_warns:
        warnings.append(Warning(src.name, w.kind, w.detail, "info"))

    # Same reasoning for the math-HTML-escape: when the author writes
    # `\(T_1<T_2\)` inside a `bart-trap-callout` JSON `body`, the pre-
    # expansion sanitize pass treats the fence as code and skips it. Once
    # expand_blocks() has unwrapped the JSON and inlined the body into raw
    # HTML, the literal `<T_2\)` is in prose and the HTML parser will eat
    # the next closing tag. Re-running the escape here catches those.
    sanitized, esc_warns = _escape_html_in_math(sanitized)
    for w in esc_warns:
        warnings.append(Warning(src.name, w.kind, w.detail, "info"))

    # ── Format guardrail (pre-render) — auto-repair common issues that
    # would otherwise make the page look crammed or render math wrong.
    from .format_check import check as _format_check
    sanitized, fmt_pre_warns = _format_check(sanitized)
    for w in fmt_pre_warns:
        warnings.append(Warning(src.name, w.kind, w.detail, w.severity))

    body_html, render_warns, meta = render(sanitized)
    for w in render_warns:
        sev = "info" if w.kind in _INFO_KINDS else "warn"
        warnings.append(Warning(src.name, w.kind, w.detail, sev))

    # Fold the cheap, idempotent format-audit autofixes INTO the render pipeline.
    # Without this, every `--rerender` cycle produces HTML with the same
    # patterns (double-escaped entities from the markdown extension, prose
    # math tokens like `[A]` / `k_1` that the author didn't wrap, inline
    # `font-size:` overrides), and `--fix` repeatedly reports the same
    # "applied N repair(s)" because the next rerender re-creates them.
    # Running the fixes here means rerendered HTML is already clean.
    from .format_audit import (
        _fix_math_html_leak,
        _fix_double_escaped_math,
        _fix_double_escaped_latex_commands,
        _fix_double_superscript,
        _fix_unbalanced_block_math,
        _fix_prose_math_wrap,
        _ensure_katex_loaded,
        _fix_inline_font_overrides,
        _fix_displaymath_inside_p,
        _fix_double_escaped_entities,
        _fix_lazy_load_images,
    )
    body_html, _ = _fix_math_html_leak(body_html)
    body_html, _ = _fix_double_escaped_math(body_html)
    body_html, _ = _fix_double_escaped_latex_commands(body_html)
    body_html, _ = _fix_double_superscript(body_html)
    body_html, _ = _fix_unbalanced_block_math(body_html)
    body_html, _ = _fix_prose_math_wrap(body_html)
    body_html, _ = _fix_inline_font_overrides(body_html)
    body_html, _ = _fix_displaymath_inside_p(body_html)
    body_html, _ = _fix_double_escaped_entities(body_html)

    # ── Format guardrail (post-render) — inspect rendered HTML for
    # box-internal sloppiness (bare formula-cards, empty worked-examples,
    # etc.) that only becomes apparent after expansion.
    _, fmt_post_warns = _format_check(sanitized, body_html)
    seen_kinds = {w.kind for w in fmt_pre_warns}
    for w in fmt_post_warns:
        # Don't double-report kinds the pre-pass already flagged.
        if w.kind in seen_kinds:
            continue
        warnings.append(Warning(src.name, w.kind, w.detail, w.severity))

    # Add to search index — one entry per heading
    for h in meta.get("headings", []):
        if h["level"] > 4:
            continue
        search_index.append({
            "title": h["title"],
            "anchor": h["slug"],
            "url": search_url,
            "source": search_source,
            "level": h["level"],
        })
    # Plus a top-level entry for the doc itself
    search_index.append({
        "title": search_source,
        "anchor": "",
        "url": search_url,
        "source": "page",
        "level": 1,
    })

    needs_math = has_math(body_html)
    needs_chem = has_chem(body_html)
    full_html = assemble_page(
        body=body_html,
        title=title,
        subject=subject,
        rel_root=rel_root,
        page_toc=meta.get("toc", []),
        packet_nav=packet_nav,
        current_url=current_url,
        extra_crumb=extra_crumb,
        pager_html=pager_html,
        needs_math=needs_math,
        needs_chem=needs_chem,
    )

    warnings.extend([Warning(src.name, w.kind, w.detail) for w in _validate_html(full_html, src.name)])

    # Minify before writing — lossless, ~25-35% size reduction.
    minified = minify_html(full_html)
    out_html.write_text(minified, encoding="utf-8")
    return warnings
