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

from .agents import (
    AuthorAgent,
    CriticAgent,
    DistillerAgent,
    NotationExtractorAgent,
    PlannerAgent,
    ProblemIndexerAgent,
    ResearcherAgent,
    ReviewerAgent,
    ReviserAgent,
    SolverAgent,
    TopicDistillerAgent,
    WhimsyIndexerAgent,
)
from .agents.base import AgentContext
from .agents.heuristics import health_check
from .agents import block_density
from .agents import problem_indexer as _problem_indexer
from .agents import review_queue as _review_queue
from .agents import whimsy_indexer as _whimsy_indexer
from .branding import ACCENT, ACCENT_HI, ACCENT_LO, CREAM_LO, INK, RICH_DIM, RICH_OK
from .config import Config
from .io.checkpoint import atomic_write_json, atomic_write_text, is_complete
from .io.corpus import build_corpus, extract_all
from .backends import AnthropicAPIBackend, ClaudeCodeBackend
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
            with self._stage("extract"):
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

            if self.cfg.auth_mode == "claude-code":
                llm = ClaudeCodeBackend(
                    telemetry=self.telemetry,
                    cache_dir=self.paths.cache_dir,
                    on_event=self._on_llm_event,
                )
            else:
                llm = AnthropicAPIBackend(
                    api_key=self.cfg.api_key,
                    telemetry=self.telemetry,
                    cache_dir=self.paths.cache_dir,
                    on_event=self._on_llm_event,
                )

            # Warm-up smoke test — one tiny call to fail-fast on auth/model issues
            # before launching the big parallel batch. Costs ~5 tokens; saves
            # potentially minutes of silent waiting if something is wrong.
            self.console.print(
                f"  [{ACCENT_HI}]→[/{ACCENT_HI}] warm-up check "
                f"[dim](single tiny call to confirm model + auth)[/dim]"
            )
            try:
                t0 = time.time()
                _ = llm.complete(
                    model=self.cfg.fast_model,
                    system="You are a test responder. Reply with one word.",
                    user="Say: ok",
                    max_tokens=20,
                    label="warmup",
                    use_disk_cache=False,
                )
                dt = time.time() - t0
                self.console.print(
                    f"  [{RICH_OK}]✓[/{RICH_OK}] warm-up succeeded "
                    f"[dim]({dt:.1f}s · model + auth working)[/dim]"
                )
            except Exception as e:  # noqa: BLE001
                self.console.print(
                    f"  [red]✗[/red] warm-up failed: {type(e).__name__}: {e}\n"
                    f"  [dim]aborting before launching the full batch.[/dim]"
                )
                raise

            # Build the full-corpus context (used only by Distiller + Researcher).
            full_corpus_block = self._make_corpus_block(corpus.body)
            full_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=full_corpus_block)

            # ── Distill the corpus to a tight brief ONCE, with the fast model.
            # All downstream agents (Planner, Author, Reviewer) use the brief
            # in place of the raw corpus — drops input tokens by ~80%.
            self.console.print(
                f"\n  [{ACCENT_HI}]→[/{ACCENT_HI}] distilling corpus → brief "
                f"[dim]({self.cfg.fast_model.split('-')[1] if '-' in self.cfg.fast_model else 'fast'} · "
                f"replaces 100K+ char corpus everywhere downstream)[/dim]"
            )
            brief_path = self.paths.checkpoints_dir / "corpus_brief.md"
            if brief_path.exists() and brief_path.stat().st_size > 500:
                corpus_brief = brief_path.read_text()
                self.console.print(f"  [dim]✓ reused cached corpus brief ({len(corpus_brief):,} chars)[/dim]")
            else:
                distiller = DistillerAgent(full_ctx)
                corpus_brief = distiller.distill()
                brief_path.write_text(corpus_brief)
                self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] corpus brief written ({len(corpus_brief):,} chars)")

            # The brief block is what most agents see going forward.
            brief_block = [{
                "type": "text",
                "text": (
                    "CORPUS BRIEF — a structured digest of the user's course materials, "
                    "use this as the primary source of truth:\n\n" + corpus_brief
                ),
                "cache_control": {"type": "ephemeral"},
            }]

            brief_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=brief_block)
            # Planner is now deterministic — no agent instance needed.
            author = AuthorAgent(brief_ctx)

            # Researcher keeps full corpus access — it's the only agent that needs it.
            researcher = ResearcherAgent(full_ctx)

            # Reviewer fuses critic + reviser into a single corpus-free call.
            reviewer_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=[])
            reviewer = ReviewerAgent(reviewer_ctx)
            # Solver lives in the brief context — it doesn't need the full corpus.
            solver = SolverAgent(brief_ctx)

            # ── Per-run sidecar primitives (notation card, problem index,
            # exam patterns). Three independent corpus reads → run them in
            # parallel for ~3x faster sidecar extraction.
            with ThreadPoolExecutor(max_workers=3) as _sidecar_pool:
                _f_notation = _sidecar_pool.submit(
                    self._extract_notation_card, corpus_brief, brief_ctx,
                )
                _f_problems = _sidecar_pool.submit(
                    self._extract_problem_index, full_ctx,
                )
                _f_exam = _sidecar_pool.submit(
                    self._extract_exam_patterns, full_ctx,
                )
                notation_card = _f_notation.result()
                problem_index = _f_problems.result()
                exam_patterns = _f_exam.result()

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
                # Use the deterministic planner — no LLM call, runs in
                # milliseconds. This single change saves the slowest call
                # in the entire pipeline (was 4-7 min on subscription).
                self.console.print("\n[bold #c96442]▸ Planning[/bold #c96442]")
                from .agents.deterministic_planner import deterministic_plan
                master_plan, day_entries = deterministic_plan(
                    self.cfg, corpus_brief, days_until, today_iso,
                )
                atomic_write_text(master_plan_path, master_plan)
                atomic_write_json(day_plan_json, day_entries)
                self.console.print(
                    f"  [{RICH_OK}]✓[/{RICH_OK}] deterministic plan "
                    f"[dim]({len(day_entries)} day(s) scheduled · no LLM call)[/dim]"
                )

            # ── Spaced-review queue: deterministic, no LLM cost.
            review_queue = _review_queue.build_review_queue(day_entries)
            atomic_write_json(
                self.paths.checkpoints_dir / "review_queue.json",
                {str(k): v for k, v in review_queue.items()},
            )

            # ----- 2. Top-level artifacts in parallel
            self.console.print("\n[bold #c96442]▸ Generating top-level artifacts[/bold #c96442]")
            # Practice exam splits: Author writes Part A (problems only),
            # Solver fills Part B (answer key) — saves the long combined call.
            # max_tokens chosen to be the smallest ceiling that comfortably
            # fits a complete artifact. Sonnet treats max_tokens as a budget
            # to fill, so smaller is dramatically faster. Empirically:
            #   schematics ~6K chars (~2K tokens out)
            #   whimsy ~5K chars
            #   short guide ~6K chars
            #   practice exam Part A ~8K chars (Solver fills Part B separately)
            artifacts = [
                ("schematics", "01_SCHEMATICS.md", _schematics_brief, 5000),
                ("whimsical_notes", "02_WHIMSICAL_NOTES.md", _whimsy_brief, 5000),
                ("short_study_guide", "03_SHORT_STUDY_GUIDE.md", _short_guide_brief, 4000),
                ("practice_exam", "04_PRACTICE_EXAM.md", _practice_exam_brief, 5000),
            ]
            with self._stage("top_level_artifacts"):
                self._run_artifacts_parallel(
                    artifacts, author, reviewer, master_plan,
                    solver=solver, notation_card=notation_card,
                    exam_patterns=exam_patterns,
                )

            # ── Whimsy index: parse generated whimsy artifact into per-topic hooks.
            whimsy_index = self._extract_whimsy_index(brief_ctx)

            # ----- 3. Daily study cards (one batched Haiku call)
            # Replaces the old per-day Researcher pattern (36 corpus-reads)
            # with a single batched call that emits all per-day study cards.
            # Net: ~40% time reduction on subscription mode.
            self.console.print("\n[bold #c96442]▸ Building per-day study cards (batched)[/bold #c96442]")
            cards_path = self.paths.checkpoints_dir / "study_cards.json"
            if cards_path.exists() and cards_path.stat().st_size > 200:
                research_by_day = {int(k): v for k, v in json.loads(cards_path.read_text()).items()}
                self.console.print(
                    f"  [dim]✓ reused cached study cards ({len(research_by_day)} days)[/dim]"
                )
            else:
                topic_distiller = TopicDistillerAgent(brief_ctx)
                self.console.print(
                    f"  [{ACCENT_HI}]→[/{ACCENT_HI}] one batched call for all {len(day_entries)} days "
                    f"[dim](haiku · replaces 36 separate researcher calls)[/dim]"
                )
                research_by_day = topic_distiller.distill_per_day(day_entries)
                # Persist for resumes
                cards_path.write_text(json.dumps({str(k): v for k, v in research_by_day.items()}))
                self.console.print(
                    f"  [{RICH_OK}]✓[/{RICH_OK}] {len(research_by_day)} study card(s) generated"
                )
                # If the batched call missed any days, fall back to per-day Researchers
                missing = [d for d in day_entries if d["day"] not in research_by_day]
                if missing:
                    self.console.print(
                        f"  [yellow]⚠[/yellow] {len(missing)} day(s) missing from batch — "
                        f"falling back to per-day researcher"
                    )
                    fallback = self._prefetch_research(missing, researcher)
                    research_by_day.update(fallback)

            # ── Per-day Researcher: full-corpus excerpts for each day.
            # Runs in addition to the topic-distiller study card. The Author
            # gets BOTH — the study card (concise day plan) and the
            # research slice (verbatim corpus excerpts). Disk-cached so
            # --resume is free.
            import os as _os
            if _os.environ.get("BART_SKIP_RESEARCHER") == "1":
                research_full_by_day: dict[int, str] = {}
                self.console.print(
                    "  [dim]BART_SKIP_RESEARCHER=1 — skipping per-day Researcher[/dim]"
                )
            else:
                self.console.print(
                    "\n[bold #c96442]▸ Per-day Researcher (full corpus)[/bold #c96442]"
                )
                research_full_by_day = self._prefetch_research(day_entries, researcher)

            # Then generate daily lessons (Author + optional Reviewer).
            self.console.print("\n[bold #c96442]▸ Generating daily lessons[/bold #c96442]")
            with self._stage("daily_lessons"):
                self._run_daily_lessons(
                    day_entries, author, reviewer, master_plan, research_by_day,
                    notation_card=notation_card,
                    problem_index=problem_index,
                    review_queue=review_queue,
                    whimsy_index=whimsy_index,
                    research_full_by_day=research_full_by_day,
                    exam_patterns=exam_patterns,
                )

            # ----- 4. Manifest + telemetry + summary
            self._write_manifest(corpus, day_entries, master_plan_path)
            self.telemetry.write(self.paths.root / "telemetry.json")

            # ----- 5. Render HTML packet
            with self._stage("html_render"):
                self._build_html_packet()

            # ----- 6. Custom study tools (Anki, Mermaid, etc.)
            with self._stage("tools"):
                self._run_tools()

            self._print_summary()
            return 0

        except KeyboardInterrupt:
            self.console.print("\n[red]✗ Interrupted. Run is preserved — re-run with --resume[/red] "
                               f"[cyan]{self.paths.run_id}[/cyan] [red]to continue.[/red]")
            return 130
        except Exception as e:
            self.logger.error("Orchestrator failed: %s\n%s", e, traceback.format_exc())
            err_type = type(e).__name__
            self.console.print(f"\n[red]✗ Run failed:[/red] [bold]{err_type}[/bold]: {e}")
            self.console.print(f"[dim]Full traceback in {self.paths.log_path}[/dim]")
            self.console.print(f"[dim]Resume with:[/dim] [white]./run --resume {self.paths.run_id}[/white]")
            return 1

    # ------------------------------------------------------------------
    def _extract(self):
        self.console.print("[bold #c96442]▸ Extracting materials[/bold #c96442]")
        kept, skipped = extract_all(console=self.console)
        return kept, skipped

    def _print_header(self):
        days = self.days_override or self.cfg.days_until
        auth_label = (
            "claude code (subscription)" if self.cfg.auth_mode == "claude-code"
            else "anthropic api"
        )
        self.console.print(
            Panel.fit(
                f"[bold]bart[/bold][bold {ACCENT}].[/bold {ACCENT}]   run [{ACCENT_LO}]{self.paths.run_id}[/{ACCENT_LO}]\n"
                f"subject: [white]{self.cfg.subject}[/white]\n"
                f"exam:    [white]{self.cfg.exam_date}[/white]  ([{RICH_OK}]{days}[/{RICH_OK}] days away)\n"
                f"level:   [white]{self.cfg.student_level}[/white]    style: [white]{self.cfg.style}[/white]\n"
                f"auth:    [white]{auth_label}[/white]\n"
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
        total_artifacts = 4 + days
        eta_min = max(2, int(total_artifacts * 0.4 / max(self.max_parallel, 1) + 1.5))
        critic_note = (
            " [dim](critic+revise enabled)[/dim]" if self.use_critic else " [dim](critic disabled)[/dim]"
        )

        from rich.prompt import Confirm
        if self.cfg.auth_mode == "claude-code":
            cost_line = f"  cost:       [{ACCENT_HI}]covered by your Claude subscription[/{ACCENT_HI}]"
            footer = "[dim]subscription rate limits apply.[/dim]"
        else:
            estimate = self._estimate_cost(corpus_chars, days)
            cost_line = f"  est. cost:  [{ACCENT_HI}]~${estimate:.2f}[/{ACCENT_HI}]{critic_note}"
            footer = "[dim]final cost depends on response lengths and cache hits.[/dim]"

        self.console.print(
            Panel.fit(
                f"[bold]ready to generate[/bold]\n\n"
                f"  artifacts:  [{ACCENT_HI}]{total_artifacts}[/{ACCENT_HI}]  "
                f"([{ACCENT_HI}]4[/{ACCENT_HI}] top-level + [{ACCENT_HI}]{days}[/{ACCENT_HI}] daily lessons)\n"
                f"  est. time:  [{ACCENT_HI}]~{eta_min} min[/{ACCENT_HI}]\n"
                f"{cost_line}\n\n"
                f"{footer}",
                border_style=ACCENT,
            )
        )
        import sys as _sys
        if not _sys.stdin.isatty():
            return True
        return Confirm.ask("\nproceed?", default=True)

    # ── Sidecar primitives — cached to disk so resumes skip them.
    def _extract_notation_card(self, corpus_brief: str, brief_ctx: AgentContext) -> str:
        path = self.paths.checkpoints_dir / "notation_card.md"
        if path.exists() and path.stat().st_size > 50:
            self.console.print(f"  [dim]✓ reused notation card ({path.stat().st_size} chars)[/dim]")
            return path.read_text()
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] extracting notation card "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        agent = NotationExtractorAgent(brief_ctx)
        text = agent.extract(corpus_brief)
        path.write_text(text)
        return text

    def _extract_problem_index(self, full_ctx: AgentContext) -> list[dict[str, Any]]:
        path = self.paths.checkpoints_dir / "problem_index.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                data = json.loads(path.read_text())
                self.console.print(f"  [dim]✓ reused problem index ({len(data)} entries)[/dim]")
                return data
            except json.JSONDecodeError:
                pass
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing corpus problems "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        agent = ProblemIndexerAgent(full_ctx)
        index = agent.index()
        atomic_write_json(path, index)
        return index

    def _extract_exam_patterns(self, full_ctx: AgentContext) -> dict[str, Any]:
        import os
        path = self.paths.checkpoints_dir / "exam_patterns.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                data = json.loads(path.read_text())
                self.console.print(
                    f"  [dim]✓ reused exam patterns "
                    f"({len(data.get('problems', []))} problems)[/dim]"
                )
                return data
            except json.JSONDecodeError:
                pass
        if os.environ.get("BART_SKIP_EXAM_PATTERN") == "1":
            from .agents.exam_pattern import EMPTY_PATTERNS
            return dict(EMPTY_PATTERNS)
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing past-exam patterns "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        from .agents.exam_pattern import ExamPatternAgent, EMPTY_PATTERNS
        try:
            agent = ExamPatternAgent(full_ctx)
            patterns = agent.extract()
        except Exception as e:  # noqa: BLE001
            self.logger.warning("exam_pattern extraction failed: %s", e)
            patterns = dict(EMPTY_PATTERNS)
        atomic_write_json(path, patterns)
        return patterns

    def _extract_whimsy_index(self, brief_ctx: AgentContext) -> dict[str, str]:
        path = self.paths.checkpoints_dir / "whimsy_index.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                data = json.loads(path.read_text())
                self.console.print(f"  [dim]✓ reused whimsy index ({len(data)} topics)[/dim]")
                return data
            except json.JSONDecodeError:
                pass
        whimsy_path = self.paths.root / "02_WHIMSICAL_NOTES.md"
        if not whimsy_path.exists():
            return {}
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing whimsy by topic "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        agent = WhimsyIndexerAgent(brief_ctx)
        index = agent.index(whimsy_path.read_text())
        atomic_write_json(path, index)
        return index

    def _make_corpus_block(self, corpus: str) -> list[dict]:
        # Cache breakpoint on the corpus block. Anthropic's prompt caching
        # gives a ~90% input-token discount on cache hits within 5 min, so
        # this dramatically helps API-mode users on subsequent calls.
        return [{
            "type": "text",
            "text": "USER'S COURSE MATERIALS — primary source of truth for all generations:\n\n" + corpus,
            "cache_control": {"type": "ephemeral"},
        }]

    # ------------------------------------------------------------------
    def _run_artifacts_parallel(
        self, artifacts, author, reviewer, master_plan,
        solver: SolverAgent | None = None,
        notation_card: str = "",
        exam_patterns: dict[str, Any] | None = None,
    ):
        results: dict[str, str] = {}
        exam_patterns = exam_patterns or {}

        def _gen_one(kind, filename, brief_fn, max_tokens):
            target = self.paths.root / filename
            if is_complete(target):
                self.logger.info("artifact %s already complete — skipping", filename)
                return kind, target.read_text()
            model_short = self._model_short(self.cfg.primary_model)
            self.console.print(
                f"  [{ACCENT_HI}]→[/{ACCENT_HI}] starting [white]{filename}[/white] "
                f"[dim]({model_short})[/dim]"
            )
            brief = brief_fn(self.cfg, master_plan)
            def _on_density(report):
                self.logger.info(
                    "%s density: total=%d score=%.0f missing=%s",
                    kind, report.total_blocks, report.score, report.missing,
                )
                if not report.healthy:
                    self.console.print(
                        f"  [yellow]⚠[/yellow] {kind} block density "
                        f"[dim]{report.score:.0f}/100 — missing {dict(report.missing)} — applying block-fix[/dim]"
                    )
            # Practice exam splits: Author writes Part A only (problems),
            # Solver fills Part B (answer key). Each call is shorter than the
            # combined call would be, and Solver is corpus-free.
            if kind == "practice_exam" and solver is not None:
                from .agents import exam_pattern as _exam_pattern
                exam_patterns_full = _exam_pattern.format_for_practice_exam(exam_patterns)
                part_a = author.write(
                    "practice_exam_part_a", brief, max_tokens=max_tokens,
                    label_suffix="part_a", on_density=_on_density,
                    exam_patterns_block=exam_patterns_full,
                )
                part_b = solver.solve(part_a, notation_card, max_tokens=max_tokens)
                text = part_a.rstrip() + "\n\n---\n\n" + part_b.lstrip()
                # Skip the heuristic+reviewer pass for the split exam — each
                # half was already written within scope.
                atomic_write_text(target, text)
                return kind, text
            text = author.write(kind, brief, max_tokens=max_tokens, label_suffix="initial", on_density=_on_density)

            if self.use_critic:
                hc = health_check(kind, text)
                density_after = block_density.evaluate(kind, text)
                if hc.healthy and density_after.healthy:
                    self.logger.info(
                        "skipped review for %s (heuristic=%d density=%.0f)",
                        kind, hc.score, density_after.score,
                    )
                    self.console.print(
                        f"  [{ACCENT_HI}]→[/{ACCENT_HI}] [white]{filename}[/white] "
                        f"[dim]passed gates (heuristic={hc.score}, density={density_after.score:.0f}); skipping reviewer[/dim]"
                    )
                else:
                    reason_bits = []
                    if not hc.healthy:
                        reason_bits.append(f"heuristic: {', '.join(hc.reasons[:2])}")
                    if not density_after.healthy:
                        reason_bits.append(f"density missing {dict(density_after.missing)}")
                    self.console.print(
                        f"  [{ACCENT_HI}]→[/{ACCENT_HI}] reviewing [white]{filename}[/white] "
                        f"[dim]({'; '.join(reason_bits)})[/dim]"
                    )
                    rev = reviewer.review(kind, text, brief, max_tokens=max_tokens)
                    self.logger.info("reviewer %s: score=%d, passed=%s", kind, rev.score, rev.passed)
                    if not rev.passed and rev.revised_text:
                        text = rev.revised_text
                        self.console.print(
                            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] revised [white]{filename}[/white] "
                            f"[dim](was {rev.score}; addressed {len(rev.must_fix)} item(s))[/dim]"
                        )

            atomic_write_text(target, text)
            return kind, text

        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            tasks = {
                pool.submit(_gen_one, kind, filename, brief_fn, max_tokens): (kind, filename)
                for kind, filename, brief_fn, max_tokens in artifacts
            }
            done_count = 0
            total = len(artifacts)
            for fut in as_completed(tasks):
                kind, filename = tasks[fut]
                done_count += 1
                try:
                    k, txt = fut.result()
                    results[k] = txt
                    self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] [{done_count}/{total}] {filename}")
                except Exception as e:  # noqa: BLE001
                    self.logger.error("artifact %s failed: %s", kind, e)
                    self.console.print(f"  [red]✗[/red] [{done_count}/{total}] {filename} — {e}")

    def _prefetch_research(self, day_entries, researcher) -> dict[int, str]:
        """Run all per-day researchers in parallel via the fast model.

        Researchers have no inter-day dependencies, and each call is cheap
        (Haiku, ~1-3K tokens out). Pre-fetching them removes a serial
        dependency in the daily-lesson pipeline.
        """
        if not day_entries:
            return {}
        out: dict[int, str] = {}

        def _do_one(entry):
            day_num = entry["day"]
            checkpoint = self.paths.checkpoints_dir / f"research_day_{day_num:02d}.md"
            if checkpoint.exists() and checkpoint.stat().st_size > 200:
                return day_num, checkpoint.read_text()
            topic = entry.get("topic", f"Day {day_num}")
            objectives = entry.get("learning_objectives", [])
            text = researcher.research(topic, objectives)
            checkpoint.write_text(text)
            return day_num, text

        # Researcher is haiku; bump parallelism — these are I/O bound and cheap.
        researcher_parallel = max(self.max_parallel, 6)
        total = len(day_entries)
        self.console.print(
            f"  [dim]running {total} researcher(s) in parallel "
            f"({researcher_parallel} concurrent, haiku — fast)[/dim]"
        )
        with ThreadPoolExecutor(max_workers=researcher_parallel) as pool:
            futs = [pool.submit(_do_one, e) for e in day_entries]
            done = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    day_num, text = fut.result()
                    out[day_num] = text
                    if done == 1 or done == total or done % 5 == 0:
                        self.console.print(
                            f"  [{RICH_OK}]✓[/{RICH_OK}] research [{done}/{total}]"
                        )
                except Exception as e:  # noqa: BLE001
                    self.logger.error("researcher failed: %s", e)
                    self.console.print(
                        f"  [red]✗[/red] researcher failed: {type(e).__name__}: {e}"
                    )
        return out

    def _run_daily_lessons(
        self, day_entries, author, reviewer, master_plan, research_by_day,
        notation_card: str = "",
        problem_index: list[dict[str, Any]] | None = None,
        review_queue: dict[int, list[dict[str, Any]]] | None = None,
        whimsy_index: dict[str, str] | None = None,
        research_full_by_day: dict[int, str] | None = None,
        exam_patterns: dict[str, Any] | None = None,
    ):
        if not day_entries:
            self.console.print("[yellow]  ⚠ Planner produced no day entries — skipping daily lessons.[/yellow]")
            return
        problem_index = problem_index or []
        review_queue = review_queue or {}
        whimsy_index = whimsy_index or {}
        research_full_by_day = research_full_by_day or {}
        exam_patterns = exam_patterns or {}

        def _gen_day(entry):
            day_num = entry["day"]
            day_date = entry.get("date", "")
            topic = entry.get("topic", "")
            filename = f"Day_{day_num:02d}_{day_date}.md"
            target = self.paths.daily_dir / filename
            if is_complete(target, min_chars=800):
                self.logger.info("day %s already complete — skipping", day_num)
                return day_num, filename, "cached"

            self.console.print(
                f"  [{ACCENT_HI}]→[/{ACCENT_HI}] starting Day {day_num:02d}"
                f"{f' — {topic[:48]}' if topic else ''}"
                f" [dim]({self._model_short(self.cfg.primary_model)})[/dim]"
            )
            research = research_by_day.get(day_num, "")
            # Filter the problem index for problems touching today's topic/chapters.
            day_topics = [topic] + entry.get("chapters", [])
            todays_problems = _problem_indexer.filter_for_topics(problem_index, day_topics)
            brief = _daily_lesson_brief(
                self.cfg, day_num, day_date, entry, master_plan, research,
                notation_card=notation_card,
                todays_problems=todays_problems,
                review_block=_review_queue.format_review_block(review_queue.get(day_num, [])),
                whimsy_hook=_whimsy_indexer.lookup(whimsy_index, topic),
            )
            def _on_density(report):
                self.logger.info(
                    "day %d density: total=%d score=%.0f missing=%s",
                    day_num, report.total_blocks, report.score, report.missing,
                )
                if not report.healthy:
                    self.console.print(
                        f"  [yellow]⚠[/yellow] Day {day_num:02d} block density "
                        f"[dim]{report.score:.0f}/100 — missing {dict(report.missing)} — applying block-fix[/dim]"
                    )
            from .agents import exam_pattern as _exam_pattern
            day_research_full = research_full_by_day.get(day_num, "")
            exam_patterns_drill = _exam_pattern.format_for_daily_drill(
                exam_patterns, day_topics,
            )
            # max_tokens=6000 is plenty for a fully-instrumented daily lesson
            # (~18K chars). The previous 12000 ceiling let Sonnet pad to 35K
            # chars, which is the dominant wall-time cost on subscription mode.
            text = author.write(
                "daily_lesson", brief,
                max_tokens=6000, label_suffix=f"day{day_num}",
                on_density=_on_density,
                research_slice=day_research_full,
                exam_patterns_block=exam_patterns_drill,
            )

            if self.use_critic:
                hc = health_check("daily_lesson", text)
                # Density is the primary quality signal in the new pipeline.
                # If both heuristic and density agree the lesson is healthy,
                # we skip the reviewer entirely (saves ~50% of reviewer calls).
                density_after = block_density.evaluate("daily_lesson", text)
                if hc.healthy and density_after.healthy:
                    self.logger.info(
                        "day %d skipped review (heuristic=%d density=%.0f)",
                        day_num, hc.score, density_after.score,
                    )
                else:
                    reasons = []
                    if not hc.healthy:
                        reasons.append(f"heuristic={hc.reasons[:2]}")
                    if not density_after.healthy:
                        reasons.append(f"density-missing={dict(density_after.missing)}")
                    self.logger.info("day %d reviewing because: %s", day_num, reasons)
                    rev = reviewer.review("daily_lesson", text, brief, max_tokens=6000)
                    self.logger.info("day %d reviewer: score=%d, passed=%s", day_num, rev.score, rev.passed)
                    if not rev.passed and rev.revised_text:
                        text = rev.revised_text

            atomic_write_text(target, text)
            return day_num, filename, "written"

        total_days = len(day_entries)
        failed_entries: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            tasks = {pool.submit(_gen_day, e): e for e in day_entries}
            self.console.print(
                f"  [dim]queued {total_days} day(s); running up to "
                f"{self.max_parallel} in parallel[/dim]"
            )
            done_count = 0
            for fut in as_completed(tasks):
                entry = tasks[fut]
                done_count += 1
                try:
                    day_num, filename, status = fut.result()
                    icon = f"[dim]●[/dim]" if status == "cached" else f"[{RICH_OK}]✓[/{RICH_OK}]"
                    self.console.print(f"  {icon} [{done_count}/{total_days}] Day {day_num:02d} — {filename}")
                except Exception as e:  # noqa: BLE001
                    self.logger.error("day %s failed: %s\n%s", entry.get("day"), e, traceback.format_exc())
                    self.console.print(
                        f"  [red]✗[/red] [{done_count}/{total_days}] Day {entry.get('day')} — {e} "
                        f"[dim](will retry serially)[/dim]"
                    )
                    failed_entries.append(entry)

        # ── Day-level retry pass ─────────────────────────────────────
        # Parallel runs lose entire days when an API error exhausts the
        # call-level retries (transient 529s, regional rate-limits, the
        # connection pool dropping). Re-attempt failures one at a time so
        # they have the full per-call retry budget without contention. This
        # is the difference between shipping a packet with days 1, 3, 5
        # vs. all 8 days from the same run.
        if failed_entries:
            self.console.print(
                f"  [{ACCENT_HI}]↻[/{ACCENT_HI}] retrying {len(failed_entries)} failed day(s) serially "
                f"[dim](parallel run lost them — likely rate-limit / transient)[/dim]"
            )
            still_failed: list[dict[str, Any]] = []
            for entry in failed_entries:
                try:
                    day_num, filename, status = _gen_day(entry)
                    self.console.print(
                        f"  [{RICH_OK}]✓[/{RICH_OK}] Day {day_num:02d} — {filename} [dim](recovered)[/dim]"
                    )
                except Exception as e:  # noqa: BLE001
                    self.logger.error("day %s failed on retry: %s", entry.get("day"), e)
                    self.console.print(
                        f"  [red]✗[/red] Day {entry.get('day')} — {e} [dim](still failing)[/dim]"
                    )
                    still_failed.append(entry)
            if still_failed:
                missing = ", ".join(str(e.get("day")) for e in still_failed)
                self.console.print(
                    f"  [yellow]⚠ {len(still_failed)} day(s) still missing after retry: {missing}.[/yellow]"
                )
                self.console.print(
                    f"  [dim]resume with:[/dim] [bold]./run --resume {self.paths.run_id}[/bold]"
                )

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
        title = "Done." if self.cfg.auth_mode == "claude-code" else "Done."
        table = Table(title=title, border_style=ACCENT_LO)
        table.add_column("Metric", style=ACCENT_HI)
        table.add_column("Value", style="white", justify="right")
        table.add_row("Calls", str(s["calls"]))
        table.add_row("Input tokens (approx)", f"{s['input_tokens']:,}")
        table.add_row("Output tokens (approx)", f"{s['output_tokens']:,}")
        if self.cfg.auth_mode != "claude-code":
            table.add_row("Cache reads", f"{s['cache_read_input_tokens']:,}")
            table.add_row("Cache writes", f"{s['cache_creation_input_tokens']:,}")
        table.add_row("Wall time", f"{s['duration_s']:.1f}s")
        if self.cfg.auth_mode == "claude-code":
            table.add_row("[bold]Cost[/bold]", "[bold]covered by subscription[/bold]")
        else:
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
    def _run_tools(self) -> None:
        """Run each registered custom study tool."""
        try:
            from .tools import TOOLS
        except Exception as e:  # noqa: BLE001
            self.logger.error("tools import failed: %s", e)
            return

        if not TOOLS:
            return

        self.console.print("\n[bold #c96442]▸ Running study tools[/bold #c96442]")
        manifest = json.loads(self.paths.manifest_path.read_text())
        for tool in TOOLS:
            try:
                result = tool.run(self.paths.root, manifest)
                if result.success:
                    self.console.print(
                        f"  [{RICH_OK}]✓[/{RICH_OK}] {tool.name} — {result.detail}"
                    )
                    for p in result.output_paths:
                        self.console.print(f"     [dim]→ {p.relative_to(self.paths.root.parent.parent)}[/dim]")
                else:
                    self.console.print(
                        f"  [yellow]⊘[/yellow] {tool.name} — {result.detail}"
                    )
            except Exception as e:  # noqa: BLE001
                self.logger.error("tool %s failed: %s", tool.name, e)
                self.console.print(f"  [red]✗[/red] {tool.name} — {type(e).__name__}: {e}")

    def _build_html_packet(self) -> None:
        """Render markdown artifacts to a polished HTML packet."""
        self.console.print("\n[bold #c96442]▸ Building HTML packet[/bold #c96442]")
        try:
            from .render.packet import build_packet
            manifest = json.loads((self.paths.manifest_path).read_text())
            warnings = build_packet(self.paths.root, manifest)
        except Exception as e:  # noqa: BLE001
            self.logger.error("HTML packet build failed: %s\n%s", e, traceback.format_exc())
            self.console.print(f"  [yellow]⚠[/yellow] HTML build failed: {e}")
            self.console.print("  [dim]markdown originals are still available in the run dir.[/dim]")
            return

        problems = [w for w in warnings if w.severity != "info"]
        infos = [w for w in warnings if w.severity == "info"]
        if problems:
            counts: dict[str, int] = {}
            for w in problems:
                counts[w.kind] = counts.get(w.kind, 0) + 1
            summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            self.console.print(f"  [yellow]⚠[/yellow] {len(problems)} formatting issue(s): {summary}")
            self.console.print(f"  [dim]see {self.paths.root / 'render_warnings.json'}[/dim]")
        else:
            extra = f" [dim]({len(infos)} normalizations applied)[/dim]" if infos else ""
            self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] HTML packet built cleanly{extra}")

        # Audit + autofix every page before declaring the packet "done". Cheap
        # post-render check that catches regressions the renderer itself can't
        # see — broken KaTeX delim config, double-escaped math, raw `\\[…\\]`
        # left in `<pre>`, dangling `<font>` tags, etc. Idempotent on a clean
        # packet so re-running costs nothing.
        try:
            from .render.format_audit import audit, render_report
            result = audit(self.paths.root, apply_fixes=True)
            if result.errors or result.warnings or result.fixes_applied:
                render_report(self.console, result, self.paths.root)
            else:
                self.console.print(
                    f"  [{RICH_OK}]✓[/{RICH_OK}] format audit clean ({result.files_scanned} pages)"
                )
        except Exception as e:  # noqa: BLE001
            # Audit failures are not packet failures — log and continue.
            self.logger.warning("format audit failed: %s", e)
            self.console.print(f"  [yellow]⚠[/yellow] format audit failed: {type(e).__name__}: {e}")

        self.console.print(f"  [dim]open[/dim] [white]{self.paths.root / 'index.html'}[/white]")

    @staticmethod
    def _model_short(model: str) -> str:
        """Cosmetic short label for a model id (e.g. 'sonnet', 'opus', 'haiku')."""
        m = model.lower()
        if "haiku" in m:
            return "haiku"
        if "sonnet" in m:
            return "sonnet"
        if "opus" in m:
            return "opus"
        return model.split("-", 1)[0] if "-" in model else model

    def _stage(self, name: str):
        """Context manager that records the duration of a pipeline stage."""
        orchestrator = self
        class _StageCtx:
            def __enter__(self):
                self.t0 = time.time()
                return self
            def __exit__(self, *exc):
                orchestrator.telemetry.record_stage(name, time.time() - self.t0)
                return False
        return _StageCtx()

    def _on_llm_event(self, event: str, payload: dict):
        self.logger.debug("llm.%s %s", event, payload)
        if event == "stream_progress":
            label = payload.get("label", "?")
            chars = payload.get("chars", 0)
            short = label[:42] + "…" if len(label) > 42 else label
            self.console.print(
                f"  [dim]· streaming: [/dim][{ACCENT_HI}]{short}[/{ACCENT_HI}]"
                f"[dim] ({chars:,} chars received)[/dim]"
            )
        elif event == "heartbeat":
            elapsed = payload.get("elapsed_s", 0)
            label = payload.get("label", "?")
            # Skip heartbeat if streaming progress is firing — they overlap noisily.
            short = label[:48] + "…" if len(label) > 48 else label
            self.console.print(
                f"  [dim]· still working: [/dim][{ACCENT_HI}]{short}[/{ACCENT_HI}]"
                f"[dim] ({elapsed}s)[/dim]"
            )
        elif event == "slow_warning":
            label = payload.get("label", "")
            if "fast" not in label.lower() and "haiku" not in label.lower():
                self.console.print(
                    f"  [dim]tip: subscription mode + opus is the slowest combo. "
                    f"Ctrl-C and rerun with[/dim] [white]./run --turbo[/white] "
                    f"[dim]for a ~5x speedup.[/dim]"
                )

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

