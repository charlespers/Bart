"""Configuration management — wizard, file storage, validation."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from .branding import ACCENT, INK
from .paths import ROOT

CONFIG_PATH = ROOT / ".bart_config.json"


class Config(BaseModel):
    auth_mode: str = "api"  # "api" | "claude-code"
    api_key: str = ""        # required when auth_mode == "api"
    exam_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    subject: str = Field(min_length=1, max_length=200)
    guidance: str = ""
    student_level: str = "undergraduate"  # undergraduate | graduate | aplevel | professional
    style: str = "academic-rigorous"  # academic-rigorous | conversational | minimal
    daily_hours: float = 3.0
    primary_model: str = "claude-opus-4-7"
    fast_model: str = "claude-haiku-4-5-20251001"

    @field_validator("exam_date")
    @classmethod
    def must_be_future(cls, v: str) -> str:
        d = datetime.strptime(v, "%Y-%m-%d").date()
        if d < date.today():
            raise ValueError("exam_date must be today or in the future")
        return v

    @field_validator("auth_mode")
    @classmethod
    def valid_auth_mode(cls, v: str) -> str:
        if v not in ("api", "claude-code"):
            raise ValueError("auth_mode must be 'api' or 'claude-code'")
        return v

    @property
    def exam(self) -> date:
        return datetime.strptime(self.exam_date, "%Y-%m-%d").date()

    @property
    def days_until(self) -> int:
        return (self.exam - date.today()).days


def load_config() -> Optional[Config]:
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return Config(**data)
    except (json.JSONDecodeError, ValidationError):
        return None


def save_config(cfg: Config) -> None:
    CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def run_setup_wizard(force: bool = False) -> Config:
    """Interactive setup. Reuses any existing values unless force=True."""
    console = Console()
    is_first_run = not load_config()
    if is_first_run or force:
        console.print(
            Panel.fit(
                f"[bold]welcome.[/bold] [dim]i'm[/dim] [bold]bart[/bold][bold {ACCENT}].[/bold {ACCENT}]\n"
                "[dim]we'll ask 6 quick questions, then get out of your way.[/dim]\n"
                "[dim]you can change any answer later with[/dim] [white]./run setup[/white]",
                border_style=ACCENT,
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[bold]bart[/bold][bold {ACCENT}].[/bold {ACCENT}] [dim]quick reconfigure[/dim]",
                border_style=ACCENT,
            )
        )

    existing = load_config() if not force else None
    defaults = existing.model_dump() if existing else {}

    # ─── 1/6  Auth mode + (optional) API key ───
    from .backends import ClaudeCodeBackend
    claude_cli_present = ClaudeCodeBackend.is_available()
    saved_mode = defaults.get("auth_mode", "api")
    saved_key = defaults.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")

    auth_mode = saved_mode
    api_key = saved_key

    if not force and saved_mode == "claude-code" and claude_cli_present:
        if Confirm.ask("[1/6] Use saved auth mode (Claude Code subscription)?", default=True):
            console.print(f"[dim]  → using `claude` CLI at[/dim] [white]{shutil.which('claude')}[/white]")
        else:
            auth_mode = ""
    elif not force and saved_mode == "api" and saved_key:
        if Confirm.ask("[1/6] Use saved Anthropic API key?", default=True):
            pass
        else:
            auth_mode = ""
            api_key = ""
    else:
        auth_mode = ""

    if not auth_mode:
        # Offer the choice. Only show "claude-code" if the CLI is installed.
        if claude_cli_present:
            console.print(
                "\n[bold][1/6] How do you want bart to talk to Claude?[/bold]\n"
                f"  [{ACCENT}]1[/{ACCENT}]  Claude Code subscription [dim](use your existing claude.ai login — recommended if you have Pro/Max/Team)[/dim]\n"
                f"  [{ACCENT}]2[/{ACCENT}]  Anthropic API key      [dim](pay-per-token, supports prompt caching + cost tracking)[/dim]"
            )
            choice = Prompt.ask("  pick", choices=["1", "2"], default="1")
            auth_mode = "claude-code" if choice == "1" else "api"
        else:
            console.print(
                "\n[dim]bart found no `claude` CLI on PATH, so subscription auth isn't available.[/dim]\n"
                "[dim]If you have Pro/Max/Team, install Claude Code from[/dim] [cyan]https://claude.ai/code[/cyan] [dim]and rerun setup.[/dim]\n"
                "[dim]Otherwise, paste an Anthropic API key below.[/dim]"
            )
            auth_mode = "api"

    if auth_mode == "api" and not api_key:
        console.print(
            "[dim]Need a key? Grab one at [cyan]https://console.anthropic.com[/cyan] (free credits on signup).[/dim]"
        )
        api_key = Prompt.ask(
            "[bold]Anthropic API key[/bold] [dim](sk-ant-…)[/dim]",
            password=True,
        )
        if not api_key.strip():
            console.print("[red]✗ API key required. Get one at https://console.anthropic.com/[/red]")
            sys.exit(1)
        if not api_key.startswith("sk-"):
            console.print("[yellow]⚠ That doesn't look like an Anthropic key (expected sk-…). Continuing anyway.[/yellow]")
    elif auth_mode == "claude-code":
        api_key = ""  # not needed; clear any stale value

    # Exam date
    while True:
        exam_date = Prompt.ask(
            "[bold][2/6] Exam date[/bold] [dim](YYYY-MM-DD, e.g. 2026-05-10)[/dim]",
            default=defaults.get("exam_date", ""),
        )
        try:
            d = datetime.strptime(exam_date, "%Y-%m-%d").date()
            if d < date.today():
                console.print("[yellow]⚠ Date is in the past.[/yellow]")
                continue
            if d == date.today():
                console.print("[yellow]⚠ Exam is today — nothing to plan.[/yellow]")
                sys.exit(1)
            break
        except ValueError:
            console.print("[yellow]⚠ Format must be YYYY-MM-DD.[/yellow]")

    days_until = (datetime.strptime(exam_date, "%Y-%m-%d").date() - date.today()).days
    console.print(f"  → [green]{days_until} days[/green] until exam.\n")

    # Subject
    subject = Prompt.ask(
        "[bold][3/6] Course / subject[/bold] [dim](e.g. 'Intro to Microeconomics' or 'Organic Chem II')[/dim]",
        default=defaults.get("subject", ""),
    )

    # Student level
    level = Prompt.ask(
        "[bold][4/6] Level[/bold]",
        choices=["undergraduate", "graduate", "aplevel", "professional"],
        default=defaults.get("student_level", "undergraduate"),
    )

    # Style
    style = Prompt.ask(
        "[bold][5/6] Voice[/bold] [dim](how should bart write?)[/dim]",
        choices=["academic-rigorous", "conversational", "minimal"],
        default=defaults.get("style", "academic-rigorous"),
    )

    # Daily hours
    while True:
        try:
            daily_hours = float(
                Prompt.ask(
                    "[bold][6/6] Daily study hours available[/bold]",
                    default=str(defaults.get("daily_hours", 3.0)),
                )
            )
            if daily_hours <= 0 or daily_hours > 16:
                raise ValueError
            break
        except ValueError:
            console.print("[yellow]⚠ Enter a number between 0 and 16.[/yellow]")

    # Guidance — optional, multiline
    console.print(
        "\n[bold]Extra guidance[/bold] [dim](optional — weak areas, focus, format preferences)[/dim]"
    )
    console.print("[dim]Type as many lines as you want. Press ENTER on a blank line to finish, or just hit ENTER to skip.[/dim]")
    lines: list[str] = []
    first = True
    while True:
        line = input("  > ")
        if line == "":
            break
        first = False
        lines.append(line)
    guidance = "\n".join(lines).strip() or "(no extra guidance)"

    cfg = Config(
        auth_mode=auth_mode,
        api_key=api_key,
        exam_date=exam_date,
        subject=subject,
        guidance=guidance,
        student_level=level,
        style=style,
        daily_hours=daily_hours,
        primary_model=defaults.get("primary_model", "claude-opus-4-7"),
        fast_model=defaults.get("fast_model", "claude-haiku-4-5-20251001"),
    )
    save_config(cfg)
    console.print("\n[green]✓[/green] config saved to [cyan].bart_config.json[/cyan]\n")
    return cfg
