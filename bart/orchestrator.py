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
import os
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
from .io.corpus import build_corpus, default_char_budget, extract_all
from .backends import (
    AnthropicAPIBackend,
    ClaudeCodeBackend,
    LLMContextTooLongError,
    LocalBackend,
)
from .paths import RunPaths
from .runrecord import RunRecord
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
        # Structured record of what went sideways this run — artifact
        # outcomes (ok/failed/recovered/skipped/fallback) and warnings.
        # Threaded through the artifact + sidecar paths; read by
        # `_finalize()` for the incomplete-packet banner and returned from
        # `run()` so the caller can set an exit code.
        self.run_record = RunRecord()
        # Populated when auth_mode == "local". The finally block in run()
        # tears it down so the inference server stops cleanly even on Ctrl-C.
        self._local_runtime = None

    # ------------------------------------------------------------------
    def run(self) -> int:
        try:
            # bart-figure blocks read BART_IMAGE_GEN at render time; mirror
            # the config flag into the env so the renderer (which is pure and
            # config-unaware) knows whether to generate real illustrations.
            if getattr(self.cfg, "image_generation", False):
                os.environ["BART_IMAGE_GEN"] = "1"
            self._print_header()
            with self._stage("extract"):
                kept, skipped = self._extract()
            corpus = build_corpus(
                kept, skipped, char_budget=default_char_budget(self.cfg.auth_mode),
            )
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
            elif self.cfg.auth_mode == "local":
                # New local stack: detect hardware → install engine → download
                # weights → start mlx-lm/llama-cpp-python server → return a
                # RuntimeHandle wrapping an OpenAI-compatible client.
                from . import local_runtime as _lr
                self.console.print(
                    "\n[bold #c96442]▸ Local model setup[/bold #c96442]"
                )
                self._local_runtime = _lr.prepare(
                    console=self.console,
                    model_key=self.cfg.local_model_key or None,
                    family=self.cfg.local_model_family,
                    on_event=self._on_llm_event,
                )
                llm = LocalBackend(
                    runtime=self._local_runtime,
                    telemetry=self.telemetry,
                    cache_dir=self.paths.cache_dir,
                    on_event=self._on_llm_event,
                )
                # Local server is single-slot by default — collapse parallel
                # fan-out so the orchestrator doesn't queue 4 calls behind
                # the same server slot. Without this, ThreadPoolExecutor(4)
                # silently serializes and wall-clock balloons.
                if self.max_parallel > self._local_runtime.parallel_slots:
                    self.console.print(
                        f"  [dim]capping max_parallel from "
                        f"{self.max_parallel} to "
                        f"{self._local_runtime.parallel_slots} "
                        f"(local server slot count)[/dim]"
                    )
                    self.max_parallel = self._local_runtime.parallel_slots
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
            # In local mode this `full_corpus_block` is *deliberately
            # never used* — the chunked agents in `local_chunked.py` walk
            # `corpus.files` directly so no single LLM call ever sees the
            # whole corpus. We still build it so a misrouted call would fail
            # loud (the LocalBackend num_ctx cap will refuse the call) rather
            # than silently OOM the daemon.
            full_corpus_block = self._make_corpus_block(corpus.body)
            full_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=full_corpus_block)

            # In local mode a single LLM call must never receive the whole
            # corpus — even Qwen3's 128K context fills up fast at 4-bit KV
            # cache rates on consumer hardware.
            is_local = self.cfg.auth_mode == "local"

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
            elif is_local:
                # Local mode: per-file map-reduce. Never sends the full
                # corpus to a 1B-4B model in a single call.
                from .agents.local_chunked import chunked_distill
                self.console.print(
                    f"  [dim](local mode: per-file digest across "
                    f"{len(corpus.files)} file(s))[/dim]"
                )
                def _distill_progress(i: int, n: int, label: str) -> None:
                    if i == 1 or i == n or i % 5 == 0:
                        self.console.print(
                            f"  [dim]  · digest {i}/{n}: {label}[/dim]"
                        )
                corpus_brief = chunked_distill(
                    llm, self.cfg, corpus.files, on_progress=_distill_progress,
                )
                brief_path.write_text(corpus_brief)
                self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] corpus brief written ({len(corpus_brief):,} chars)")
            else:
                distiller = DistillerAgent(full_ctx)
                corpus_brief = distiller.distill()
                brief_path.write_text(corpus_brief)
                self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] corpus brief written ({len(corpus_brief):,} chars)")

            # The brief block is what most agents see going forward.
            # 1h TTL: a typical run takes 10–30 min and re-uses this block
            # across every Author + sidecar call. The 5-min default would
            # expire mid-run and force a cache rewrite.
            brief_block = [{
                "type": "text",
                "text": (
                    "CORPUS BRIEF — a structured digest of the user's course materials, "
                    "use this as the primary source of truth:\n\n" + corpus_brief
                ),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }]

            brief_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=brief_block)
            # Planner is now deterministic — no agent instance needed.
            author = AuthorAgent(brief_ctx)

            # Researcher keeps full corpus access — it's the only agent that needs it.
            # Pass the brief block as a fallback so per-topic calls can survive
            # context-too-long without crashing the run.
            researcher = ResearcherAgent(full_ctx, fallback_corpus_block=brief_block)

            # Reviewer fuses critic + reviser into a single corpus-free call.
            reviewer_ctx = AgentContext(cfg=self.cfg, llm=llm, corpus_block=[])
            reviewer = ReviewerAgent(reviewer_ctx)
            # Solver lives in the brief context — it doesn't need the full corpus.
            solver = SolverAgent(brief_ctx)

            # ── Per-run sidecar primitives (notation card, problem index,
            # exam patterns). Three independent corpus reads → run them in
            # parallel for ~3x faster sidecar extraction.
            #
            # In local mode we route problem-index and exam-pattern through
            # chunked per-file paths instead of full-corpus single calls,
            # for the same reason as the distiller: a 1B model can't ingest
            # 400K chars without OOM-ing.
            with ThreadPoolExecutor(max_workers=3) as _sidecar_pool:
                _f_notation = _sidecar_pool.submit(
                    self._extract_notation_card, corpus_brief, brief_ctx,
                )
                if is_local:
                    _f_problems = _sidecar_pool.submit(
                        self._extract_problem_index_local, llm, corpus.files,
                    )
                    _f_exam = _sidecar_pool.submit(
                        self._extract_exam_patterns_local, llm, corpus.files,
                    )
                else:
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
                # Sidecar: on failure, fall back to an empty mapping — the
                # `missing` logic just below then routes every day through the
                # per-day researcher (or empty slices in local mode), so the
                # run continues rather than aborting.
                research_by_day = self._run_sidecar(
                    "topic_distiller",
                    lambda: topic_distiller.distill_per_day(day_entries),
                    fallback={}, record=self.run_record, logger=self.logger,
                )
                # Persist for resumes
                cards_path.write_text(json.dumps({str(k): v for k, v in research_by_day.items()}))
                self.console.print(
                    f"  [{RICH_OK}]✓[/{RICH_OK}] {len(research_by_day)} study card(s) generated"
                )
                # If the batched call missed any days, fall back to per-day Researchers.
                # In local mode a per-day Researcher would hit the LocalBackend's
                # n_ctx cap (full-corpus call), so we substitute an empty
                # research slice — the Author still has the brief + day entry to
                # work from, which is enough on local models.
                missing = [d for d in day_entries if d["day"] not in research_by_day]
                if missing:
                    if is_local:
                        self.console.print(
                            f"  [yellow]⚠[/yellow] {len(missing)} day(s) missing from batch — "
                            f"local mode fills with empty research slice (brief carries the load)"
                        )
                        for d in missing:
                            research_by_day[d["day"]] = ""
                    else:
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
            #
            # Per-day Researcher full-corpus pass: OPT-IN as of tokenomics
            # Tier 1. TopicDistiller (above) replaces the bulk of what this
            # used to provide — per-day study cards keyed off the brief.
            # The full-corpus pass adds verbatim excerpts on top of the
            # brief. Useful for high-rigor courses but additive cost: N
            # full-corpus reads × Haiku, with 6-concurrent fan-out that
            # historically caused 429 storms.
            #
            # Opt back in via:
            #   - cfg.deep_research = true   (persistent, in .bart_config.json)
            #   - BART_RUN_FULL_RESEARCHER=1 (one-shot env override)
            # Forced off via:
            #   - BART_SKIP_RESEARCHER=1     (legacy env override, still honored)
            #
            # Local mode never runs this pass.
            import os as _os
            wants_full = (
                getattr(self.cfg, "deep_research", False)
                or _os.environ.get("BART_RUN_FULL_RESEARCHER") == "1"
            )
            forced_off = _os.environ.get("BART_SKIP_RESEARCHER") == "1"
            run_full = wants_full and not forced_off and not is_local

            if not run_full:
                research_full_by_day: dict[int, str] = {}
                if forced_off:
                    reason = "BART_SKIP_RESEARCHER=1"
                elif is_local:
                    reason = "local mode"
                else:
                    reason = "deep_research disabled (default — TopicDistiller covers it)"
                self.console.print(
                    f"  [dim]{reason} — skipping per-day Researcher full-corpus pass[/dim]"
                )
            else:
                self.console.print(
                    "\n[bold #c96442]▸ Per-day Researcher (full corpus, deep_research=true)[/bold #c96442]"
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

            self._finalize()
            self._print_summary()
            return self.run_record

        except KeyboardInterrupt:
            self.console.print("\n[red]✗ Interrupted. Run is preserved — re-run with --resume[/red] "
                               f"[cyan]{self.paths.run_id}[/cyan] [red]to continue.[/red]")
            return 130
        except Exception as e:
            # `LocalRuntimeError` carries a hand-written, user-friendly
            # message + remediation hint — print just that, no traceback.
            # Everything else gets the developer-style failure card.
            from .local_runtime.errors import LocalRuntimeError
            self.logger.error("Orchestrator failed: %s\n%s", e, traceback.format_exc())
            if isinstance(e, LocalRuntimeError):
                self.console.print(f"\n[red]✗ {e}[/red]")
                self.console.print(
                    f"\n[dim]Full log at {self.paths.log_path} · "
                    f"diagnose with[/dim] [white]./run doctor[/white]"
                )
            else:
                err_type = type(e).__name__
                self.console.print(f"\n[red]✗ Run failed:[/red] [bold]{err_type}[/bold]: {e}")
                self.console.print(f"[dim]Full traceback in {self.paths.log_path}[/dim]")
                self.console.print(f"[dim]Resume with:[/dim] [white]./run --resume {self.paths.run_id}[/white]")
            return 1
        finally:
            # Local-mode cleanup runs even on Ctrl-C / exception. Touches the
            # 24h cache so the next run within a day reuses the model, and
            # force-unloads from VRAM so the user's machine isn't still
            # holding 12-40 GB after the packet is done.
            if self.cfg.auth_mode == "local" and self._local_runtime is not None:
                try:
                    self._local_runtime.shutdown()
                    self.console.print(
                        "  [dim]✓ stopped local inference server "
                        "(weights cached 24h for fast next run)[/dim]"
                    )
                except Exception:  # noqa: BLE001
                    pass

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
                f"primary: [white]{self.cfg.primary_model}[/white]    "
                f"daily: [white]{getattr(self.cfg, 'daily_model', self.cfg.primary_model)}[/white]    "
                f"fast:  [white]{self.cfg.fast_model}[/white]",
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
        """USD cost estimate that mirrors the actual call graph.

        The previous estimate counted only ``1 + 4 + days`` calls and
        applied a flat 1.4× critic multiplier. That undercounts the real
        cost by ~5-10× because it omits, in order of magnitude:

        - The per-day Researcher full-corpus pass (the largest hidden line
          item — ~corpus_tokens × days of cache-read).
        - Three sidecar full-corpus calls (notation, problem_indexer,
          exam_pattern).
        - The TopicDistiller batched call.
        - Author retry calls (truncation_fix, block_fix).
        - Reviewer + Reviser calls (these run on the *primary* model with
          a brief + full artifact, not cheap).
        - Solver (practice exam Part B answer key).
        - Whimsy index extraction.
        - Output tokens approximated as 8K/call when the actual ceiling
          for the daily-lesson Author is 6K and Sonnet/Opus fill the budget.

        The new version computes each line explicitly and returns the sum.
        Callers can also reach `cost_breakdown(...)` to render a table.
        """
        bd = self.cost_breakdown(corpus_chars, days)
        return bd["total"]

    def cost_breakdown(self, corpus_chars: int, days: int) -> dict[str, float]:
        """Return a dict of {line_item: usd} the orchestrator can render.

        Each line item is computed by replicating the orchestrator's call
        graph with conservative-but-realistic token budgets. Subscription
        and local are zero-cost; only API mode produces non-zero
        items here. The returned dict always includes a ``total`` key.
        """
        from .telemetry import PRICING
        opus = PRICING.get(self.cfg.primary_model, PRICING["claude-opus-4-7"])
        haiku = PRICING.get(self.cfg.fast_model, PRICING["claude-haiku-4-5-20251001"])
        # Daily lessons run on cfg.daily_model (Sonnet by default).
        daily = PRICING.get(
            getattr(self.cfg, "daily_model", "claude-sonnet-4-6"),
            PRICING["claude-sonnet-4-6"],
        )

        corpus_tokens = corpus_chars // 4

        # Brief is what most agents see going forward; ~12K chars typical
        # after distillation (the distiller is asked for ≤2500 words).
        brief_tokens = 3_000

        # Brief + per-call instruction + skeleton + small sidecars.
        author_user_tokens = brief_tokens + 2_000

        # Reviewer sees brief + full artifact (corpus-free).
        artifact_out_tokens = 5_500   # average over schematics/whimsy/exam/short_guide
        daily_out_tokens   = 5_500   # current max_tokens=6000; the model fills.
        reviewer_in_tokens = brief_tokens + artifact_out_tokens
        reviewer_out_tokens = 4_000   # PASS path is short, REVISE path is long; avg.

        items: dict[str, float] = {}

        def _line(name: str, m: dict, *, in_=0, out=0, cw=0, cr=0) -> None:
            items[name] = (
                in_ * m["input"]
                + out * m["output"]
                + cw * m["cache_write"]
                + cr * m["cache_read"]
            ) / 1_000_000.0

        # 1. Distiller: full corpus → 6K-token brief. Fast model.
        _line("distiller", haiku, cw=corpus_tokens, out=brief_tokens)

        # 2. Three sidecar full-corpus calls. Each reads the corpus from cache
        #    (cache write happened in the distiller call within 5 min of start)
        #    and emits ~1.5K tokens.
        _line("notation_extractor", haiku, in_=brief_tokens, out=400)
        _line("problem_indexer", haiku, cr=corpus_tokens, out=2_500)
        _line("exam_pattern", haiku, cr=corpus_tokens, out=2_500)

        # 3. TopicDistiller batched call: brief + day plan in, study cards out.
        _line(
            "topic_distiller", haiku,
            in_=brief_tokens + 2_000, out=days * 600,
        )

        # 4. Whimsy indexer: small Haiku call.
        _line("whimsy_indexer", haiku, in_=2_000, out=600)

        # 5. Top-level artifact Authors (4). Brief is cached after distiller.
        n_top = 4
        _line(
            "top_level_authors", opus,
            cr=n_top * brief_tokens, in_=n_top * 2_000,
            out=n_top * artifact_out_tokens,
        )

        # 6. Top-level Reviewer pass (only ~50% trigger after density gates).
        if self.use_critic:
            _line(
                "top_level_reviewer", opus,
                in_=n_top * reviewer_in_tokens // 2,
                out=n_top * reviewer_out_tokens // 2,
            )

        # 7. Daily lesson Authors (N). Each gets brief + skeleton + research.
        # Tokenomics Tier 1: routed through cfg.daily_model (Sonnet by
        # default) instead of Opus. ~5× cheaper on input + output.
        _line(
            "daily_authors", daily,
            cr=days * brief_tokens, in_=days * author_user_tokens,
            out=days * daily_out_tokens,
        )
        # Block-fix continuation (Haiku, ~50% trigger rate).
        _line(
            "daily_block_fix", haiku,
            in_=days * 600 // 2, out=days * 800 // 2,
        )

        # 8. Daily Reviewer pass (~50% trigger after density gates).
        if self.use_critic:
            _line(
                "daily_reviewer", opus,
                in_=days * reviewer_in_tokens // 2,
                out=days * reviewer_out_tokens // 2,
            )

        # 9. Per-day Researcher full-corpus pass.
        #    Tokenomics Tier 1: now OFF by default (TopicDistiller covers
        #    the bulk of the value at ~1/36 the cost). Counted only when
        #    cfg.deep_research=true or BART_RUN_FULL_RESEARCHER=1.
        import os as _os
        run_full_researcher = (
            (
                getattr(self.cfg, "deep_research", False)
                or _os.environ.get("BART_RUN_FULL_RESEARCHER") == "1"
            )
            and _os.environ.get("BART_SKIP_RESEARCHER") != "1"
            and self.cfg.auth_mode != "local"
        )
        if run_full_researcher:
            _line(
                "per_day_researcher", haiku,
                cr=days * corpus_tokens, out=days * 1_500,
            )

        # 10. Solver (practice exam Part B answer key).
        _line("solver", opus, in_=brief_tokens + artifact_out_tokens, out=4_000)

        # Subscription and local modes are operationally $0 — zero out.
        if self.cfg.auth_mode in ("claude-code", "local"):
            for k in items:
                items[k] = 0.0

        items["total"] = sum(v for k, v in items.items() if k != "total")
        return items

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
        elif self.cfg.auth_mode == "local":
            from .local_runtime.models import normalize_family
            _fam_label = {
                "gemma4": "Gemma 4", "qwen3": "Qwen3",
            }.get(normalize_family(self.cfg.local_model_family), "Qwen3")
            cost_line = (
                f"  cost:       [{ACCENT_HI}]$0.00 (local — {_fam_label} via "
                f"mlx-lm/llama.cpp)[/{ACCENT_HI}]"
            )
            footer = (
                "[dim]first run downloads the model "
                "(~3-20 GB depending on tier); cached for 24h.[/dim]"
            )
        else:
            bd = self.cost_breakdown(corpus_chars, days)
            estimate = bd["total"]
            # Top three line items, for transparency. Helpful when the
            # number lands higher than the user expects — they can see
            # which agent dominates and decide whether to disable it
            # (--fast / --turbo / BART_SKIP_RESEARCHER / --no-critic).
            ranked = sorted(
                ((k, v) for k, v in bd.items() if k != "total" and v > 0),
                key=lambda kv: kv[1], reverse=True,
            )[:3]
            top3 = ", ".join(f"{k} ${v:.2f}" for k, v in ranked) if ranked else ""
            cost_line = f"  est. cost:  [{ACCENT_HI}]~${estimate:.2f}[/{ACCENT_HI}]{critic_note}"
            if top3:
                cost_line += f"\n  top items:  [dim]{top3}[/dim]"
            footer = (
                "[dim]final cost depends on response lengths and cache hits. "
                "knobs: [/dim][white]--fast[/white][dim] / [/dim][white]--turbo[/white]"
                "[dim] / [/dim][white]--no-critic[/white][dim] each cut sizable items.[/dim]"
            )

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
    def _run_sidecar(self, name, fn, *, fallback, record, logger):
        """Run a sidecar (a corpus-derived index/card the main pipeline can
        live without). On *any* exception: log a warning, record an
        `outcome=fallback` artifact + a `warn` entry on the run record, and
        return `fallback` instead of letting the error abort the run. On
        success: record `ok` and return the value.

        This is the uniform replacement for the sidecars' previous mix of
        ad-hoc try/except fallbacks and (worse) no handling at all — a
        failing notation or whimsy index used to take the whole packet down.
        """
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 — sidecars degrade, never abort
            logger.warning("sidecar %s failed — using fallback: %s", name, e)
            record.record_warning("warn", name, f"fell back to empty ({type(e).__name__}: {e})")
            record.record_artifact(name, "fallback")
            return fallback
        record.record_artifact(name, "ok")
        return value

    def _extract_notation_card(self, corpus_brief: str, brief_ctx: AgentContext) -> str:
        path = self.paths.checkpoints_dir / "notation_card.md"
        if path.exists() and path.stat().st_size > 50:
            self.console.print(f"  [dim]✓ reused notation card ({path.stat().st_size} chars)[/dim]")
            return path.read_text()
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] extracting notation card "
            f"[dim](haiku · cached for the rest of the run)[/dim]"
        )
        text = self._run_sidecar(
            "notation",
            lambda: NotationExtractorAgent(brief_ctx).extract(corpus_brief),
            fallback="", record=self.run_record, logger=self.logger,
        )
        if text:
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
        # Side-car. If the corpus overflows the fast-model window we'd
        # rather emit an empty index than crash the whole run — daily
        # lessons can re-derive problems on the fly when this is empty.
        # The context-too-long case keeps its own precise message; every
        # other error degrades through `_run_sidecar`.
        def _do_index():
            try:
                return ProblemIndexerAgent(full_ctx).index()
            except LLMContextTooLongError as e:
                self.logger.warning("problem_indexer skipped (context too long): %s", e)
                self.console.print(
                    "  [yellow]⚠[/yellow] problem index skipped — corpus exceeds "
                    "fast-model window; daily lessons will derive problems inline"
                )
                return []
        index = self._run_sidecar(
            "problem_indexer", _do_index,
            fallback=[], record=self.run_record, logger=self.logger,
        )
        atomic_write_json(path, index)
        return index

    def _extract_problem_index_local(self, llm, files) -> list[dict[str, Any]]:
        """Local-mode chunked variant of `_extract_problem_index`.

        Uses the same disk-cache file as the API path so a re-run with
        --resume can reuse either form. The shape of the JSON written is
        identical, so downstream `filter_for_topics` works unchanged.
        """
        path = self.paths.checkpoints_dir / "problem_index.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                data = json.loads(path.read_text())
                self.console.print(f"  [dim]✓ reused problem index ({len(data)} entries)[/dim]")
                return data
            except json.JSONDecodeError:
                pass
        from .agents.local_chunked import chunked_problem_index
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing corpus problems "
            f"[dim](local · per-file chunked)[/dim]"
        )
        index = self._run_sidecar(
            "problem_indexer",
            lambda: chunked_problem_index(llm, self.cfg, files),
            fallback=[], record=self.run_record, logger=self.logger,
        )
        atomic_write_json(path, index)
        return index

    def _extract_exam_patterns_local(self, llm, files) -> dict[str, Any]:
        """Local-mode chunked variant of `_extract_exam_patterns`."""
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
        from .agents.local_chunked import chunked_exam_pattern
        from .agents.exam_pattern import EMPTY_PATTERNS
        self.console.print(
            f"  [{ACCENT_HI}]→[/{ACCENT_HI}] indexing past-exam patterns "
            f"[dim](local · per-file chunked)[/dim]"
        )
        patterns = self._run_sidecar(
            "exam_pattern",
            lambda: chunked_exam_pattern(llm, self.cfg, files),
            fallback=dict(EMPTY_PATTERNS), record=self.run_record, logger=self.logger,
        )
        atomic_write_json(path, patterns)
        return patterns

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
        patterns = self._run_sidecar(
            "exam_pattern",
            lambda: ExamPatternAgent(full_ctx).extract(),
            fallback=dict(EMPTY_PATTERNS), record=self.run_record, logger=self.logger,
        )
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
        index = self._run_sidecar(
            "whimsy",
            lambda: WhimsyIndexerAgent(brief_ctx).index(whimsy_path.read_text()),
            fallback={}, record=self.run_record, logger=self.logger,
        )
        if index:
            atomic_write_json(path, index)
        return index

    def _make_corpus_block(self, corpus: str) -> list[dict]:
        # Cache breakpoint on the corpus block. Anthropic's prompt caching
        # gives a ~90% input-token discount on cache hits, and the 1h TTL
        # ensures the cache survives the entire run (Distiller +
        # sidecar passes for problem index, exam patterns, plus optional
        # per-day Researcher). Without 1h TTL the corpus block would have
        # to be re-cached every 5 minutes.
        return [{
            "type": "text",
            "text": "USER'S COURSE MATERIALS — primary source of truth for all generations:\n\n" + corpus,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
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
                return kind, target.read_text(), "skipped"
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
            def _on_warning(detail, _k=kind):
                self.run_record.record_warning("warn", f"author:{_k}", detail)
            if kind == "practice_exam" and solver is not None:
                from .agents import exam_pattern as _exam_pattern
                exam_patterns_full = _exam_pattern.format_for_practice_exam(exam_patterns)
                part_a = author.write(
                    "practice_exam_part_a", brief, max_tokens=max_tokens,
                    label_suffix="part_a", on_density=_on_density,
                    exam_patterns_block=exam_patterns_full,
                    on_warning=_on_warning,
                )
                part_b = solver.solve(part_a, notation_card, max_tokens=max_tokens)
                text = part_a.rstrip() + "\n\n---\n\n" + part_b.lstrip()
                # Skip the heuristic+reviewer pass for the split exam — each
                # half was already written within scope.
                atomic_write_text(target, text)
                return kind, text, "ok"
            text = author.write(
                kind, brief, max_tokens=max_tokens, label_suffix="initial",
                on_density=_on_density, on_warning=_on_warning,
            )

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
            return kind, text, "ok"

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
                    k, txt, status = fut.result()
                    results[k] = txt
                    self.run_record.record_artifact(filename, status)
                    self.console.print(f"  [{RICH_OK}]✓[/{RICH_OK}] [{done_count}/{total}] {filename}")
                except Exception as e:  # noqa: BLE001
                    self.run_record.record_artifact(filename, "failed")
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

            # Daily lessons run on cfg.daily_model (Sonnet by default), NOT
            # primary — and on the subscription backend at a lower effort
            # ("low" unless BART_DAILY_EFFORT says otherwise). Label it
            # honestly so a slow daily phase doesn't look like an Opus stall.
            _daily_model = (
                os.environ.get("BART_DAILY_MODEL_OVERRIDE", "")
                or getattr(self.cfg, "daily_model", self.cfg.primary_model)
            )
            _daily_effort = os.environ.get("BART_DAILY_EFFORT", "low")
            self.console.print(
                f"  [{ACCENT_HI}]→[/{ACCENT_HI}] starting Day {day_num:02d}"
                f"{f' — {topic[:48]}' if topic else ''}"
                f" [dim]({self._model_short(_daily_model)} · effort {_daily_effort})[/dim]"
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
                on_warning=lambda detail, _d=day_num: self.run_record.record_warning(
                    "warn", f"author:day{_d}", detail
                ),
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

        def _day_artifact_name(entry: dict[str, Any]) -> str:
            try:
                return f"Day_{int(entry.get('day')):02d}_{entry.get('date', '')}.md"
            except (TypeError, ValueError):
                return f"Day_{entry.get('day')}"

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
                    self.run_record.record_artifact(
                        filename, "skipped" if status == "cached" else "ok"
                    )
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
                    self.run_record.record_artifact(filename, "recovered")
                    self.console.print(
                        f"  [{RICH_OK}]✓[/{RICH_OK}] Day {day_num:02d} — {filename} [dim](recovered)[/dim]"
                    )
                except Exception as e:  # noqa: BLE001
                    self.run_record.record_artifact(_day_artifact_name(entry), "failed")
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

    def _finalize(self) -> None:
        """End-of-run accounting. If any artifact is `failed`, print a loud
        'PACKET INCOMPLETE' banner with the missing artifacts + the resume
        command. Reads `self.run_record`; populated by the artifact + sidecar
        paths over the run."""
        missing = self.run_record.failed_artifacts()
        if not missing:
            return
        listed = ", ".join(missing)
        self.console.print(
            Panel.fit(
                f"[bold red]⚠ PACKET INCOMPLETE[/bold red] — "
                f"{len(missing)} artifact(s) missing:\n"
                f"  [white]{listed}[/white]\n\n"
                f"[dim]Recover with:[/dim] "
                f"[bold white]./run --resume {self.paths.run_id}[/bold white]",
                border_style="red",
            )
        )

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

        # Everything that went sideways — sidecars that fell back, artifacts
        # that needed re-asks, days that stayed broken, error-level findings.
        # Distinct (amber) from the red PACKET INCOMPLETE banner in _finalize().
        self._print_warnings_panel()

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

    def _print_warnings_panel(self) -> None:
        """If anything went sideways this run (a sidecar fell back, an artifact
        needed a re-ask, a day stayed broken, an autofix did a lot, unsafe math
        survived…), print an amber 'warnings & fallbacks' panel listing each
        item. Reads `self.run_record.summary_lines()`; on a fully clean run it
        prints nothing. Distinct from the red 'PACKET INCOMPLETE' banner in
        `_finalize()` — that one means "a hole in the packet"; this one is the
        full punch-list including non-fatal degradations."""
        lines = self.run_record.summary_lines()
        if not lines:
            return
        body = "\n".join(f"[white]•[/white] {line}" for line in lines)
        self.console.print()
        self.console.print(
            Panel.fit(
                f"[bold yellow]⚠ warnings & fallbacks[/bold yellow] — "
                f"{len(lines)} item(s) this run:\n\n{body}\n\n"
                f"[dim]machine-readable detail: format_audit.json · "
                f"render_warnings.json · run.log[/dim]",
                border_style="yellow",
            )
        )

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
            warnings = build_packet(self.paths.root, manifest, run_record=self.run_record)
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
    # The full teaching contract (the motivate→name→ground→connect→contrast→apply
    # arc, voice rules, misconception-first framing) lives in the author system
    # prompt — don't restate it here; point at it and add the per-day asks.
    teaching_contract = (
        "TEACHING CONTRACT — apply the teaching contract from your system "
        "prompt for every load-bearing concept (`bart-concept-build` for each; "
        "`bart-trap-callout` kind=trap titled \"What students usually think\" "
        "for each misconception). For THIS day specifically: pick the 1–3 "
        "THRESHOLD concepts where understanding qualitatively shifts, and name "
        "the wrong-but-natural MISCONCEPTION students bring in for each.\n\n"
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