# Master-plan excerpt size. Was 2500 — most of that was unused context the
# Author already gets from research_brief + topic. 800 chars is the
# orientation summary; cuts ~10K characters across a daily-lesson run.
_PLAN_EXCERPT_CHARS = 800


def _block_catalog(artifact_kind: str = "") -> str:
    """Slim block catalog scoped to the artifact's needs.

    Sending all 31 schemas to every Author call is ~5K wasted tokens per
    call. The slim catalogs cover the 8-12 blocks each artifact actually
    uses (defined in `block_expand._CATALOG_BY_ARTIFACT`).
    """
    from .render.block_expand import catalog_for, render_block_catalog
    body = catalog_for(artifact_kind) if artifact_kind else render_block_catalog()
    return (
        "BLOCK CATALOG (use these — vanilla markdown is the wrong choice for "
        "every box, callout, formula, quick-check, drill, mnemonic, table, "
        "or diagram). Emit each as a fenced ```bart-<name> with JSON body:\n\n"
        + body
    )


def _quality_gate(artifact_kind: str) -> str:
    """One-line summary of the block-density floor, fed into briefs."""
    from .agents.block_density import thresholds_summary
    s = thresholds_summary(artifact_kind)
    return (s + "\n\n") if s else ""


def _schematics_brief(cfg: Config, master_plan: str) -> str:
    _kind = "schematics"
    return (
        f"ARTIFACT: SCHEMATICS — visual / structural reference for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:_PLAN_EXCERPT_CHARS]}\n\n"
        + _quality_gate(_kind) +
        "Required content (each section uses bart blocks, not raw markdown):\n"
        "1. ONE `bart-concept-map` showing how major topics connect.\n"
        "2. For each major topic: ONE `bart-formula-card` per key formula "
        "(with `legend` and one-line `note`).\n"
        "3. ONE or more `bart-comparison-matrix` for property tables / "
        "when-to-use guides.\n"
        "4. `bart-trap-callout` (kind=trap) for every 'if you see X, watch "
        "for Y' item — at least 5 across the artifact.\n"
        "5. Where the topic has a clear sequence (mechanism, derivation), "
        "use `bart-process-ribbon` or `bart-flowchart`.\n\n"
        "Dense, scannable, exam-day useful.\n\n"
    )


