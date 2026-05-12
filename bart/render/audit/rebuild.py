"""The format-audit driver + the streaming-corruption rebuild.

``audit(run_dir, *, apply_fixes=False) -> AuditResult`` is the public entry
point (powers ``./run format``): per-file string fixes → corruption-fingerprint
detection → optional full-packet rebuild from the sibling markdown sources (no
API cost) → a regular check pass over the (possibly rebuilt) HTML.
``render_report`` prints the result and writes ``format_audit.json``.

Internal to ``bart.render.audit``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from rich.console import Console

from ._shared import (
    AuditIssue,
    AuditResult,
)
from .checks import (
    _CHECKS,
    _check_raw_md_hr_leak,
    _check_raw_md_heading_leak,
    _check_broken_attrs,
    _check_render_fallback,
    _check_unclosed_code_fence,
    _check_llm_wrapper_fence,
)
from .fixes import _FIXES


# ─── Driver ──────────────────────────────────────────────────────


# Defense-in-depth tripwire: the autofix pass is the *last* line of defense, not
# the first. Source-level block/math validation (validate_blocks, make_math_html_safe)
# should be catching malformed output at generation time; a clean run should need
# only a handful of post-hoc repairs. If the autofix pass ever applies more than
# this many fixes, that's a regression in the source-level validation — flag it
# loudly (a `warn` `autofix_overrun` entry in format_audit.json) rather than
# papering over it.
AUTOFIX_WARN_THRESHOLD = 20


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

    # ── Stage 0: backfill sandbox + ensure topbar link ────────────
    # Older runs may pre-date the sandbox feature. The autofix drops
    # sandbox.html / sandbox.js into the packet so every existing run
    # gets the live-preview feature on next `format --fix`.
    if apply_fixes:
        try:
            n = _ensure_sandbox_present(run_dir)
            if n:
                result.fixes_applied += n
                result.issues.append(AuditIssue(
                    "<run>", "fixed:sandbox_installed",
                    f"backfilled sandbox.html + sandbox.js ({n} new file(s)) — "
                    f"open `<run>/sandbox.html` to paste-and-preview markdown",
                    "info", fix_applied=True,
                ))
        except Exception as e:  # noqa: BLE001
            result.issues.append(AuditIssue(
                "<run>", "sandbox_install_error", str(e), "warn",
            ))

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
        # `_check_math_html_leak` doesn't go in this set because it has a
        # fast string-only autofix above (`_fix_math_html_leak`); rebuild
        # is only needed when string fixes can't synthesize structure.
        #
        # `_check_unclosed_code_fence` only triggers the rebuild when at
        # least one source `.md` actually has an odd fence count — the same
        # HTML symptom can come from library-block JSON leaking raw ```,
        # which a source-side fence-close repair won't fix and rebuilding
        # would be wasteful.
        corruption_hits: list[AuditIssue] = []
        for check in (_check_raw_md_hr_leak, _check_raw_md_heading_leak,
                      _check_broken_attrs, _check_render_fallback):
            try:
                corruption_hits.extend(check(rel, html))
            except Exception:
                pass
        try:
            fence_hits = _check_unclosed_code_fence(rel, html)
        except Exception:
            fence_hits = []
        if fence_hits and _count_odd_fence_sources(run_dir):
            corruption_hits.extend(fence_hits)
        try:
            wrapper_hits = _check_llm_wrapper_fence(rel, html)
        except Exception:
            wrapper_hits = []
        if wrapper_hits and _count_llm_wrapper_sources(run_dir):
            corruption_hits.extend(wrapper_hits)
        if corruption_hits:
            needs_rerender = True
            rerender_kinds_total += len(corruption_hits)

        file_html[path] = html

    # Trailing-meta residue: stray solo ``` lines outside any bart-* pair
    # are LLM meta-commentary the wrapper-strip pass left behind. The HTML
    # symptom (literal text wrapped in a Pygments code-block at the end of
    # `<article>`) is masked by `_strip_protected` upstream, so we flag
    # the source-side condition directly. Source-only triggers like this
    # are gated on `apply_fixes` since the rebuild itself runs the
    # truncation — a passive audit shouldn't fire a rebuild trigger.
    if apply_fixes:
        stray = _count_stray_fence_sources(run_dir)
        if stray:
            needs_rerender = True
            rerender_kinds_total += stray
            result.issues.append(AuditIssue(
                "<run>", "trailing_meta_residue",
                f"{stray} source `.md` file(s) carry stray solo ``` line(s) "
                f"outside any bart-* pair — LLM trailing meta-commentary "
                f"after a stripped wrapper; truncating + re-rendering",
                "error",
            ))

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
    # If rebuild ran in stage 2, the post-rebuild HTML may still have
    # patterns that the cheap string fixes can repair (e.g. a `\(t<0\)`
    # that the lib_blocks helpers emitted before our post-expansion
    # escape pass landed). Apply the fix list one more time on top of
    # the rebuilt HTML so the on-disk file is fully repaired.
    for path in file_paths:
        if path not in file_html:
            continue
        rel = str(path.relative_to(run_dir))
        html = file_html[path]

        if apply_fixes and needs_rerender:
            for kind, fix in _FIXES:
                html, n = fix(html)
                if n:
                    result.fixes_applied += n
                    result.issues.append(AuditIssue(
                        rel, f"fixed:{kind}", f"applied {n} repair(s) (post-rebuild)",
                        "info", fix_applied=True,
                    ))

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

    # ── Autofix-overrun tripwire ──────────────────────────────────
    # The autofix pass is defense-in-depth — a clean run should need only a few
    # repairs (source-level block/math validation catches the rest at generation
    # time). An unusual number of repairs means that validation has a gap; flag
    # it loudly so the regression is visible rather than silently absorbed.
    if apply_fixes and result.fixes_applied > AUTOFIX_WARN_THRESHOLD:
        result.issues.append(AuditIssue(
            "<run>", "autofix_overrun",
            f"autofix applied {result.fixes_applied} repairs "
            f"(threshold {AUTOFIX_WARN_THRESHOLD}) — the source-level block/math "
            f"validation has a gap; check render_warnings.json",
            "warn",
        ))

    return result


def _ensure_sandbox_present(run_dir: Path) -> int:
    """Drop the sandbox.html / sandbox.js pair into the packet if missing.

    The sandbox is a self-contained client-side markdown preview page —
    paste any markdown, see it rendered through the bart library-block +
    KaTeX pipeline. Older runs (generated before sandbox shipped) won't
    have it; this autofix backfills them so every packet exposes the
    feature without requiring a full re-render. Returns the count of
    files actually copied.
    """
    from ..assets import copy_template_assets
    n = 0
    for fname in ("sandbox.html", "sandbox.js"):
        if not (run_dir / fname).exists():
            n += 1
    if n:
        copy_template_assets(run_dir)
    return n


_FENCE_LINE_RE = re.compile(r"^```", re.MULTILINE)


_WRAPPER_FENCE_OPEN_RE = re.compile(r"^```(?:markdown|md)?\s*$", re.IGNORECASE)


def _detect_llm_wrapper_fence(text: str) -> int | None:
    """Return the line index (0-based) of an LLM-wrapper fence opener,
    or None if the source doesn't start with one.

    Smoking gun: the first non-blank line is ` ```(markdown|md)? ` and
    the next non-blank line is a markdown heading (`#`). Real documents
    don't open with a code block whose first content is a heading.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    if not _WRAPPER_FENCE_OPEN_RE.match(lines[i]):
        return None
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return None
    if not lines[j].lstrip().startswith("#"):
        return None
    return i


