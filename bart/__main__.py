"""CLI entry point. `python -m bart` or via ./run wrapper."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .branding import render_splash
from .config import load_config, run_setup_wizard
from .orchestrator import Orchestrator
from .paths import RunPaths

console = Console(stderr=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bart",
        description="Agentic study-packet harness. Drop materials in, run once, get a personalized exam-prep packet out.",
    )
    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="Generate a study packet (default).")
    run.add_argument("--reconfigure", action="store_true", help="Re-run the setup wizard before generating.")
    run.add_argument("--resume", type=str, metavar="RUN_ID", help="Resume an interrupted run by id (folder name in output/).")
    run.add_argument("--dry-run", action="store_true", help="Extract materials and plan, but do not call the API.")
    run.add_argument("--no-critic", action="store_true", help="Skip the critic-revision loop on lessons (faster, lower quality).")
    run.add_argument("--max-parallel", type=int, default=4, help="Max concurrent API calls (default 4).")
    run.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override the number of daily lessons to generate (defaults to days-until-exam).",
    )

    sub.add_parser("setup", help="Run the configuration wizard only.")
    sub.add_parser("list", help="List previous runs in output/.")
    sub.add_parser("doctor", help="Verify environment, dependencies, and API key.")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.cmd or "run"

    # Show the splash (ASCII loaf + wordmark) for interactive commands.
    if cmd in {"run", "setup"} and sys.stdout.isatty():
        render_splash(console, version=f"v{__version__}")

    if cmd == "setup":
        run_setup_wizard(force=True)
        return 0

    if cmd == "list":
        from .commands import list_runs
        return list_runs()

    if cmd == "doctor":
        from .commands import doctor
        return doctor()

    if cmd == "run":
        cfg = load_config()
        if cfg is None or args.reconfigure:
            # Quick sanity check: do they have any materials yet?
            from .paths import MATERIALS
            n_materials = 0
            if MATERIALS.exists():
                n_materials = sum(
                    1 for p in MATERIALS.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and p.name != ".gitkeep"
                )
            if n_materials == 0 and cfg is None:
                console.print(
                    "\n[yellow]heads up:[/yellow] [cyan]materials/[/cyan] is empty.\n"
                    "  drop your course PDFs / slides / notes there first, then re-run.\n"
                    "  [dim]we'll keep going for now in case you want to set up the wizard early.[/dim]\n"
                )
            cfg = run_setup_wizard(force=args.reconfigure)
        paths = RunPaths.create(resume=args.resume)
        orch = Orchestrator(
            cfg=cfg,
            paths=paths,
            console=console,
            dry_run=args.dry_run,
            use_critic=not args.no_critic,
            max_parallel=args.max_parallel,
            days_override=args.days,
        )
        return orch.run()

    return 1


if __name__ == "__main__":
    sys.exit(main())
