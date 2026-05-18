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

# Per-process config path. Defaults to ROOT/.bart_config.json (the CLI's
# single-user layout). The website server overrides this via BART_CONFIG so
# concurrent runs don't trample each other's config.
CONFIG_PATH = (
    Path(os.environ["BART_CONFIG"]).resolve()
    if os.environ.get("BART_CONFIG")
    else ROOT / ".bart_config.json"
)


class Config(BaseModel):
    # auth_mode "local" runs an open-weight model (Qwen3 family) via mlx-lm
    # on Apple Silicon or llama-cpp-python elsewhere. The legacy
    # "ollama-local" Gemma path was removed — old configs auto-migrate via
    # `load_config()` below.
    auth_mode: str = "api"  # "api" | "claude-code" | "local"
    api_key: str = ""        # required when auth_mode == "api"
    exam_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    subject: str = Field(min_length=1, max_length=200)
    guidance: str = ""
    student_level: str = "undergraduate"  # undergraduate | graduate | aplevel | professional
    style: str = "academic-rigorous"  # academic-rigorous | conversational | minimal
    daily_hours: float = 3.0
    primary_model: str = "claude-opus-4-7"
    daily_model: str = "claude-sonnet-4-6"
    fast_model: str = "claude-haiku-4-5-20251001"
    deep_research: bool = False
    # auth_mode == "local" only — the model_key from bart.local_runtime.models.
    # Empty string means "auto-detect at run start". Stored so successive runs
    # don't re-pick (and re-download) just because the user has freed memory
    # since first setup.
    local_model_key: str = ""

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
        if v not in ("api", "claude-code", "local"):
            raise ValueError(
                "auth_mode must be 'api' | 'claude-code' | 'local'"
            )
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
    except json.JSONDecodeError:
        return None
    # Migrate ollama-local configs from older builds. The Gemma+Ollama path
    # is gone; flip them to "local" with an empty model_key so the next
    # `./run` re-detects hardware and picks an appropriate Qwen3 variant.
    if data.get("auth_mode") == "ollama-local":
        data["auth_mode"] = "local"
        data["local_model_key"] = ""
        # Clear the legacy gemma3/gemma4 tags from primary/fast — they
        # reference Ollama-only model names that no longer mean anything.
        for key in ("primary_model", "fast_model"):
            if str(data.get(key, "")).startswith(("gemma3:", "gemma4:")):
                data[key] = ""
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2))
        except OSError:
            pass
    try:
        return Config(**data)
    except ValidationError:
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
    elif not force and saved_mode == "local":
        if Confirm.ask("[1/6] Use saved auth mode (local open-weight model)?", default=True):
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
            f"  [{ACCENT}]{choice_num}[/{ACCENT}]  Local open-weight model "
            f"[dim](free, offline; Qwen3 via MLX on Apple Silicon or "
            f"llama.cpp elsewhere; bart auto-installs everything)[/dim]"
        )
        choice_map[str(choice_num)] = "local"

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

    # ─── 1b/6  Local-mode setup ───
    local_primary = defaults.get("primary_model", "claude-opus-4-7")
    local_fast = defaults.get("fast_model", "claude-haiku-4-5-20251001")
    local_model_key = defaults.get("local_model_key", "")
    if auth_mode == "local":
        api_key = ""  # not used in local mode
        from .local_runtime.hardware import detect, recommended_tier
        from .local_runtime.models import pick

        platform = detect()
        tier = recommended_tier(platform)
        model = pick(platform, tier)

        accel_label = {
            "metal": "Apple Silicon (Metal)",
            "cuda":  "NVIDIA GPU (CUDA)",
            "cpu":   "CPU only",
        }.get(platform.accelerator, platform.accelerator)
        console.print(
            f"\n  detected [cyan]{accel_label}[/cyan], "
            f"[cyan]{platform.usable_gb:.0f} GB[/cyan] usable memory"
        )
        console.print(
            f"  → preselecting [bold]{model.display_name}[/bold] "
            f"[dim]({model.bytes_on_disk/1024**3:.1f} GB on disk, "
            f"{model.context_window//1024}K context)[/dim]"
        )
        if platform.accelerator == "cpu":
            console.print(
                "\n  [yellow]⚠ no GPU/Metal detected — generation will be "
                "slow (~1-3 tok/s on big models). Daily lessons may take "
                "20-40 minutes each. Consider switching to API or "
                "subscription mode if generation time matters.[/yellow]"
            )

        if Confirm.ask("\n  use this preselection?", default=True):
            local_model_key = model.key
        else:
            console.print(
                "\n  available variants:\n"
                "    [bold]qwen3-32b-mlx-4bit[/bold]      ~18 GB  (huge tier, ≥22 GB)\n"
                "    [bold]qwen3-30b-a3b-mlx-4bit[/bold]  ~17 GB  (large MoE, ≥16 GB, 3.3B active — fast)\n"
                "    [bold]qwen3-14b-mlx-4bit[/bold]      ~8.5 GB (mid tier, ≥11 GB)\n"
                "    [bold]qwen3-8b-mlx-4bit[/bold]       ~4.6 GB (small tier, ≥7 GB)\n"
                "    [bold]qwen3-4b-mlx-4bit[/bold]       ~2.4 GB (tiny tier, ≥4 GB)\n"
                "  [dim]on non-Apple platforms swap `mlx-4bit` → `gguf-q4km`[/dim]\n"
            )
            picked = Prompt.ask(
                "  model_key", default=model.key,
            )
            local_model_key = picked.strip() or model.key

        # Set primary/daily/fast all to the same local key — the local
        # backend ignores the `model` arg and uses the runtime-bound model
        # for every call. Storing the key here is informational.
        local_primary = local_model_key
        local_fast = local_model_key

        console.print(
            f"\n  [dim]bart will install the inference engine and download "
            f"weights on first run (~{model.bytes_on_disk/1024**3:.1f} GB; "
            f"cached 24h between runs).[/dim]"
        )
        console.print(
            f"  [dim]quality note: Qwen3 has native tool calling and JSON "
            f"mode, so structured artifacts (problem index, exam pattern, "
            f"diagrams) are dramatically more reliable than the legacy "
            f"Gemma path.[/dim]\n"
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

    if auth_mode == "local":
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
        local_model_key=local_model_key if auth_mode == "local" else "",
    )
    save_config(cfg)
    console.print("\n[green]✓[/green] config saved to [cyan].bart_config.json[/cyan]\n")
    return cfg
