"""Command-line interface for the Bart Instagram outreach pipeline.

    outreach doctor [--check-token]   preflight checks
    outreach generate [--date D] [--angle "..."]   build tomorrow's Reel
    outreach review [--date D]        list the queue / inspect one item
    outreach approve --date D         pass the review gate
    outreach reject --date D          drop an item
    outreach publish --date D [--video-url URL]   publish an approved Reel
    outreach insights                 performance of published Reels
"""
from __future__ import annotations

import argparse
import sys
from datetime import date as _date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_or_init
from .doctor import FAIL, OK, WARN, run_doctor
from .paths import ItemPaths
from .review import list_items, read_status, set_state

console = Console()

_STATUS_STYLE = {OK: "green", WARN: "yellow", FAIL: "red"}
_STATE_STYLE = {"draft": "yellow", "approved": "cyan",
                "rejected": "red", "published": "green"}
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


def _tomorrow() -> str:
    return (_date.today() + timedelta(days=1)).isoformat()


def _banner() -> None:
    console.print(Panel.fit("[bold]bart[/bold][#c96442].[/#c96442] [dim]outreach[/dim]",
                            border_style="#c96442"))


# ── commands ─────────────────────────────────────────────────────────

def cmd_doctor(args: argparse.Namespace) -> int:
    _banner()
    cfg = load_or_init()
    checks = run_doctor(cfg, check_token=args.check_token)
    table = Table(show_header=True, header_style="bold")
    table.add_column("check"); table.add_column("status"); table.add_column("detail")
    for c in checks:
        style = _STATUS_STYLE[c.status]
        table.add_row(c.name, f"[{style}]{_MARK[c.status]} {c.status}[/{style}]", c.detail)
    console.print(table)
    if any(c.status == FAIL for c in checks):
        console.print("[red]Some checks failed — fix them before running `generate`/`publish`.[/red]")
        return 1
    console.print("[green]Ready.[/green]")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    _banner()
    cfg = load_or_init()
    # Fail fast on the checks `generate` actually needs.
    checks = run_doctor(cfg, check_token=False)
    blocking = [c for c in checks if c.status == FAIL
                and c.name in ("python deps", "node", "npx", "ffmpeg",
                               "anthropic key", "tts")]
    if blocking:
        for c in blocking:
            console.print(f"[red]✗ {c.name}: {c.detail}[/red]")
        console.print("[red]Run `outreach doctor` and fix these first.[/red]")
        return 1

    target = args.date or _tomorrow()
    console.print(f"[bold]Generating Reel for {target}[/bold]\n")
    from .generate import generate_item
    from .render import RenderError
    from .script import ScriptError
    from .audio import AudioError
    try:
        result = generate_item(cfg, target, angle=args.angle, log=console.print)
    except (ScriptError, AudioError, RenderError) as e:
        console.print(f"\n[red]✗ generate failed: {e}[/red]")
        return 1
    console.print(
        f"\n[green]Draft {target} is ready for review.[/green]\n"
        f"  preview : {result.item.video_path}\n"
        f"  caption : {result.item.caption_path}\n"
        f"  approve : [white]outreach approve --date {target}[/white]"
    )
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    _banner()
    if args.date:
        item = ItemPaths.for_date(args.date)
        if not item.status_path.exists():
            console.print(f"[red]No queue item for {args.date}.[/red]")
            return 1
        st = read_status(item)
        style = _STATE_STYLE.get(st.state, "white")
        console.print(f"[bold]{st.date}[/bold]  state=[{style}]{st.state}[/{style}]")
        if item.caption_path.exists():
            console.print(Panel(item.caption_path.read_text(), title="caption",
                                border_style="dim"))
        for f, label in ((item.video_path, "video"), (item.audio_path, "audio")):
            console.print(f"  {label}: {f if f.exists() else '[dim](not generated)[/dim]'}")
        console.print("[dim]history:[/dim]")
        for h in st.history:
            console.print(f"  {h}")
        return 0

    items = list_items()
    if not items:
        console.print("[dim]Queue is empty. Run `outreach generate`.[/dim]")
        return 0
    table = Table(show_header=True, header_style="bold")
    table.add_column("date"); table.add_column("state"); table.add_column("media id")
    for st in items:
        style = _STATE_STYLE.get(st.state, "white")
        table.add_row(st.date, f"[{style}]{st.state}[/{style}]", st.media_id or "—")
    console.print(table)
    return 0


