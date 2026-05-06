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
    auth_mode: str = "api"  # "api" | "claude-code" | "ollama-local"
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
        if v not in ("api", "claude-code", "ollama-local"):
            raise ValueError("auth_mode must be 'api' | 'claude-code' | 'ollama-local'")
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
    elif not force and saved_mode == "ollama-local":
        if Confirm.ask("[1/6] Use saved auth mode (local Gemma 4 via Ollama)?", default=True):
            pass
        else:
            auth_mode = ""
    else:
        auth_mode = ""

    if not auth_mode:
        # Offer the full choice. Order: subscription → API → local.
        # Show option 1 only when the CLI is detected; options 2 & 3 always
        # available (no preconditions on local mode itself — install runs
        # later if needed).
        choices_lines = []
        choice_num = 1
        choice_map: dict[str, str] = {}
        if claude_cli_present:
            choices_lines.append(
                f"  [{ACCENT}]{choice_num}[/{ACCENT}]  Claude Code subscription "
                f"[dim](use your existing claude.ai login — best quality if you have Pro/Max/Team)[/dim]"
            )
            choice_map[str(choice_num)] = "claude-code"
            choice_num += 1
        choices_lines.append(
            f"  [{ACCENT}]{choice_num}[/{ACCENT}]  Anthropic API key      "
            f"[dim](pay-per-token, supports prompt caching + cost tracking)[/dim]"
        )
        choice_map[str(choice_num)] = "api"
        choice_num += 1
        choices_lines.append(
            f"  [{ACCENT}]{choice_num}[/{ACCENT}]  Local Gemma 4 (Ollama) "
            f"[dim](free, runs offline; ~Sonnet-class quality, not Opus)[/dim]"
        )
        choice_map[str(choice_num)] = "ollama-local"

        console.print(
            "\n[bold][1/6] How do you want bart to talk to a model?[/bold]\n"
            + "\n".join(choices_lines)
        )
        default_choice = "1" if claude_cli_present else "1"
        choice = Prompt.ask(
            "  pick", choices=list(choice_map.keys()), default=default_choice,
        )
        auth_mode = choice_map[choice]

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

    # ─── 1b/6  Local-mode setup (Ollama detect, install, model tier pick) ───
    local_primary = defaults.get("primary_model", "claude-opus-4-7")
    local_fast = defaults.get("fast_model", "claude-haiku-4-5-20251001")
    if auth_mode == "ollama-local":
        api_key = ""  # not used in local mode
        from . import local_setup as _ls
        if not _ls.ollama_installed():
            console.print(
                "\n[yellow]ollama isn't installed.[/yellow] bart can install it for you, "
                "or you can install it yourself.\n"
            )
            ok = _ls.install_with_consent(
                lambda q: Confirm.ask(f"[bold]{q}[/bold]", default=True)
            )
            if not ok:
                console.print(
                    "\n[dim]skipping auto-install. install ollama yourself, then re-run "
                    "[white]./run setup[/white] and pick local mode again.[/dim]\n"
                )
                console.print(_ls.manual_install_message())
                sys.exit(1)
            console.print("  [green]✓[/green] ollama installed")

        mem_gb, source = _ls.probe_memory_gb()
        primary, fast, label = _ls.pick_tier(mem_gb)
        cpu_warn = ""
        if not _ls.has_gpu_or_unified():
            cpu_warn = (
                "\n  [yellow]⚠ no GPU detected — generation will run on CPU "
                "and may be very slow (~1 token/sec on big models). "
                "we'll pre-pick the 1B variant to keep things usable.[/yellow]"
            )
            primary, fast, label = "gemma4:1b", "gemma4:1b", "1B (CPU-only fallback)"
        console.print(
            f"\n  detected [cyan]{mem_gb:.1f} GB[/cyan] available {source} "
            f"memory → preselecting [bold]{label}[/bold]"
            + cpu_warn
        )
        if Confirm.ask("\n  use this preselection?", default=True):
            local_primary = primary
            local_fast = fast
        else:
            console.print(
                "\n  available variants:\n"
                "    [bold]gemma4:27b[/bold]  ~40GB unified/VRAM\n"
                "    [bold]gemma4:12b[/bold]  ~18GB\n"
                "    [bold]gemma4:4b[/bold]   ~6GB\n"
                "    [bold]gemma4:1b[/bold]   ~2GB (works on CPU)\n"
            )
            local_primary = Prompt.ask(
                "  primary model (long-form Author)",
                default=primary,
            )
            local_fast = Prompt.ask(
                "  fast model (Distiller / Researcher / sidecars)",
                default=fast,
            )
        console.print(
            f"\n  [dim]models will be pulled at run start (10–30 min on first run; "
            f"cached for 24 hours so back-to-back runs are fast).[/dim]"
        )
        console.print(
            f"  [dim]quality note: local Gemma 4 produces ~Sonnet-class lessons. "
            f"For Opus-class quality on long structured artifacts, switch to "
            f"`./run setup` and pick option 1 or 2.[/dim]\n"
        )

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

    if auth_mode == "ollama-local":
        primary_model_for_cfg = local_primary
        fast_model_for_cfg = local_fast
    else:
        primary_model_for_cfg = defaults.get("primary_model", "claude-opus-4-7")
        fast_model_for_cfg = defaults.get("fast_model", "claude-haiku-4-5-20251001")
    cfg = Config(
        auth_mode=auth_mode,
        api_key=api_key,
        exam_date=exam_date,
        subject=subject,
        guidance=guidance,
        student_level=level,
        style=style,
        daily_hours=daily_hours,
        primary_model=primary_model_for_cfg,
        fast_model=fast_model_for_cfg,
    )
    save_config(cfg)
    console.print("\n[green]✓[/green] config saved to [cyan].bart_config.json[/cyan]\n")
    return cfg
