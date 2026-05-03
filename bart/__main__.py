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
    run.add_argument(
        "--fast",
        action="store_true",
        help="Speed preset: Sonnet primary model, no critic loop, --max-parallel 8. ~5x faster, slightly lower quality.",
    )
    run.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override primary model for this run (e.g. claude-sonnet-4-6, claude-haiku-4-5-20251001).",
    )

    sub.add_parser("setup", help="Run the configuration wizard only.")
    sub.add_parser("list", help="List previous runs in output/.")
    sub.add_parser("doctor", help="Verify environment, dependencies, and API key.")

    rndr = sub.add_parser("render", help="Re-render an existing run's HTML packet (no API calls).")
    rndr.add_argument("run_id", nargs="?", help="Run id (folder name in output/). Defaults to most recent.")

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # If the user typed bare `./run` (or any flags but no subcommand), default
    # the subcommand to `run`. This makes the run-subcommand's flags available
    # on the namespace even when the user didn't type the word "run".
    known_cmds = {"run", "setup", "list", "doctor", "render"}
    if not argv or argv[0] not in known_cmds:
        argv = ["run", *argv]
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

    if cmd == "render":
        from .commands import render_packet
        return render_packet(getattr(args, "run_id", None))

    if cmd == "run":
        reconfigure = getattr(args, "reconfigure", False)
        resume = getattr(args, "resume", None)
        dry_run = getattr(args, "dry_run", False)
        no_critic = getattr(args, "no_critic", False)
        max_parallel = getattr(args, "max_parallel", 4)
        days_override = getattr(args, "days", None)
        fast_mode = getattr(args, "fast", False)
        model_override = getattr(args, "model", None)

        # --fast preset: sonnet + no critic + parallel 8
        if fast_mode:
            no_critic = True
            if max_parallel == 4:  # only bump if user didn't explicitly set it
                max_parallel = 8
            if not model_override:
                model_override = "claude-sonnet-4-6"

        cfg = load_config()
        if cfg is None or reconfigure:
            from .paths import MATERIALS
            n_materials = 0
            if MATERIALS.exists():
                n_materials = sum(
                    1 for p in MATERIALS.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and p.name != ".gitkeep"
                )
            if n_materials == 0 and cfg is None:
                console.print(
                    "\n[yellow]heads up:[/yellow] [cyan]materials/[/cyan] looks empty.\n"
                    "  drop your course PDFs / slides / notes there first, then re-run.\n"
                    "  [dim](you can finish the setup wizard now and add materials after.)[/dim]\n"
                )
            cfg = run_setup_wizard(force=reconfigure)
        # Apply per-run model override without persisting it.
        if model_override:
            cfg = cfg.model_copy(update={"primary_model": model_override})

        paths = RunPaths.create(resume=resume)
        orch = Orchestrator(
            cfg=cfg,
            paths=paths,
            console=console,
            dry_run=dry_run,
            use_critic=not no_critic,
            max_parallel=max_parallel,
            days_override=days_override,
        )
        return orch.run()

    return 1


if __name__ == "__main__":
    sys.exit(main())
