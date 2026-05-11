"""Auxiliary CLI commands: list, doctor, render, format, quality, fix-patch, preview."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import load_config
from .paths import OUTPUT, ROOT


def list_runs() -> int:
    console = Console()
    if not OUTPUT.exists():
        console.print("[dim]No runs yet.[/dim]")
        return 0
    runs = sorted([d for d in OUTPUT.iterdir() if d.is_dir() and d.name.startswith("run_")])
    if not runs:
        console.print("[dim]No runs yet.[/dim]")
        return 0
    table = Table(title="Previous bart runs", border_style="cyan")
    table.add_column("run_id", style="magenta")
    table.add_column("subject", style="white")
    table.add_column("exam_date", style="white")
    table.add_column("artifacts", justify="right")
    table.add_column("cost", justify="right")
    for run in runs:
        manifest_path = run / "manifest.json"
        if manifest_path.exists():
            try:
                m = json.loads(manifest_path.read_text())
                cost = m.get("telemetry_summary", {}).get("cost_usd", 0.0)
                arts = len(m.get("artifacts", [])) + len(m.get("daily_lessons", []))
                cfg = m.get("config", {})
                table.add_row(
                    run.name,
                    cfg.get("subject", "?"),
                    cfg.get("exam_date", "?"),
                    str(arts),
                    f"${cost:.2f}",
                )
            except Exception:  # noqa: BLE001
                table.add_row(run.name, "[dim]?[/dim]", "[dim]?[/dim]", "?", "?")
        else:
            table.add_row(run.name, "[dim]incomplete[/dim]", "—", "—", "—")
    console.print(table)
    return 0


def _doctor_check_local(cfg, console) -> bool:
    """Local-mode diagnostics. Each step is self-contained, prints pass/fail
    with a remediation hint, and never raises (so a failed step doesn't
    prevent later steps from running). Returns True iff every step passes.
    """
    from .local_runtime import cache as _cache
    from .local_runtime import installer as _installer
    from .local_runtime.errors import LocalRuntimeError
    from .local_runtime.hardware import detect, recommended_tier
    from .local_runtime.models import get as get_model, pick

    ok = True

    # 1. Hardware probe.
    try:
        platform = detect()
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]✗[/red] hardware detection failed: {e}")
        return False
    console.print(
        f"[green]✓[/green] hardware: {platform.os}/{platform.arch} "
        f"[dim]{platform.accelerator}, {platform.usable_gb:.1f} GB usable[/dim]"
    )

    # 2. Model resolved.
    try:
        if cfg.local_model_key:
            model = get_model(cfg.local_model_key)
        else:
            model = pick(platform, recommended_tier(platform))
        console.print(
            f"[green]✓[/green] model: [cyan]{model.display_name}[/cyan] "
            f"[dim]({model.bytes_on_disk/1024**3:.1f} GB on disk)[/dim]"
        )
    except KeyError:
        console.print(
            f"[red]✗[/red] config references unknown model "
            f"`{cfg.local_model_key}`. Run `./run setup` to pick a valid one."
        )
        return False

    # 3. Engine package importable.
    if _installer.is_engine_installed(platform):
        console.print(
            f"[green]✓[/green] inference engine "
            f"[cyan]{_installer.engine_package(platform)}[/cyan] importable"
        )
    else:
        console.print(
            f"[yellow]⊘[/yellow] inference engine "
            f"[cyan]{_installer.engine_package(platform)}[/cyan] not installed yet "
            f"[dim](will install on first ./run)[/dim]"
        )

    # 4. Hugging Face reachability for the chosen model.
    try:
        _installer.validate_repo_reachable(model)
        console.print(
            f"[green]✓[/green] Hugging Face repo reachable "
            f"[dim]({model.hf_repo})[/dim]"
        )
    except LocalRuntimeError as e:
        console.print(f"[red]✗[/red] {e}")
        ok = False

    # 5. Cache dir writable.
    try:
        d = _cache.models_dir()
        probe = d / ".bart_write_probe"
        probe.write_text("ok")
        probe.unlink()
        console.print(f"[green]✓[/green] cache dir writable [dim]({d})[/dim]")
    except OSError as e:
        console.print(
            f"[red]✗[/red] cache dir not writable ({d}): {e}\n"
            f"  [dim]→ set BART_CACHE_DIR or fix ~/.cache/ permissions[/dim]"
        )
        ok = False

    # 6. Disk space for the chosen model.
    free_gb = _installer._disk_free_gb(_cache.models_dir())
    needed_gb = (model.bytes_on_disk * 1.10 + 1 * 1024**3) / (1024**3)
    if _cache.is_downloaded(model):
        console.print(
            f"[green]✓[/green] weights already downloaded "
            f"[dim]({model.bytes_on_disk/1024**3:.1f} GB cached)[/dim]"
        )
    elif free_gb < needed_gb:
        console.print(
            f"[red]✗[/red] not enough free disk space: need "
            f"~{needed_gb:.1f} GB, only {free_gb:.1f} GB free.\n"
            f"  [dim]→ free up space or set BART_CACHE_DIR to a larger volume[/dim]"
        )
        ok = False
    else:
        console.print(
            f"[green]✓[/green] {free_gb:.0f} GB free disk "
            f"[dim](need ~{needed_gb:.1f} GB for first download)[/dim]"
        )

    return ok


def doctor() -> int:
    console = Console()
    ok = True
    console.print("[bold cyan]bart doctor[/bold cyan]\n")

    # Python version
    if sys.version_info < (3, 10):
        console.print(f"[red]✗[/red] Python {sys.version_info.major}.{sys.version_info.minor} — need 3.10+")
        ok = False
    else:
        console.print(f"[green]✓[/green] Python {sys.version_info.major}.{sys.version_info.minor}")

    # Dependencies
    for mod in ["anthropic", "pypdf", "docx", "pptx", "rich", "pydantic"]:
        try:
            __import__(mod)
            console.print(f"[green]✓[/green] {mod} importable")
        except ImportError as e:
            console.print(f"[red]✗[/red] {mod} not importable: {e}")
            ok = False

    # Optional PDF fallbacks — not required, but enable extraction from
    # tricky PDFs (broken font maps, image-only scans).
    import os as _os
    try:
        __import__("pdfplumber")
        console.print("[green]✓[/green] pdfplumber importable [dim](handles broken font maps)[/dim]")
    except ImportError:
        console.print("[yellow]⊘[/yellow] pdfplumber not installed [dim](optional — install for better PDF extraction on tricky files)[/dim]")
    try:
        __import__("pytesseract")
        __import__("pdf2image")
        import shutil as _sh
        if _sh.which("tesseract") and _sh.which("pdftoppm"):
            disabled = _os.environ.get("BART_PDF_OCR") == "0"
            state = "disabled (BART_PDF_OCR=0)" if disabled else "auto-runs on image-only PDFs"
            console.print(f"[green]✓[/green] OCR ready [dim]({state})[/dim]")
        elif not _sh.which("tesseract"):
            console.print("[yellow]⊘[/yellow] OCR libs installed but `tesseract` binary missing [dim](brew install tesseract)[/dim]")
        else:
            console.print("[yellow]⊘[/yellow] OCR libs installed but `pdftoppm` (poppler) missing [dim](brew install poppler)[/dim]")
    except ImportError:
        console.print(
            "[yellow]⊘[/yellow] OCR not installed "
            "[dim](optional — `pip install pytesseract pdf2image` + `brew install tesseract poppler` to auto-OCR image PDFs)[/dim]"
        )

    # Config
    cfg = load_config()
    if not cfg:
        console.print("[yellow]⚠[/yellow] no config — run setup")
    else:
        console.print(f"[green]✓[/green] config: {cfg.subject} / exam {cfg.exam_date} ({cfg.days_until} days)")

    # Auth-mode-specific connectivity
    if cfg:
        if cfg.auth_mode == "local":
            ok = _doctor_check_local(cfg, console) and ok
        elif cfg.auth_mode == "claude-code":
            import shutil as _sh
            cli = _sh.which("claude")
            if not cli:
                console.print("[red]✗[/red] auth_mode=claude-code but `claude` CLI not on PATH. "
                              "Install Claude Code from https://claude.ai/code or rerun setup.")
                ok = False
            else:
                console.print(f"[green]✓[/green] `claude` CLI found at {cli}")
                # Quick sanity ping — doesn't burn meaningful subscription quota.
                # Strip API-key env vars so the CLI uses subscription auth.
                import os as _os
                scrubbed = {
                    k: v for k, v in _os.environ.items()
                    if k not in {
                        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                        "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
                        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                    }
                }
                try:
                    import subprocess
                    proc = subprocess.run(
                        [cli, "--print", "--model", cfg.fast_model],
                        input="reply with the single word: pong",
                        capture_output=True, text=True, timeout=60,
                        env=scrubbed,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        console.print(f"[green]✓[/green] subscription auth working "
                                      f"(model {cfg.fast_model})")
                    else:
                        # Try once more without --model in case that's the issue
                        proc2 = subprocess.run(
                            [cli, "--print"],
                            input="reply with the single word: pong",
                            capture_output=True, text=True, timeout=60,
                            env=scrubbed,
                        )
                        if proc2.returncode == 0 and proc2.stdout.strip():
                            console.print(
                                f"[yellow]⚠[/yellow] `claude` CLI works but rejected model "
                                f"`{cfg.fast_model}` (default model worked). Edit "
                                ".bart_config.json to use a model your subscription supports."
                            )
                        else:
                            err = (proc.stderr or proc2.stderr or '').strip()[:300]
                            console.print(f"[red]✗[/red] `claude` CLI ping failed (exit "
                                          f"{proc.returncode}). stderr: {err or '(empty)'}")
                            console.print("[dim]  hint: run `claude` once interactively to log in.[/dim]")
                            ok = False
                except Exception as e:  # noqa: BLE001
                    console.print(f"[red]✗[/red] `claude` CLI invocation error: {e}")
                    ok = False
        else:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=cfg.api_key)
                client.messages.create(
                    model=cfg.fast_model,
                    max_tokens=8,
                    messages=[{"role": "user", "content": "ping"}],
                )
                console.print(f"[green]✓[/green] API reachable ({cfg.fast_model})")
            except Exception as e:  # noqa: BLE001
                console.print(f"[red]✗[/red] API check failed: {e}")
                ok = False

    # Materials directory
    from .paths import MATERIALS
    if MATERIALS.exists():
        n = sum(1 for p in MATERIALS.rglob("*") if p.is_file() and not p.name.startswith("."))
        console.print(f"[green]✓[/green] materials/ exists ({n} files)")
    else:
        console.print("[yellow]⚠[/yellow] materials/ not yet created")

    return 0 if ok else 1


def render_packet(run_id: str | None) -> int:
    """Re-render the HTML packet for an existing run, without calling the API."""
    console = Console()
    runs = sorted([d for d in OUTPUT.iterdir() if d.is_dir() and d.name.startswith("run_")]) \
        if OUTPUT.exists() else []
    if not runs:
        console.print("[red]✗[/red] no runs in output/. nothing to render.")
        return 1
    if run_id:
        target = OUTPUT / run_id
        if not target.exists():
            console.print(f"[red]✗[/red] run '{run_id}' not found.")
            console.print("[dim]available:[/dim]")
            for r in runs:
                console.print(f"  - {r.name}")
            return 1
    else:
        target = runs[-1]
        console.print(f"[dim]rendering most recent run:[/dim] [cyan]{target.name}[/cyan]")

    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]✗[/red] manifest.json missing in {target}. cannot render.")
        return 1

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] manifest.json invalid: {e}")
        return 1

    from .render.packet import build_packet
    console.print(f"[bold #c96442]▸ Rendering[/bold #c96442]")
    warnings = build_packet(target, manifest)

    # Re-emit domain-tool pages too (chem/cs/ece reference cards). Their
    # template lives outside packet.py so a plain build_packet() leaves
    # them on whatever version the *original* run produced — including
    # the old MathJax-loader template. Without this re-run, `./run render`
    # silently leaves stale auxiliary pages, and `./run format` flags them
    # as `katex_not_loaded`.
    try:
        from .tools import TOOLS
        for tool in TOOLS:
            try:
                tool.run(target, manifest)
            except Exception:  # noqa: BLE001
                # Tool failures here aren't fatal — the packet itself is fine.
                pass
    except Exception:  # noqa: BLE001
        pass

    problems = [w for w in warnings if w.severity != "info"]
    infos = [w for w in warnings if w.severity == "info"]
    if problems:
        counts: dict[str, int] = {}
        for w in problems:
            counts[w.kind] = counts.get(w.kind, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        console.print(f"  [yellow]⚠[/yellow] {len(problems)} formatting issue(s): {summary}")
        console.print(f"  [dim]see {target}/render_warnings.json[/dim]")
    else:
        extra = f" [dim]({len(infos)} normalizations applied)[/dim]" if infos else ""
        console.print(f"  [green]✓[/green] HTML packet built cleanly{extra}")

    # Auto-run the format audit + safe autofixes on every render. The audit is
    # cheap (string-only, no API calls) and idempotent on a clean packet, so
    # there's no reason not to. This is what guarantees that the file on disk
    # is in a state the user can open and read — not just that the renderer
    # produced bytes. A regression that sneaks through the renderer (e.g. the
    # collapsed-`'\\('` KaTeX delimiter bug) gets surfaced here instead of
    # silently shipping to the browser.
    from .render.format_audit import audit, render_report
    result = audit(target, apply_fixes=True)
    if result.fixes_applied or result.errors or result.warnings:
        render_report(console, result, target)
    else:
        console.print(f"  [green]✓[/green] format audit clean ({result.files_scanned} pages)")
    console.print(f"  [dim]open[/dim] [white]{target}/index.html[/white]")
    return 0


# ── format ────────────────────────────────────────────────────────


def _resolve_run_dir(run_id: str | None) -> Path | None:
    """Locate the target run dir by id, or fall back to the most recent.
    Returns None and prints to console if nothing matches; the caller
    should treat that as a fatal CLI error."""
    console = Console()
    if not OUTPUT.exists():
        console.print("[red]✗[/red] output/ does not exist yet — generate a run first.")
        return None
    runs = sorted(d for d in OUTPUT.iterdir() if d.is_dir() and d.name.startswith("run_"))
    if not runs:
        console.print("[red]✗[/red] no runs in output/.")
        return None
    if run_id:
        target = OUTPUT / run_id
        if not target.exists():
            console.print(f"[red]✗[/red] run '{run_id}' not found.")
            console.print("[dim]available:[/dim]")
            for r in runs:
                console.print(f"  - {r.name}")
            return None
        return target
    return runs[-1]


def format_packet(
    run_id: str | None,
    *,
    apply_fixes: bool = False,
    strict: bool = False,
    rerender: bool = False,
) -> int:
    """Audit + optionally repair every HTML page under output/<run_id>/.

    Read-only by default. Pass `--fix` to apply safe in-place repairs;
    pass `--rerender` to rebuild HTML from markdown first (picks up CSS /
    template changes). `--strict` returns non-zero on any non-info issue
    so CI / automation can gate on a clean packet.
    """
    console = Console()
    target = _resolve_run_dir(run_id)
    if target is None:
        return 1
    if not run_id:
        console.print(f"[dim]auditing most recent run:[/dim] [cyan]{target.name}[/cyan]")

    if rerender:
        console.print(f"[bold #c96442]▸ Rendering[/bold #c96442]  [dim](pre-format)[/dim]")
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except json.JSONDecodeError as e:
                console.print(f"[red]✗[/red] manifest.json invalid: {e}")
                return 1
            from .render.packet import build_packet
            build_packet(target, manifest)
        else:
            console.print(f"[yellow]⚠[/yellow] manifest.json missing; skipping rerender")

    from .render.format_audit import audit, render_report
    result = audit(target, apply_fixes=apply_fixes)
    render_report(console, result, target)

    if apply_fixes and result.fixes_applied:
        console.print(
            f"\n  [dim]tip:[/dim] re-run with [cyan]--rerender[/cyan] to also pick up any "
            "CSS / template changes."
        )

    if strict and (result.errors or result.warnings):
        return 2
    if result.errors:
        return 1
    return 0


# ── quality ───────────────────────────────────────────────────────


def quality_audit(
    run_id: str | None,
    *,
    strict: bool = False,
    coverage_threshold: float = 80.0,
) -> int:
    """Run the coverage / fidelity / format harness over a generated packet.

    No API calls — coverage and fidelity are pure regex; format delegates to
    the existing `format_audit`. Writes `quality_audit.json` next to the
    run's other artifacts.
    """
    console = Console()
    target = _resolve_run_dir(run_id)
    if target is None:
        return 1
    if not run_id:
        console.print(f"[dim]auditing most recent run:[/dim] [cyan]{target.name}[/cyan]")

    from .render.harness import audit_run, write_report
    result = audit_run(target)
    out_path = write_report(target, result)

    # ── Coverage report ─────────────────────────────────────────
    cov_color = (
        "green" if result.coverage_pct >= coverage_threshold else
        "yellow" if result.coverage_pct >= 50 else
        "red"
    )
    console.print()
    console.print(
        f"[bold #c96442]▸ quality[/bold #c96442]  "
        f"[{cov_color}]coverage {result.coverage_pct:.0f}%[/{cov_color}]  "
        f"[dim]({result.coverage_hit}/{result.coverage_total} topics covered)[/dim]"
    )
    if result.coverage_findings:
        n_show = min(12, len(result.coverage_findings))
        console.print(f"\n  [yellow]missing topics[/yellow] [dim](first {n_show} of "
                      f"{len(result.coverage_findings)}):[/dim]")
        for f in result.coverage_findings[:n_show]:
            sev_glyph = "[red]✗[/red]" if f.severity == "error" else "[yellow]•[/yellow]"
            console.print(f"    {sev_glyph} [white]{f.topic}[/white]  "
                          f"[dim]({f.source})[/dim]")
        if len(result.coverage_findings) > n_show:
            console.print(f"    [dim]… and {len(result.coverage_findings) - n_show} "
                          f"more in {out_path.name}[/dim]")

    # ── Fidelity report ─────────────────────────────────────────
    fid_color = (
        "green" if result.fidelity_pct >= 90 else
        "yellow" if result.fidelity_pct >= 70 else
        "red"
    )
    console.print()
    console.print(
        f"[bold #c96442]▸ fidelity[/bold #c96442]  "
        f"[{fid_color}]{result.fidelity_pct:.0f}%[/{fid_color}]  "
        f"[dim]({result.cites_total - result.cites_unmatched}/{result.cites_total} "
        f"cites match the corpus)[/dim]"
    )
    if result.fidelity_findings:
        n_show = min(8, len(result.fidelity_findings))
        console.print(f"\n  [yellow]unmatched cites[/yellow] [dim](first {n_show} of "
                      f"{len(result.fidelity_findings)}):[/dim]")
        for f in result.fidelity_findings[:n_show]:
            console.print(f"    [yellow]•[/yellow] [white]{f.cite}[/white]  "
                          f"[dim]({f.artifact})[/dim]")

    # ── Format summary ──────────────────────────────────────────
    fmt = result.format_summary or {}
    if "error" in fmt:
        console.print(f"\n[bold #c96442]▸ format[/bold #c96442]  "
                      f"[red]check failed: {fmt['error']}[/red]")
    elif fmt:
        errs = fmt.get("errors", 0)
        warns = fmt.get("warnings", 0)
        glyph = "[green]✓[/green]" if (errs == 0 and warns == 0) else \
                "[red]✗[/red]" if errs else "[yellow]⚠[/yellow]"
        console.print(f"\n[bold #c96442]▸ format[/bold #c96442]  {glyph}  "
                      f"[dim]{fmt.get('files_scanned', 0)} HTML page(s)  ·  "
                      f"{errs} error(s), {warns} warning(s)[/dim]")
        if errs or warns:
            console.print(f"  [dim]for line-level detail: ./run format {target.name}[/dim]")

    console.print(f"\n[dim]full report:[/dim] {out_path}")

    if strict:
        if result.coverage_pct < coverage_threshold:
            return 2
        if any(f.severity == "error" for f in result.coverage_findings):
            return 2
        if (fmt or {}).get("errors", 0):
            return 2
    return 0


# ── fix-patch ─────────────────────────────────────────────────────


def fix_patch(
    run_id: str | None,
    *,
    coverage_threshold: float = 80.0,
    allow_api: bool = False,
) -> int:
    """One-shot recovery: bring an existing packet up to current quality.

    Pipeline (every stage is idempotent and skipped when nothing to do):

      1. **Re-render**   from sibling markdown — picks up CSS / template /
         renderer fixes that landed after the run was generated.
      2. **Format --fix** — applies every safe in-place repair (double-
         escaped math, unbalanced \\[, font-size overrides, lazy-loaded
         images, double-escaped entities) and triggers another rebuild
         if a streaming-corruption fingerprint is detected.
      3. **Quality audit** — runs coverage + fidelity + format; surfaces
         the same report `./run quality` would.
      4. **Per-page recovery** — for every HTML page that is still
         broken (errors after the format pass) AND has a sibling .md,
         force one more `build_packet` cycle. The markdown is the source
         of truth and re-rendering twice is free.

    No new agent calls. Safe to run on any run. Mirrors what a user would
    do by hand: `./run render && ./run format --fix && ./run quality`.

    `allow_api` is reserved for a future stage that would regenerate
    artifacts which can't be rebuilt from on-disk markdown (e.g. a day
    file the orchestrator dropped). Off by default — keeps the cost
    floor at zero.
    """
    console = Console()
    target = _resolve_run_dir(run_id)
    if target is None:
        return 1
    if not run_id:
        console.print(f"[dim]patching most recent run:[/dim] [cyan]{target.name}[/cyan]")

    # ── Stage 1: rerender ──────────────────────────────────────
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]✗[/red] manifest.json missing in {target}; cannot patch.")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        console.print(f"[red]✗[/red] manifest.json invalid: {e}")
        return 1

    console.print(f"\n[bold #c96442]▸ stage 1[/bold #c96442]  re-render from markdown  "
                  f"[dim](no API cost)[/dim]")
    from .render.packet import build_packet
    render_warnings = build_packet(target, manifest)
    rebuild_problems = [w for w in render_warnings if w.severity != "info"]
    if rebuild_problems:
        counts: dict[str, int] = {}
        for w in rebuild_problems:
            counts[w.kind] = counts.get(w.kind, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        console.print(f"  [yellow]⚠[/yellow] {len(rebuild_problems)} render warning(s): {summary}")
    else:
        console.print(f"  [green]✓[/green] render clean")

    # Re-emit domain-tool reference pages too — same rationale as render_packet.
    try:
        from .tools import TOOLS
        for tool in TOOLS:
            try:
                tool.run(target, manifest)
            except Exception:
                pass
    except Exception:
        pass

    # ── Stage 2: format --fix ──────────────────────────────────
    console.print(f"\n[bold #c96442]▸ stage 2[/bold #c96442]  format --fix  "
                  f"[dim](in-place repairs + corruption rebuild)[/dim]")
    from .render.format_audit import audit, render_report
    fmt_result = audit(target, apply_fixes=True)
    if fmt_result.fixes_applied:
        console.print(f"  [green]✓[/green] applied {fmt_result.fixes_applied} repair(s) "
                      f"across {fmt_result.files_scanned} page(s)")
    else:
        console.print(f"  [green]✓[/green] no repairs needed across {fmt_result.files_scanned} page(s)")

    # ── Stage 3: quality harness ───────────────────────────────
    console.print(f"\n[bold #c96442]▸ stage 3[/bold #c96442]  quality harness  "
                  f"[dim](coverage + fidelity + format)[/dim]")
    from .render.harness import audit_run, write_report
    q = audit_run(target)
    out_path = write_report(target, q)
    cov_color = (
        "green" if q.coverage_pct >= coverage_threshold else
        "yellow" if q.coverage_pct >= 50 else
        "red"
    )
    fid_color = (
        "green" if q.fidelity_pct >= 90 else
        "yellow" if q.fidelity_pct >= 70 else
        "red"
    )
    console.print(
        f"  [{cov_color}]coverage {q.coverage_pct:.0f}%[/{cov_color}] "
        f"[dim]({q.coverage_hit}/{q.coverage_total})[/dim]   "
        f"[{fid_color}]fidelity {q.fidelity_pct:.0f}%[/{fid_color}] "
        f"[dim]({q.cites_total - q.cites_unmatched}/{q.cites_total})[/dim]"
    )
    if q.coverage_findings:
        n_show = min(8, len(q.coverage_findings))
        console.print(f"  [yellow]missing topics[/yellow] [dim]({n_show}/{len(q.coverage_findings)} shown):[/dim]")
        for f in q.coverage_findings[:n_show]:
            console.print(f"    • [white]{f.topic}[/white]  [dim]({f.source})[/dim]")

    # ── Stage 4: per-page recovery for still-broken pages ──────
    fmt_post = audit(target, apply_fixes=False)
    still_broken = [i for i in fmt_post.errors]
    by_file: dict[str, list] = {}
    for i in still_broken:
        if i.file == "<run>":
            continue
        by_file.setdefault(i.file, []).append(i)

    if by_file:
        console.print(f"\n[bold #c96442]▸ stage 4[/bold #c96442]  per-page recovery  "
                      f"[dim]({len(by_file)} page(s) still broken)[/dim]")
        # build_packet is whole-packet; running it again after stage 1
        # rarely helps, but it's the only no-cost lever we have. Then
        # re-audit and surface what's left.
        build_packet(target, manifest)
        fmt_final = audit(target, apply_fixes=True)
        leftover = [i for i in fmt_final.errors if i.file != "<run>"]
        if leftover:
            console.print(f"  [yellow]⚠[/yellow] {len(leftover)} error(s) remain after rebuild")
            if not allow_api:
                console.print(
                    f"  [dim]these need agent regeneration; rerun with[/dim] "
                    f"[cyan]./run --resume {target.name}[/cyan] "
                    f"[dim]to regenerate the affected day(s).[/dim]"
                )
        else:
            console.print(f"  [green]✓[/green] every page now passes")
    else:
        console.print(f"\n[bold #c96442]▸ stage 4[/bold #c96442]  per-page recovery  "
                      f"[dim](nothing to do — every page passed stage 2)[/dim]")

    console.print(f"\n[dim]quality report:[/dim] {out_path}")
    console.print(f"[dim]open[/dim] [white]{target}/index.html[/white]")
    return 0


# ── preview ───────────────────────────────────────────────────────


def preview(
    md_path: str | None = None,
    *,
    open_browser: bool = True,
    out_dir: str | None = None,
) -> int:
    """Render an arbitrary markdown file through the bart pipeline and open
    it in the browser. Sandbox for ad-hoc content (e.g. a practice exam
    pasted in from elsewhere) — no API calls, no run_id required.

    `md_path`: path to a .md file. If None, reads from STDIN.
    `open_browser`: open the resulting HTML in the default browser.
    `out_dir`: where to write the preview packet (default: a fresh tmp dir).
    """
    import json as _json
    import shutil as _shutil
    import sys as _sys
    import tempfile as _tempfile
    import webbrowser as _wb
    from pathlib import Path as _P

    console = Console()

    # ── 1. Load the markdown ─────────────────────────────────────
    if md_path:
        src = _P(md_path).expanduser().resolve()
        if not src.exists():
            console.print(f"[red]✗[/red] file not found: {src}")
            return 1
        if src.is_dir():
            console.print(f"[red]✗[/red] expected a file, got a directory: {src}")
            return 1
        try:
            md_text = src.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]✗[/red] cannot read {src}: {e}")
            return 1
        title_hint = src.stem
    else:
        if _sys.stdin.isatty():
            console.print(
                "[dim]reading markdown from stdin — paste content, then Ctrl-D:[/dim]"
            )
        md_text = _sys.stdin.read()
        if not md_text.strip():
            console.print("[red]✗[/red] no markdown content received on stdin.")
            return 1
        title_hint = "preview"

    # ── 2. Stage a minimal run_dir so build_packet can do its job ─
    if out_dir:
        run_dir = _P(out_dir).expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = _P(_tempfile.mkdtemp(prefix="bart_preview_"))
    console.print(f"[bold #c96442]▸ preview[/bold #c96442]  staging at [cyan]{run_dir}[/cyan]")

    # Write the markdown as a top-level artifact so build_packet picks it up.
    safe_stem = "".join(c for c in title_hint if c.isalnum() or c in "_-")[:40] or "preview"
    md_filename = f"00_{safe_stem.upper()}.md"
    (run_dir / md_filename).write_text(md_text, encoding="utf-8")

    # Minimal manifest. build_packet inspects daily_lessons; we provide none.
    manifest = {
        "config": {
            "subject": title_hint.replace("_", " ").title(),
            "exam_date": "",
        },
        "generated_at": "",
        "artifacts": [{"file": md_filename, "label": title_hint}],
        "daily_lessons": [],
    }
    (run_dir / "manifest.json").write_text(
        _json.dumps(manifest, indent=2), encoding="utf-8"
    )

    # ── 3. Drive the renderer (no agent calls) ───────────────────
    try:
        from .render.packet import build_packet
        warnings = build_packet(run_dir, manifest)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]✗[/red] render failed: {type(e).__name__}: {e}")
        return 1

    # Run format autofix once so the page is clean on first open.
    try:
        from .render.format_audit import audit, render_report
        result = audit(run_dir, apply_fixes=True)
        if result.errors:
            console.print(
                f"  [yellow]⚠[/yellow] {len(result.errors)} formatting error(s) — "
                f"see the rendered page for visible markers"
            )
    except Exception:
        pass

    # Locate the HTML — build_packet emits using the artifact's `html` field
    # (lowercased markdown filename with .html). For a top-level artifact
    # named `00_FOO.md`, the HTML is `00_foo.html`.
    candidates = [
        run_dir / "index.html",
        run_dir / md_filename.lower().replace(".md", ".html"),
    ]
    html_file = next((c for c in candidates if c.exists()), None)
    # Fall back to any HTML found in the run dir.
    if html_file is None:
        for p in run_dir.rglob("*.html"):
            html_file = p
            break

    if html_file is None:
        console.print(f"[red]✗[/red] no HTML produced — nothing to open.")
        return 1

    console.print(f"  [green]✓[/green] rendered  [white]{html_file}[/white]")

    if open_browser:
        try:
            _wb.open(html_file.as_uri())
            console.print(f"  [dim]opened in default browser[/dim]")
        except Exception:  # noqa: BLE001
            console.print(f"  [dim]open manually:[/dim] [white]{html_file}[/white]")

    n_warns = sum(1 for w in warnings if w.severity != "info")
    if n_warns:
        console.print(f"  [yellow]⚠[/yellow] {n_warns} render warning(s) — "
                      f"check the page for inline issues.")
    return 0
