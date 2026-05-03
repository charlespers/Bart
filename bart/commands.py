"""Auxiliary CLI commands: list, doctor."""
from __future__ import annotations

import json
import sys

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
