"""Configuration management — wizard, file storage, validation."""
from __future__ import annotations

import json
import os
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
    api_key: str = Field(min_length=10, description="Anthropic API key (sk-ant-…)")
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

    # API key
    api_key = defaults.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key and not force:
        if Confirm.ask("[1/6] Use saved Anthropic API key?", default=True):
            pass
        else:
            api_key = ""
    if not api_key:
        console.print(
            "[dim]Need a key? Grab one at [cyan]https://console.anthropic.com[/cyan] (free credits on signup).[/dim]"
        )
        api_key = Prompt.ask(
            "[bold][1/6] Anthropic API key[/bold] [dim](sk-ant-…)[/dim]",
            password=True,
        )
        if not api_key.strip():
            console.print("[red]✗ API key required. Get one at https://console.anthropic.com/[/red]")
            sys.exit(1)
        if not api_key.startswith("sk-"):
            console.print("[yellow]⚠ That doesn't look like an Anthropic key (expected sk-…). Continuing anyway.[/yellow]")

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