def _whimsy_brief(cfg: Config, master_plan: str) -> str:
    _kind = "whimsical_notes"
    return (
        f"ARTIFACT: WHIMSICAL NOTES — memorable companion for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:_PLAN_EXCERPT_CHARS]}\n\n"
        + _quality_gate("whimsical_notes") +
        "Per major topic: ONE `bart-mnemonic-card` (acronym + per-letter "
        "expansion + optional story). Whimsy must aid the math, not replace "
        "it. End each topic with a short serif paragraph tying the analogy "
        "back to the corpus.\n\n"
    )


def _short_guide_brief(cfg: Config, master_plan: str) -> str:
    _kind = "short_study_guide"
    return (
        f"ARTIFACT: SHORT STUDY GUIDE — the document to read in the final hour for {cfg.subject}.\n\n"
        + _quality_gate("short_study_guide") +
        "Sections:\n"
        "1. The 20 facts that matter most — numbered markdown list, one line each.\n"
        "2. The 5 most-likely-tested concepts — two paragraphs each, each "
        "concept ends with ONE `bart-quick-check` to confirm understanding.\n"
        "3. The 5 most common traps — one `bart-trap-callout` (kind=trap) each.\n"
        "4. A 10-question rapid-fire quiz — ONE `bart-checkpoint` block "
        "with all 10 cards.\n\n"
        "Brutal selectivity.\n\n"
    )