_BART_FENCE_OPEN_RE = re.compile(r"^```bart-", re.MULTILINE)


_SOLO_FENCE_RE = re.compile(r"^```\s*$")


_LANG_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z]")


def _truncate_trailing_meta(text: str) -> tuple[str, int]:
    """After stripping an LLM wrapper opener, the wrapper's "intended"
    close shows up as a stray solo ``` line followed by meta-commentary
    the LLM kept generating ("Practice exam Part A drafted: …"). Drop
    everything from that stray fence onward.

    We only truncate when every language-tagged fence in the doc is a
    `bart-*` opener — otherwise a legit ``` ```python …``` ``` example
    in prose would get destroyed. Returns (new_text, lines_dropped).
    """
    lines = text.splitlines(keepends=True)
    # Bail if there's a non-bart language-tagged fence anywhere: that's
    # almost always a real code example we don't want to chop through.
    for line in lines:
        if _LANG_FENCE_OPEN_RE.match(line) and not _BART_FENCE_OPEN_RE.match(line):
            return text, 0

    inside_bart = False
    for i, line in enumerate(lines):
        if _BART_FENCE_OPEN_RE.match(line):
            inside_bart = True
            continue
        if _SOLO_FENCE_RE.match(line):
            if inside_bart:
                inside_bart = False
                continue
            # Stray solo fence outside any bart-* pair — the wrapper's
            # intended close. Everything past this is trailing meta.
            return "".join(lines[:i]), len(lines) - i
    return text, 0


