"""CLI entry point. `python -m bart` or via ./run wrapper."""
from __future__ import annotations

import argparse
import os
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
    # Top-level --format shortcut: re-render + autofix the most recent (or
    # named) run in one call. Equivalent to `./run format --rerender --fix`,
    # but available as a bare top-level flag so any project can keep a single
    # "make my packet pretty" muscle-memory command: `./run --format`.
    p.add_argument(
        "--format",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="RUN_ID",
        help="Re-render and autofix an existing run's HTML packet in one step. "
             "Pass a run id to target a specific run; omit to use the most "
             "recent. Equivalent to `./run format --rerender --fix`.",
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
        help="Speed preset: Sonnet primary model, no critic loop. ~3-5x faster than default.",
    )
    run.add_argument(
        "--turbo",
        action="store_true",
        help="Maximum-speed preset: Haiku for top-level artifacts, Sonnet for daily lessons, no critic. "
             "Best when subscription rate-limited.",
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

    fmt = sub.add_parser(
        "format",
        help="Audit and auto-fix formatting/layout issues across every HTML page in a run "
             "(no API calls). Verifies KaTeX wiring, math balance, asset paths, font/spacing "
             "rules, accessibility basics, and library-block structural invariants.",
    )
    fmt.add_argument("run_id", nargs="?", help="Run id. Defaults to most recent.")
    fmt.add_argument("--fix", action="store_true",
                     help="Apply safe auto-repairs in place (otherwise read-only).")
    fmt.add_argument("--strict", action="store_true",
                     help="Exit non-zero if any warning is found (CI mode).")
    fmt.add_argument("--rerender", action="store_true",
                     help="Re-render the packet from markdown after fixing — picks up CSS/template changes.")

    pv = sub.add_parser(
        "preview",
        help="Sandbox: render an arbitrary markdown file (or stdin) through the full bart "
             "pipeline and open it in the browser. No API calls, no run_id needed — just "
             "paste in a practice exam, lesson draft, or test fragment and see it render. "
             "Usage: ./run preview path/to/exam.md  OR  cat exam.md | ./run preview",
    )
    pv.add_argument("md_path", nargs="?",
                    help="Path to a markdown file. Omit to read from stdin.")
    pv.add_argument("--no-open", action="store_true",
                    help="Don't open the result in the browser; just print the path.")
    pv.add_argument("--out", type=str, default=None, metavar="DIR",
                    help="Write the preview packet to DIR (default: a fresh tmp dir).")

    fp = sub.add_parser(
        "fix-patch",
        help="Recover an old run: re-render from markdown, apply every safe format autofix, "
             "run the quality harness, and force-rebuild pages that are still broken. Zero "
             "API cost. Idempotent — safe to re-run.",
    )
    fp.add_argument("run_id", nargs="?", help="Run id. Defaults to most recent.")
    fp.add_argument(
        "--coverage-threshold", type=float, default=80.0, metavar="PCT",
        help="Coverage percentage to warn below (default 80.0).",
    )

    qual = sub.add_parser(
        "quality",
        help="Quality harness: coverage (does the packet review every chapter / lecture / "
             "section in your source materials?), fidelity (do cites point to real corpus "
             "entries?), and formatting (delegates to `format`). Heuristic-only; zero API "
             "calls. Writes <run>/quality_audit.json.",
    )
    qual.add_argument("run_id", nargs="?", help="Run id. Defaults to most recent.")
    qual.add_argument("--strict", action="store_true",
                      help="Exit non-zero if coverage < 80% or any error-level finding "
                           "(CI mode).")
    qual.add_argument(
        "--coverage-threshold", type=float, default=80.0, metavar="PCT",
        help="Coverage percentage below which the harness exits non-zero in --strict "
             "(default 80.0).",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # Top-level --format shortcut: short-circuit straight into the format
    # command before the run-subcommand fallback below grabs the args.
    if any(a == "--format" or a.startswith("--format=") for a in argv):
        # argparse handles --format value parsing; build a tiny parser for it.
        top = argparse.ArgumentParser(add_help=False)
        top.add_argument("--format", nargs="?", const="__latest__", default=None)
        ns, _rest = top.parse_known_args(argv)
        from .commands import format_packet
        run_id = None if ns.format == "__latest__" else ns.format
        return format_packet(run_id=run_id, apply_fixes=True, rerender=True, strict=False)

    # If the user typed bare `./run` (or any flags but no subcommand), default
    # the subcommand to `run`. This makes the run-subcommand's flags available
    # on the namespace even when the user didn't type the word "run".
    known_cmds = {"run", "setup", "list", "doctor", "render", "format", "quality", "fix-patch", "preview"}
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

    if cmd == "format":
        from .commands import format_packet
        return format_packet(
            run_id=getattr(args, "run_id", None),
            apply_fixes=getattr(args, "fix", False),
            strict=getattr(args, "strict", False),
            rerender=getattr(args, "rerender", False),
        )

    if cmd == "quality":
        from .commands import quality_audit
        return quality_audit(
            run_id=getattr(args, "run_id", None),
            strict=getattr(args, "strict", False),
            coverage_threshold=getattr(args, "coverage_threshold", 80.0),
        )

    if cmd == "fix-patch":
        from .commands import fix_patch
        return fix_patch(
            run_id=getattr(args, "run_id", None),
            coverage_threshold=getattr(args, "coverage_threshold", 80.0),
        )

    if cmd == "preview":
        from .commands import preview
        return preview(
            md_path=getattr(args, "md_path", None),
            open_browser=not getattr(args, "no_open", False),
            out_dir=getattr(args, "out", None),
        )

    if cmd == "run":
        reconfigure = getattr(args, "reconfigure", False)
        resume = getattr(args, "resume", None)
        dry_run = getattr(args, "dry_run", False)
        no_critic = getattr(args, "no_critic", False)
        max_parallel = getattr(args, "max_parallel", 4)
        days_override = getattr(args, "days", None)
        fast_mode = getattr(args, "fast", False)
        turbo_mode = getattr(args, "turbo", False)
        model_override = getattr(args, "model", None)

        # --fast:  Sonnet primary, no critic, parallel=4.
        # --turbo: Haiku everywhere, no critic, no block-fix, parallel=3.
        #          Daily lessons that previously took 10-20 min on Sonnet
        #          finish in ~90s on Haiku with comparable structural quality
        #          (skeleton + density gate carry the load).
        if turbo_mode or fast_mode:
            no_critic = True
            if not model_override:
                model_override = "claude-sonnet-4-6"
        if turbo_mode:
            # Haiku for the daily-lesson Author too. Massive wall-time win.
            if not getattr(args, "model", None):
                model_override = "claude-haiku-4-5-20251001"
            os.environ["BART_TOP_LEVEL_MODEL_OVERRIDE"] = "claude-haiku-4-5-20251001"
            # Block-fix continuation stays ENABLED in turbo — Haiku occasionally
            # under-uses bart blocks, and the fix is a cheap (≤1500 tok) Haiku
            # call that guarantees structural density. Speed dominates output
            # length; this retry costs ~5-10s and keeps block usage high.
            os.environ.pop("BART_SKIP_BLOCK_FIX", None)
            # Haiku tolerates higher concurrency than Sonnet on subscription.
            max_parallel = 3

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

        # Subscription mode + parallel=4 silently queues calls at the API edge.
        # If the user is on subscription auth and didn't manually set --max-parallel,
        # cap at 2 — empirically this is faster wall-clock than 4.
        if cfg.auth_mode == "claude-code" and max_parallel == 4 and not turbo_mode:
            console.print(
                "[dim]subscription mode detected: capping parallel calls at 2 "
                "(rate-limit aware). use[/dim] [white]--max-parallel N[/white] "
                "[dim]to override.[/dim]"
            )
            max_parallel = 2

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