def _practice_exam_brief(cfg: Config, master_plan: str) -> str:
    # Part A only — Solver agent generates Part B (answer key) separately.
    _kind = "practice_exam_part_a"
    from .prompts import load_prompt
    skeleton = load_prompt("skeletons/practice_exam_part_a.md")
    return (
        f"ARTIFACT: PRACTICE EXAM (Part A — problems only, no solutions) for {cfg.subject}.\n\n"
        f"MASTER PLAN EXCERPT:\n{master_plan[:_PLAN_EXCERPT_CHARS]}\n\n"
        "- Match the structure of the user's actual exam if visible in the corpus; otherwise "
        "infer a sensible structure from the materials.\n"
        "- Every problem is NEW — inspired by, not copied from, the materials.\n"
        "- Cover topics in proportion to weight from the master plan.\n"
        "- Number problems with stable IDs (P1, P2, ...) — the answer key references them.\n"
        "- Do NOT emit solutions. The answer key is a separate stage.\n\n"
        f"SKELETON\n---\n{skeleton}\n---\n\n"
    )


def _daily_lesson_brief(
    cfg: Config,
    day_num: int,
    day_date: str,
    entry: dict[str, Any],
    master_plan: str,
    research: str,
    notation_card: str = "",
    todays_problems: list[dict[str, Any]] | None = None,
    review_block: str = "",
    whimsy_hook: str = "",
) -> str:
    _kind = "daily_lesson"
    from .prompts import load_prompt
    objectives = "\n".join(f"- {o}" for o in entry.get("learning_objectives", []))
    chapters = ", ".join(entry.get("chapters", []))
    focus = entry.get("focus", "learn")
    hours = entry.get("hours", cfg.daily_hours)
    skeleton = load_prompt("skeletons/daily_lesson.md")
    # Interleaved-review subtopics — moved here from the prior day by the
    # deterministic planner. The brief asks the Author to lead the lesson
    # with quick-checks on these BEFORE introducing new content (Roediger's
    # testing effect; Bjork's interleaving).
    interleaved = entry.get("interleaved_review", [])
    interleave_section = (
        "INTERLEAVED REVIEW (cover these in a `bart-quick-check` block "
        "BEFORE the new content — testing-effect priming):\n"
        + "\n".join(f"- {x}" for x in interleaved)
        + "\n\n"
    ) if interleaved else ""
    problems_block = _problem_indexer.format_problem_block(todays_problems or [])
    notation_section = (
        f"NOTATION CARD (use these symbols verbatim)\n{notation_card}\n\n"
        if notation_card else ""
    )
    whimsy_section = (
        f"WHIMSY HOOK (drop into the Whimsical hook section)\n{whimsy_hook}\n\n"
        if whimsy_hook else ""
    )
    teaching_contract = (
        "TEACHING CONTRACT — this is a lesson, not an extraction. Before "
        "writing, identify (a) the 1–3 THRESHOLD concepts where understanding "
        "qualitatively shifts and (b) the wrong-but-natural MISCONCEPTION "
        "students bring in for each. Use `bart-concept-build` for every "
        "load-bearing concept (motivate → name → ground → connect → contrast "
        "→ apply). Use `bart-trap-callout` (kind=trap) titled \"What students "
        "usually think\" for each misconception. Voice: first-person present, "
        "active-recall cues woven in (\"pause — what do you predict?\"), "
        "concrete-to-abstract, never the reverse.\n\n"
    )
    return (
        f"ARTIFACT: DAILY LESSON — Day {day_num} of {cfg.subject}.\n"
        f"Date: {day_date} · Focus: {focus} · Hours: {hours}\n"
        f"Topic: {entry.get('topic', '')} · Chapters: {chapters}\n\n"
        + teaching_contract +
        f"OBJECTIVES\n{objectives}\n\n"
        f"{interleave_section}"
        f"SPACED REVIEW (cover briefly in the Recap section)\n{review_block}\n\n"
        f"TODAYS PROBLEMS (corpus-indexed; cover all of them in Past-exam problems)\n{problems_block}\n\n"
        f"{notation_section}"
        f"{whimsy_section}"
        f"RESEARCH BRIEF\n---\n{research}\n---\n\n"
        f"MASTER PLAN EXCERPT\n{master_plan[:_PLAN_EXCERPT_CHARS]}\n\n"
        + _quality_gate("daily_lesson") +
        f"SKELETON (follow this section ordering exactly; expand each section to its full depth)\n"
        f"---\n{skeleton}\n---\n\n"
    )
