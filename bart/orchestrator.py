"""Orchestrator — wires agents together into a full study-packet generation run.

Pipeline overview:

  ┌─────────────┐
  │  Materials  │
  └──────┬──────┘
         │  extract + assemble
  ┌──────▼──────┐
  │   Corpus    │  (cached as a system-cache block)
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │   Planner   │  → master plan + per-day topic JSON
  └──────┬──────┘
         │
         │  ┌──── Researcher (per-day grounding, fast model)
         │  │
  ┌──────▼──▼───┐
  │   Author    │  → daily lessons + schematics + whimsy + short-guide + practice exam
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │   Critic    │  → score + must-fix items
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │   Reviser   │  → revised artifact (only if critic.should_revise)
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │   Output    │  manifest.json, artifacts, telemetry
  └─────────────┘
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .agents import AuthorAgent, CriticAgent, PlannerAgent, ResearcherAgent, ReviserAgent
from .agents.base import AgentContext
from .branding import ACCENT, ACCENT_HI, ACCENT_LO, CREAM_LO, INK, RICH_DIM, RICH_OK
from .config import Config
from .io.checkpoint import atomic_write_json, atomic_write_text, is_complete
from .io.corpus import build_corpus, extract_all
from .llm import LLMClient
from .paths import RunPaths
from .telemetry import Telemetry


def setup_run_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"bart.run.{log_path.parent.name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    return logger


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        paths: RunPaths,
        console: Console,
        dry_run: bool = False,
        use_critic: bool = True,
        max_parallel: int = 4,
        days_override: int | None = None,
    ):
        self.cfg = cfg
        self.paths = paths
        self.console = console
        self.dry_run = dry_run
        self.use_critic = use_critic
        self.max_parallel = max_parallel
        self.days_override = days_override
        self.telemetry = Telemetry()
        self.logger = setup_run_logger(paths.log_path)

    # ------------------------------------------------------------------
    def run(self) -> int:
        try:
            self._print_header()
            kept, skipped = self._extract()
            corpus = build_corpus(kept, skipped)
            self._print_corpus_summary(corpus, skipped)

            if self.dry_run:
                self.console.print("[yellow]Dry run — stopping before API calls.[/yellow]")
                return 0

            days_until = self.days_override or self.cfg.days_until
            if not self._confirm_cost(corpus.total_chars, days_until):
                self.console.print("[yellow]Cancelled.[/yellow] [dim]Run preserved at[/dim] "
                                   f"[cyan]{self.paths.root}[/cyan]")
                return 0

            llm = LLMClient(
                api_key=self.cfg.api_key,
                telemetry=self.telemetry,
                cache_dir=self.paths.cache_dir,
                on_event=self._on_llm_event,
            )

            corpus_block = self._make_corpus_block(corpus.body)
            ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=corpus_block)
            planner = PlannerAgent(ctx)
            researcher = ResearcherAgent(ctx)
            author = AuthorAgent(ctx)
            critic = CriticAgent(ctx)
            reviser = ReviserAgent(ctx)

            today = date.today()
            today_iso = today.isoformat()

            # ----- 1. Plan
            corpus_summary = "\n".join(f"- {ef.rel_path} ({ef.char_count:,} chars)" for ef in corpus.files)
            master_plan_path = self.paths.root / "00_MASTER_PLAN.md"
            day_plan_json = self.paths.checkpoints_dir / "day_plan.json"

            if is_complete(master_plan_path) and day_plan_json.exists():
                self.console.print("[dim]✓ master plan checkpoint found — reusing[/dim]")
                master_plan = master_plan_path.read_text()
                day_entries = json.loads(day_plan_json.read_text())
            else:
                self.console.print("\n[bold #c96442]▸Planning[/bold #c96442]")
                with self._spinner("planner"):
                    master_plan, day_entries = planner.plan(days_until, today_iso, corpus_summary)
                atomic_write_text(master_plan_path, master_plan)
                atomic_write_json(day_plan_json, day_entries)

            # ----- 2. Top-level artifacts in parallel
            self.console.print("\n[bold #c96442]▸Generating top-level artifacts[/bold #c96442]")
            artifacts = [
                ("schematics", "01_SCHEMATICS.md", _schematics_brief, 12000),
                ("whimsical_notes", "02_WHIMSICAL_NOTES.md", _whimsy_brief, 12000),
                ("short_study_guide", "03_SHORT_STUDY_GUIDE.md", _short_guide_brief, 8000),
                ("practice_exam", "04_PRACTICE_EXAM.md", _practice_exam_brief, 16000),
            ]
            self._run_artifacts_parallel(artifacts, author, critic, reviser, master_plan)

            # ----- 3. Daily lessons (researcher → author → critic → reviser)
            self.console.print("\n[bold #c96442]▸Generating daily lessons[/bold #c96442]")
            self._run_daily_lessons(day_entries, researcher, author, critic, reviser, master_plan)

            # ----- 4. Manifest + telemetry + summary
            self._write_manifest(corpus, day_entries, master_plan_path)
            self.telemetry.write(self.paths.root / "telemetry.json")

            self._print_summary()
            return 0

        except KeyboardInterrupt:
            self.console.print("\n[red]✗ Interrupted. Run is preserved — re-run with --resume[/red] "
                               f"[cyan]{self.paths.run_id}[/cyan] [red]to continue.[/red]")
            return 130
        except Exception as e:
            self.logger.error("Orchestrator failed: %s\n%s", e, traceback.format_exc())
            self.console.print(f"\n[red]✗ Run failed: {e}[/red]")
            self.console.print(f"[dim]See {self.paths.log_path} for details.[/dim]")
            return 1

    # ------------------------------------------------------------------
    def _extract(self):
        self.console.print("[bold #c96442]▸Extracting materials[/bold #c96442]")
        kept, skipped = extract_all(console=self.console)
        return kept, skipped

    def _print_header(self):
        days = self.days_override or self.cfg.days_until
        self.console.print(
            Panel.fit(
                f"[bold]bart[/bold][bold {ACCENT}].[/bold {ACCENT}]   run [{ACCENT_LO}]{self.paths.run_id}[/{ACCENT_LO}]\n"
                f"subject: [white]{self.cfg.subject}[/white]\n"
                f"exam:    [white]{self.cfg.exam_date}[/white]  ([{RICH_OK}]{days}[/{RICH_OK}] days away)\n"
                f"level:   [white]{self.cfg.student_level}[/white]    style: [white]{self.cfg.style}[/white]\n"
                f"primary: [white]{self.cfg.primary_model}[/white]    fast:  [white]{self.cfg.fast_model}[/white]",
                border_style=ACCENT,
            )
        )

    def _print_corpus_summary(self, corpus, skipped):
        self.console.print(
            f"[dim]  → corpus: [cyan]{len(corpus.files)}[/cyan] files, "
            f"[cyan]{corpus.total_chars:,}[/cyan] chars; "
            f"[yellow]{len(skipped)}[/yellow] skipped[/dim]\n"
        )

    def _estimate_cost(self, corpus_chars: int, days: int) -> float:
        """Rough USD cost estimate before the run starts."""
        # Each artifact reads the corpus once. Cache means subsequent reads are 90% cheaper.
        # 4 top-level artifacts + days lessons + planner + (researchers via fast model).
        n_calls = 1 + 4 + days  # primary calls
        # Approx tokens: corpus is ~corpus_chars/4 tokens. First call writes cache, rest read.
        corpus_tokens = corpus_chars // 4
        cache_write_tokens = corpus_tokens
        cache_read_tokens = corpus_tokens * (n_calls - 1)
        output_tokens = n_calls * 8000  # conservative
        # Critic uses fast model, doesn't read corpus.
        critic_input = n_calls * 4000  # artifact text
        critic_output = n_calls * 800
        # Researcher (fast model) reads corpus per day (cached).
        researcher_input = days * 1000
        researcher_output = days * 1500

        from .telemetry import PRICING
        opus = PRICING.get(self.cfg.primary_model, PRICING["claude-opus-4-7"])
        haiku = PRICING.get(self.cfg.fast_model, PRICING["claude-haiku-4-5-20251001"])

        cost = (
            cache_write_tokens * opus["cache_write"]
            + cache_read_tokens * opus["cache_read"]
            + output_tokens * opus["output"]
            + critic_input * haiku["input"]
            + critic_output * haiku["output"]
            + researcher_input * haiku["cache_read"]
            + researcher_output * haiku["output"]
        ) / 1_000_000.0
        if self.use_critic:
            cost *= 1.4  # critic + revision overhead
        return cost

    def _confirm_cost(self, corpus_chars: int, days: int) -> bool:
        """Show estimated cost + time and ask for confirmation. Auto-yes if non-interactive."""
        estimate = self._estimate_cost(corpus_chars, days)
        total_artifacts = 4 + days
        eta_min = max(2, int(total_artifacts * 0.4 / max(self.max_parallel, 1) + 1.5))

        from rich.prompt import Confirm
        self.console.print(
            Panel.fit(
                f"[bold]ready to generate[/bold]\n\n"
                f"  artifacts:  [{ACCENT_HI}]{total_artifacts}[/{ACCENT_HI}]  "
                f"([{ACCENT_HI}]4[/{ACCENT_HI}] top-level + [{ACCENT_HI}]{days}[/{ACCENT_HI}] daily lessons)\n"
                f"  est. time:  [{ACCENT_HI}]~{eta_min} min[/{ACCENT_HI}]\n"
                f"  est. cost:  [{ACCENT_HI}]~${estimate:.2f}[/{ACCENT_HI}]"
                f"{' [dim](critic+revise enabled)[/dim]' if self.use_critic else ' [dim](critic disabled)[/dim]'}\n\n"
                f"[dim]final cost depends on response lengths and cache hits.[/dim]",
                border_style=ACCENT,
            )
        )
        # Skip confirmation if stdin isn't a tty (CI, piped, etc.)
        import sys as _sys
        if not _sys.stdin.isatty():
            return True
        return Confirm.ask("\nproceed?", default=True)

    def _make_corpus_block(self, corpus: str) -> list[dict]:
        return [{
            "type": "text",
            "text": "USER'S COURSE MATERIALS — primary source of truth for all generations:\n\n" + corpus,
            "cache_control": {"type": "ephemeral"},
        }]

    # ------------------------------------------------------------------
    def _run_artifacts_parallel(self, artifacts, author, critic, reviser, master_plan):
        results: dict[str, str] = {}

        def _gen_one(kind, filename, brief_fn, max_tokens):
            target = self.paths.root / filename
            if is_complete(target):
                self.logger.info("artifact %s already complete — skipping", filename)
                return kind, target.read_text()
            brief = brief_fn(self.cfg, master_plan)
            text = author.write(kind, brief, max_tokens=max_tokens, label_suffix="initial")
            if self.use_critic:
                cri = critic.critique(kind, text, brief)
                self.logger.info("critic %s: score=%d, must_fix=%d", kind, cri.score, len(cri.must_fix))
                if cri.should_revise:
                    text = reviser.revise(kind, text, cri, brief, max_tokens=max_tokens)
            atomic_write_text(target, text)
            return kind, text

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool, \
             self._artifact_progress(len(artifacts)) as progress:
            tasks = {
                pool.submit(_gen_one, kind, filename, brief_fn, max_tokens): (kind, filename)
                for kind, filename, brief_fn, max_tokens in artifacts
            }
            task_id = progress.add_task("artifacts", total=len(artifacts))
            for fut in as_completed(tasks):
                kind, filename = tasks[fut]
                try:
                    k, txt = fut.result()
                    results[k] = txt
                    self.console.print(f"  [green]✓[/green] {filename}")
                except Exception as e:  # noqa: BLE001
                    self.logger.error("artifact %s failed: %s", kind, e)
                    self.console.print(f"  [red]✗[/red] {filename} — {e}")
                progress.advance(task_id)

    def _run_daily_lessons(self, day_entries, researcher, author, critic, reviser, master_plan):
        if not day_entries:
            self.console.print("[yellow]  ⚠ Planner produced no day entries — skipping daily lessons.[/yellow]")
            return

        def _gen_day(entry):
            day_num = entry["day"]
            day_date = entry.get("date", "")
            topic = entry.get("topic", f"Day {day_num}")
            objectives = entry.get("learning_objectives", [])
            filename = f"Day_{day_num:02d}_{day_date}.md"
            target = self.paths.daily_dir / filename
            if is_complete(target, min_chars=2000):
                self.logger.info("day %s already complete — skipping", day_num)
                return day_num, filename, "cached"

            # Research with fast model
            research = researcher.research(topic, objectives)
            (self.paths.checkpoints_dir / f"research_day_{day_num:02d}.md").write_text(research)

            brief = _daily_lesson_brief(self.cfg, day_num, day_date, entry, master_plan, research)
            text = author.write("daily_lesson", brief, max_tokens=16000, label_suffix=f"day{day_num}")
            if self.use_critic:
                cri = critic.critique("daily_lesson", text, brief)
                self.logger.info("critic day %d: score=%d", day_num, cri.score)
                if cri.should_revise:
                    text = reviser.revise("daily_lesson", text, cri, brief, max_tokens=16000)
            atomic_write_text(target, text)
            return day_num, filename, "written"

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool, \
             self._artifact_progress(len(day_entries)) as progress:
            tasks = {pool.submit(_gen_day, e): e for e in day_entries}
            task_id = progress.add_task("daily lessons", total=len(day_entries))
            for fut in as_completed(tasks):
                entry = tasks[fut]
                try:
                    day_num, filename, status = fut.result()
                    icon = "[dim]●[/dim]" if status == "cached" else "[green]✓[/green]"
                    self.console.print(f"  {icon} Day {day_num:02d} — {filename}")
                except Exception as e:  # noqa: BLE001
                    self.logger.error("day %s failed: %s\n%s", entry.get("day"), e, traceback.format_exc())
                    self.console.print(f"  [red]✗[/red] Day {entry.get('day')} — {e}")
                progress.advance(task_id)

    # ------------------------------------------------------------------
    def _write_manifest(self, corpus, day_entries, master_plan_path):
        manifest = {
            "run_id": self.paths.run_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "config": self.cfg.model_dump(exclude={"api_key"}),
            "corpus": {
                "total_chars": corpus.total_chars,
                "files": [{"path": f.rel_path, "chars": f.char_count} for f in corpus.files],
                "skipped": [{"path": f.rel_path, "reason": f.skip_reason} for f in corpus.skipped_files],
            },
            "artifacts": [
                "00_MASTER_PLAN.md",
                "01_SCHEMATICS.md",
                "02_WHIMSICAL_NOTES.md",
                "03_SHORT_STUDY_GUIDE.md",
                "04_PRACTICE_EXAM.md",
            ],
            "daily_lessons": [
                {"day": e.get("day"), "date": e.get("date"), "topic": e.get("topic"), "focus": e.get("focus")}
                for e in day_entries
            ],
            "telemetry_summary": self.telemetry.summary(),
        }
        atomic_write_json(self.paths.manifest_path, manifest)

        # Drop the loaf SVG and wordmark into the run directory as well so the
        # output packet feels branded in isolation.
        try:
            from .paths import ROOT
            assets_src = ROOT / "assets"
            run_assets = self.paths.root / "assets"
            run_assets.mkdir(exist_ok=True)
            for fname in ("bart-loaf.svg", "bart-wordmark.svg", "bart-loaf.txt"):
                if (assets_src / fname).exists():
                    (run_assets / fname).write_bytes((assets_src / fname).read_bytes())
        except Exception:  # noqa: BLE001
            pass

        readme_lines = [
            "<table border=\"0\" cellpadding=\"0\" cellspacing=\"0\">",
            "  <tr>",
            "    <td valign=\"middle\" width=\"140\">",
            "      <img src=\"assets/bart-loaf.svg\" alt=\"bart\" width=\"120\" />",
            "    </td>",
            "    <td valign=\"middle\">",
            "      <img src=\"assets/bart-wordmark.svg\" alt=\"bart.\" height=\"60\" /><br/>",
            f"      <sub><code>{self.paths.run_id} · {self.cfg.subject}</code></sub>",
            "    </td>",
            "  </tr>",
            "</table>",
            "",
            f"# {self.cfg.subject} — Study Packet",
            f"_Generated {manifest['generated_at']} (run `{self.paths.run_id}`)_",
            f"_Exam: {self.cfg.exam_date}_",
            "",
            "## Top-level artifacts",
            "- `00_MASTER_PLAN.md` — exam scope, day-by-day allocation, pacing strategy",
            "- `01_SCHEMATICS.md` — concept maps, ASCII diagrams, formula tables",
            "- `02_WHIMSICAL_NOTES.md` — analogies, mnemonics, sticky stuff",
            "- `03_SHORT_STUDY_GUIDE.md` — the 60-minute panoramic version",
            "- `04_PRACTICE_EXAM.md` — full practice exam + answer key",
            "",
            "## Daily lessons",
        ]
        for e in day_entries:
            readme_lines.append(f"- `daily_lessons/Day_{e.get('day'):02d}_{e.get('date')}.md` — {e.get('topic')}")
        readme_lines.extend([
            "",
            "## Run telemetry",
            f"- Total API calls: {manifest['telemetry_summary']['calls']}",
            f"- Estimated cost: ${manifest['telemetry_summary']['cost_usd']:.2f}",
            f"- Total time: {manifest['telemetry_summary']['duration_s']:.1f}s",
            "",
            "## Materials used",
            *(f"- {f['path']} ({f['chars']:,} chars)" for f in manifest['corpus']['files']),
        ])
        if manifest["corpus"]["skipped"]:
            readme_lines.extend(["", "## Skipped"])
            readme_lines.extend(f"- {f['path']} — {f['reason']}" for f in manifest['corpus']['skipped'])
        atomic_write_text(self.paths.root / "README.md", "\n".join(readme_lines))

    def _print_summary(self):
        import subprocess
        import sys as _sys

        s = self.telemetry.summary()
        table = Table(title="🎉 Done!", border_style="green")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white", justify="right")
        table.add_row("API calls", str(s["calls"]))
        table.add_row("Input tokens", f"{s['input_tokens']:,}")
        table.add_row("Output tokens", f"{s['output_tokens']:,}")
        table.add_row("Cache reads (cheap!)", f"{s['cache_read_input_tokens']:,}")
        table.add_row("Cache writes", f"{s['cache_creation_input_tokens']:,}")
        table.add_row("Wall time", f"{s['duration_s']:.1f}s")
        table.add_row("[bold]Estimated cost[/bold]", f"[bold]${s['cost_usd']:.2f}[/bold]")
        self.console.print()
        self.console.print(table)

        rel = self.paths.root.relative_to(self.paths.root.parent.parent)
        readme = self.paths.root / "README.md"
        self.console.print(
            Panel.fit(
                f"[bold]your study packet is ready[/bold][bold {ACCENT}].[/bold {ACCENT}]\n\n"
                f"📂  [{ACCENT_HI}]{rel}[/{ACCENT_HI}]\n"
                f"📕  start here:    [white]{readme.relative_to(self.paths.root.parent.parent)}[/white]\n"
                f"🗺️   master plan:   [white]{rel}/00_MASTER_PLAN.md[/white]\n"
                f"📝  practice exam: [white]{rel}/04_PRACTICE_EXAM.md[/white]\n\n"
                f"[dim]tip: open the folder and start with day 1 of daily_lessons/[/dim]",
                border_style=ACCENT,
            )
        )

        # Try to open the output folder for the user (mac/linux only — silent fail on others)
        try:
            if _sys.platform == "darwin":
                subprocess.Popen(["open", str(self.paths.root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif _sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(self.paths.root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass

        self.console.print()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _on_llm_event(self, event: str, payload: dict):
        self.logger.debug("llm.%s %s", event, payload)

    def _spinner(self, label: str):
        return self.console.status(f"[cyan]{label}…[/cyan]", spinner="dots")

    def _artifact_progress(self, total: int):
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )


# ─────────────────────────────────────────────────────────────────────
# Brief builders — pure functions producing the brief string for each artifact
# ─────────────────────────────────────────────────────────────────────

def _schematics_brief(cfg: Config, master_plan: str) -> str:
    return (
        f"ARTIFACT: SCHEMATICS — visual / structural reference for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:3000]}\n\n"
        "Required content:\n"
        "1. **Concept map** — ASCII diagram showing how the major topics connect.\n"
        "2. **Per-topic schematics** — for each major topic, ASCII diagrams capturing the structure "
        "of that topic (whatever form fits the subject — flowcharts, hierarchies, comparison "
        "matrices, decision trees, sequence diagrams, etc.).\n"
        "3. **Formula tables** — every key formula, organized by topic, with a one-line meaning.\n"
        "4. **Property tables** — symmetries, identities, when-to-use guides.\n"
        "5. **Common traps** — numbered list of 'if you see X, watch out for Y' items grounded in the materials.\n\n"
        "Format: dense, scannable, exam-day useful. Aim for 600-1000 lines."
    )


def _whimsy_brief(cfg: Config, master_plan: str) -> str:
    return (
        f"ARTIFACT: WHIMSICAL NOTES — memorable companion to the serious lessons for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:2000]}\n\n"
        "For each major topic in the materials, include:\n"
        "1. A silly analogy that makes the concept stick.\n"
        "2. A mnemonic for any list/sequence to memorize.\n"
        "3. A two-line poem or jingle when it fits naturally.\n"
        "4. A memorable visual (ASCII art, even absurd).\n\n"
        "Whimsy must AID the math, not replace it. Each topic gets ~150-250 words. Aim for 1200-2000 lines total."
    )


def _short_guide_brief(cfg: Config, master_plan: str) -> str:
    return (
        f"ARTIFACT: SHORT STUDY GUIDE — the document to read when there are 60 minutes left for {cfg.subject}.\n\n"
        "Required:\n"
        "1. The 20 facts that matter most — numbered, one line each.\n"
        "2. The 5 concepts most likely tested — 2 paragraphs each.\n"
        "3. The 5 most common traps with avoidance tips.\n"
        "4. A 10-question rapid-fire quiz with answers.\n\n"
        "Brutal selectivity. Length: 3-5 dense markdown pages."
    )


def _practice_exam_brief(cfg: Config, master_plan: str) -> str:
    return (
        f"ARTIFACT: PRACTICE EXAM + ANSWER KEY for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:3000]}\n\n"
        "Two documents in one markdown file, separated by a horizontal rule:\n\n"
        "# Part A: Practice Exam (clean version)\n"
        "- Match the structure of the user's actual exam if visible (e.g., 3 parts, point distribution).\n"
        "- If unclear, default: Part I (20 short × 1pt), Part II (15 medium × 2pt), Part III (8 long × var).\n"
        "- Every problem is NEW, inspired by — not copied from — the materials.\n"
        "- Cover topics in proportion to estimated weight from the master plan.\n\n"
        "---\n\n"
        "# Part B: Answer Key\n"
        "- Full solutions with work shown for Part III.\n"
        "- Mark common traps and partial-credit opportunities.\n\n"
        "Length: this should be a serious 3-hour exam with a complete answer key. ~2000-3000 lines."
    )


def _daily_lesson_brief(
    cfg: Config,
    day_num: int,
    day_date: str,
    entry: dict[str, Any],
    master_plan: str,
    research: str,
) -> str:
    objectives = "\n".join(f"- {o}" for o in entry.get("learning_objectives", []))
    chapters = ", ".join(entry.get("chapters", []))
    key_problems = ", ".join(entry.get("key_problems", []))
    focus = entry.get("focus", "learn")
    hours = entry.get("hours", cfg.daily_hours)
    return (
        f"ARTIFACT: DAILY LESSON — Day {day_num} of the {cfg.subject} study plan.\n"
        f"Date: {day_date}.   Focus: {focus}.   Hours: {hours}.\n"
        f"Topic: {entry.get('topic', '')}.\n"
        f"Chapters: {chapters}.\n"
        f"Key problems to drill: {key_problems}.\n\n"
        f"LEARNING OBJECTIVES:\n{objectives}\n\n"
        f"RESEARCH BRIEF (corpus-grounded):\n---\n{research}\n---\n\n"
        f"MASTER PLAN EXCERPT (for orientation):\n{master_plan[:2500]}\n\n"
        "Required structure:\n"
        "1. **Why today matters** — 1 paragraph motivation.\n"
        "2. **Recap** — 60-sec flashback to prior days, especially anything load-bearing for today.\n"
        "3. **By the end of today you can** — 5-8 specific objectives.\n"
        "4. **Core content** — explain each subtopic with definitions, derivations, intuition. ASCII diagrams "
        "where they aid understanding.\n"
        "5. **Embedded Quick Check boxes** after each subsection — 2-3 questions with `<details>` collapsibles.\n"
        "6. **Worked examples** — 5-8 fully solved.\n"
        "7. **Mock / past-exam problems** — every problem from the corpus that touches today's topic, "
        "VERBATIM, followed by a fully worked solution in a `<details>` block.\n"
        "8. **Whimsical hook** — 2-3 short analogies / mnemonics for sticky recall.\n"
        "9. **Practice problems** — 8-10 drills with `<details>` solutions.\n"
        "10. **Cheat-sheet candidates** — what to add to the A4 sheet today.\n"
        "11. **End-of-day flashcards** — 10-15 quick-recall items with answers.\n"
        "12. **Tomorrow preview** — 1 short paragraph.\n\n"
        "Length: 800-1500 lines of dense, useful content. Honor the corpus; do not invent past exam problems."
    )