def _strip_llm_wrapper_fence_in_sources(run_dir: Path) -> int:
    """Repair LLM-emitted wrapper artifacts in live `.md` sources.

    Two independent repairs, applied per file:

      1. **Strip the leading ``` ```markdown ``` wrapper.** When the first
         non-blank line is a ``` `(markdown|md)?` `` fence followed by a
         heading, that's a wrapper opener around the entire document.
         Removing it lets python-markdown render the head as real
         headings instead of one giant Pygments-highlighted code block.

      2. **Truncate trailing meta after the wrapper's close.** The LLM
         often keeps generating "Practice exam Part A drafted: …" prose
         after closing the wrapper, leaving stray solo ``` lines outside
         any `bart-*` pair. We drop everything from the first such stray
         fence to EOF — but only when the file looks like a structured
         packet artifact (≥ 1 `bart-*` pair) and contains NO non-bart
         language-tagged fences (so a legit ``` ```python …``` ``` example
         in prose is never destroyed).

    Either repair may apply to a file independently. A previous --fix
    run may have stripped the wrapper without truncating the meta, so
    the truncation pass re-checks every source regardless of whether
    we just stripped a wrapper. Returns the count of files patched.
    """
    candidates: list[Path] = list(_live_md_sources(run_dir))
    archive = run_dir / "markdown"
    if archive.exists():
        candidates.extend(archive.rglob("*.md"))
    n = 0
    for md_path in candidates:
        try:
            original = md_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        text = original
        idx = _detect_llm_wrapper_fence(text)
        if idx is not None:
            lines = text.splitlines(keepends=True)
            del lines[idx]
            text = "".join(lines)
        if _looks_like_bart_artifact(text):
            text, _ = _truncate_trailing_meta(text)
        if text == original:
            continue
        try:
            md_path.write_text(text, encoding="utf-8")
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _looks_like_bart_artifact(text: str) -> bool:
    """A source counts as a structured packet artifact when it contains
    at least one ``` ```bart-* ``` opener. The trailing-meta truncation
    only runs on these — freeform prose with bare ``` fences gets left
    alone to avoid destroying legit code examples."""
    return bool(_BART_FENCE_OPEN_RE.search(text))


def _count_llm_wrapper_sources(run_dir: Path) -> int:
    """How many live-source `.md` files carry an LLM-wrapper fence."""
    return sum(
        1 for p in _live_md_sources(run_dir)
        if _detect_llm_wrapper_fence(_safe_read(p)) is not None
    )


def _safe_read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _live_md_sources(run_dir: Path) -> list[Path]:
    """The `.md` paths the renderer actually consumes (top-level + daily
    lessons), excluding the `markdown/` archive that gets overwritten on
    each rebuild."""
    out: list[Path] = list(run_dir.glob("*.md"))
    daily = run_dir / "daily_lessons"
    if daily.exists():
        out.extend(daily.glob("*.md"))
    return out


def _count_odd_fence_sources(run_dir: Path) -> int:
    """Count live-source `.md` files with an odd number of `^```` lines —
    i.e. an unclosed code fence the rebuild can repair."""
    n = 0
    for md_path in _live_md_sources(run_dir):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        if len(_FENCE_LINE_RE.findall(text)) % 2 == 1:
            n += 1
    return n


def _count_stray_fence_sources(run_dir: Path) -> int:
    """Count live-source `.md` files with a stray solo ``` line outside
    any `bart-*` pair, in an artifact that contains no non-bart language
    fences (so the truncation pass is safe to run). These are trailing-
    meta residue from a previously-stripped wrapper."""
    n = 0
    for md_path in _live_md_sources(run_dir):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        if not _looks_like_bart_artifact(text):
            continue
        truncated, dropped = _truncate_trailing_meta(text)
        if dropped:
            n += 1
    return n


def _repair_unclosed_fences_in_sources(run_dir: Path) -> int:
    """Close any odd-fence-count `.md` source the renderer consumes.

    A markdown file with an odd number of `^```` lines has an unclosed
    fence — python-markdown then dumps the would-be code body as prose
    and leaves the literal triple-backtick text inside a `<p>`. We append
    a closing ``` on its own line so the next render produces a real
    `<pre><code>` block.

    The renderer reads from the *live* sources at `run_dir/*.md` and
    `run_dir/daily_lessons/*.md`; the `run_dir/markdown/` subdirectory is
    an archive copy that `build_packet` overwrites on every rebuild, so
    patching the archive alone has no effect. We patch both, since the
    archive is what users open when inspecting source-of-truth.

    Idempotent (balanced files are skipped). Returns the count patched.
    """
    candidates: list[Path] = list(_live_md_sources(run_dir))
    archive = run_dir / "markdown"
    if archive.exists():
        candidates.extend(archive.rglob("*.md"))
    n = 0
    for md_path in candidates:
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        if len(_FENCE_LINE_RE.findall(text)) % 2 != 1:
            continue
        if not text.endswith("\n"):
            text += "\n"
        text += "```\n"
        try:
            md_path.write_text(text, encoding="utf-8")
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _rebuild_packet_from_markdown(run_dir: Path) -> bool:
    """Trigger a full packet rebuild from sibling markdown — no API cost.

    Mirrors what `./run render` does, but invoked inline from `format --fix`
    when a streaming-corruption fingerprint was detected. Returns True iff
    the rebuild ran without raising. We swallow exceptions to keep the
    audit driver going — the next pass of checks will still report any
    issue the rebuild didn't actually fix.

    Before invoking the renderer, we patch any unclosed ``` fence in the
    source `.md` files — otherwise the rebuild faithfully reproduces the
    same broken page that triggered the audit. The wrapper-fence strip
    runs first so the odd-fence count is measured against the de-wrapped
    document (the wrapper opener is a fence by itself; counting it would
    flip the odd/even parity and trigger a spurious append).
    """
    try:
        import json as _json
        from ..packet import build_packet
        _strip_llm_wrapper_fence_in_sources(run_dir)
        _repair_unclosed_fences_in_sources(run_dir)
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

