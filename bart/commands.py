"""Auxiliary CLI commands: list, doctor, render, format."""
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
        if cfg.auth_mode == "claude-code":
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