def _transition(date: str, new_state: str, note: str) -> int:
    try:
        st = set_state(date, new_state, note)
    except FileNotFoundError:
        console.print(f"[red]No queue item for {date}.[/red]")
        return 1
    except Exception as e:  # InvalidTransition
        console.print(f"[red]✗ {e}[/red]")
        return 1
    console.print(f"[green]✓ {date} → {st.state}[/green]")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _transition(args.date, "approved", "approved by reviewer")


def cmd_reject(args: argparse.Namespace) -> int:
    return _transition(args.date, "rejected", args.reason or "rejected by reviewer")


def cmd_publish(args: argparse.Namespace) -> int:
    _banner()
    cfg = load_or_init()
    item = ItemPaths.for_date(args.date)
    if not item.status_path.exists():
        console.print(f"[red]No queue item for {args.date}.[/red]")
        return 1
    st = read_status(item)
    if st.state != "approved":
        console.print(
            f"[red]✗ {args.date} is '{st.state}', not 'approved'. The review "
            f"gate cannot be skipped — run `outreach approve --date {args.date}` "
            f"first.[/red]"
        )
        return 1

    from .publish import PublishError, publish_reel, video_url_for
    try:
        video_url = args.video_url or video_url_for(cfg, args.date)
    except PublishError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1

    caption = item.caption_path.read_text() if item.caption_path.exists() else ""
    console.print(f"[bold]Publishing {args.date}[/bold]")
    console.print(f"[dim]The MP4 must already be live at:[/dim] {video_url}")
    try:
        result = publish_reel(cfg, video_url, caption, log=console.print)
    except PublishError as e:
        console.print(f"[red]✗ publish failed: {e}[/red]")
        return 1

    st.media_id = result.media_id
    st.permalink = result.permalink
    st.transition("published", f"media_id={result.media_id}")
    from .review import write_status
    write_status(item, st)
    console.print(f"[green]✓ published — {result.permalink or result.media_id}[/green]")
    return 0


def cmd_insights(args: argparse.Namespace) -> int:
    _banner()
    cfg = load_or_init()
    from .insights import InsightsError, best_posting_summary, gather_insights
    try:
        rows = gather_insights(cfg)
    except InsightsError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    if not rows:
        console.print("[dim]No published Reels yet — nothing to measure.[/dim]")
        return 0
    table = Table(show_header=True, header_style="bold")
    for col in ("date", "reach", "plays", "likes", "saved", "shares"):
        table.add_column(col)
    for r in rows:
        if r.error:
            table.add_row(r.date, f"[red]error: {r.error}[/red]", "", "", "", "")
            continue
        m = r.metrics
        table.add_row(r.date, str(m.get("reach", "—")), str(m.get("plays", "—")),
                      str(m.get("likes", "—")), str(m.get("saved", "—")),
                      str(m.get("shares", "—")))
    console.print(table)
    console.print(f"[dim]{best_posting_summary(rows)}[/dim]")
    return 0


# ── parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="outreach",
                                description="Bart Instagram outreach pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="run preflight checks")
    d.add_argument("--check-token", action="store_true",
                   help="also make a live Graph API call to validate the token")
    d.set_defaults(func=cmd_doctor)

    g = sub.add_parser("generate", help="build a Reel (defaults to tomorrow)")
    g.add_argument("--date", help="YYYY-MM-DD (default: tomorrow)")
    g.add_argument("--angle", help="optional angle/topic to steer the script")
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("review", help="list the queue, or inspect one item")
    r.add_argument("--date", help="inspect a single dated item")
    r.set_defaults(func=cmd_review)

    a = sub.add_parser("approve", help="pass an item through the review gate")
    a.add_argument("--date", required=True)
    a.set_defaults(func=cmd_approve)

    rj = sub.add_parser("reject", help="reject an item")
    rj.add_argument("--date", required=True)
    rj.add_argument("--reason", help="optional rejection note")
    rj.set_defaults(func=cmd_reject)

    pub = sub.add_parser("publish", help="publish an approved Reel via the Graph API")
    pub.add_argument("--date", required=True)
    pub.add_argument("--video-url",
                     help="public HTTPS URL of the MP4 (overrides config)")
    pub.set_defaults(func=cmd_publish)

    ins = sub.add_parser("insights", help="performance of published Reels")
    ins.set_defaults(func=cmd_insights)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
