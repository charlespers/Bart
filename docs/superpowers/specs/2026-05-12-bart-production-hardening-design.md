# bart production hardening — design

**Date:** 2026-05-12
**Status:** Approved by user; ready for implementation plan
**Goal:** Make bart *reliably* generate *correctly-rendered* study packets — closing both halves of the reported failure picture: "the harness often fails to generate content consistently" and "rampant incorrect quality (LaTeX / bart-blocks / markdown→HTML)".

## Background

The single biggest reliability bug — the `claude --print` subprocess in `ClaudeCodeBackend` inheriting the user's global `~/.claude/settings.json` (`effortLevel: xhigh` → minutes of hidden "thinking" before any artifact text; `enabledPlugins`/`superpowers` SessionStart hook → agentic skill-loading loop) → 600 s timeout on every substantial artifact — is **already fixed** (commit re-isolating the invocation: `--setting-sources project,local`, `--effort` pinned, `--disable-slash-commands`, `--strict-mcp-config`, `--tools ""`, prompt via `--system-prompt`; regression-guarded by `tests/test_claude_cli_isolation.py`). This spec is the rest of the hardening.

## Principles

- **Fail loud, not silent.** Every failure / fallback / skip surfaces in the run summary *and* the process exit code — not buried in `run.log`.
- **Validate at the source.** Catch malformed LLM output (broken block JSON, HTML-unsafe math) at generation time and re-ask, instead of only repairing it after render.
- **Defense in depth.** Keep the post-hoc autofix pass as a safety net even after source-level validation — but it should catch rare residue, not ~138 fixes per run.
- **Small, focused modules.** Split the two ~90 KB files; each unit one job.
- **Don't break what works.** The orchestrator architecture (distiller → indexers → deterministic planner → authors → reviewer-gate → render), the agent roster, and the block catalog all stay. This is hardening, not redesign.

## Out of scope

The local-model backend (`bart/local_runtime/`); changing subscription→API as the primary auth path; re-encoding the block format (a flatter non-JSON fence, or client-side JS rendering — documented as a future option if the validate-at-source approach proves insufficient); agent-content prompt quality beyond the math-safety rule; backward compatibility of already-generated packets.

---

## Phase A — Render-pipeline robustness

The "incorrect quality" half. Today's architecture is "let the LLM emit broken output, repair it after the fact": `bart/render/format_audit.py` (94 KB — 33 checks + 17 regex autofixes + a 3-stage rebuild engine) and `bart/render/lib_blocks.py` (90 KB). Root cause of the bug class: bart asks the model to emit **LaTeX inside JSON inside a markdown code-fence**, then HTML-escapes on top — a 4-layer escaping minefield. Approach: validate at the source, harden the math handling, modularize.

### A1 — Block validation module

**Where:** new `bart/render/blocks_validate.py`.

**What:** `validate_blocks(markdown: str, artifact_kind: str, subject: str) -> list[BlockError]`. For every `bart-<name>` fence in `markdown`: (a) parse the JSON body leniently (tolerate trailing commas and the model's other common slips, like `lib_blocks` already does on render); (b) check it against the block's schema (required fields present, field types right) — schemas from the single source of truth (A3); (c) check the LaTeX in string values is HTML-safe — bare `<` or `>` adjacent to an alphanumeric, any `$...$`, any `\uXXXX` literal → flag. Returns a list of `BlockError(block_index, fence_name, kind, detail)`; does **not** mutate the markdown.

### A2 — Agent-side block-fix loop

**Where:** `bart/agents/author.py` (and the orchestrator path that calls authors). Replaces/extends the existing count-only block-density continuation.

**What:** After an artifact is generated, run `validate_blocks`. If there are errors, do up to 2 targeted re-asks against `cfg.fast_model`: *"Your output had these problems: [block 3: JSON parse error '…'; block 7: missing required field 'tex']. Re-emit ONLY the corrected blocks, fenced exactly as before, nothing else."* Splice the corrections in by block index. If still broken after the re-asks: keep the block as-is **but record the `BlockError`** in the run record so it surfaces in the summary (today these are silently swallowed). The existing block-*density* check (too-few-blocks → ask for more) folds into this same loop.

### A3 — Single source of truth for block schemas

**Where:** new `bart/render/block_schemas.py` (or a `BLOCK_SCHEMAS` dict in `block_expand.py`).

**What:** Today the schemas live in three places that drift: hand-written text in `block_expand.py:render_block_catalog()` (what the agent sees), the implicit signatures of the renderer functions in `lib_blocks.py`, and ad-hoc re-validation. Consolidate to one table: `name → {required: [...], optional: [...], field_types: {...}}`. `render_block_catalog()` generates the agent-facing catalog *from* it; `validate_blocks()` validates *against* it; each renderer's kwargs are checked against it (in dev/test). Eliminates the drift.

### A4 — HTML-safe math from the start

**Where:** `prompts/author.md` + the brief builder (`orchestrator.py`); new `bart/render/math_safety.py` (or extend `sanitize.py`).

**What:**
- **Prompt teeth:** the current "Never `$...$`" line has none. Add explicit, near the top of the mechanical rules: *"Math delimiters are `\(...\)` (inline) and `\[...\]` (display) — never `$...$`. Inside math, write `\lt` and `\gt`, never bare `<` or `>`. Never use `\uXXXX` escapes — write the Unicode character directly."*
- **Pre-write sanitizer (aggressive):** `make_math_html_safe(markdown) -> markdown` — convert any surviving `$...$`/`$$...$$` to `\(...\)`/`\[...\]` (already done in `sanitize.py`; consolidate here); rewrite bare `<`/`>` *inside* math spans to `\lt`/`\gt` (so it's safe regardless of how KaTeX/HTML treats entities); decode `\uXXXX` literals to the actual character. Runs before any HTML render and before block expansion (and again after block expansion, since blocks emit math into prose).
- **Pre-write check (loud, not auto-fix):** if `$...$` or HTML-unsafe math *survives* the sanitizer, append a `severity: error` entry to `render_warnings.json` naming the artifact and span.

Net: the `\(q>0\)` HTML-leak class becomes structurally impossible — it's `\(q \lt 0\)` by the time the renderer sees it, or it's flagged loudly.

### A5 — Split the two large modules

**Where:** `bart/render/format_audit.py` → `bart/render/audit/` package; `bart/render/lib_blocks.py` → `bart/render/blocks/` package (a `blocks/` dir already exists — consolidate into it).

**What:** Pure restructure, byte-identical render output.
- `audit/` → `checks.py` (the ~33 detection functions), `fixes.py` (the ~17 autofixes, with their documented ordering), `rebuild.py` (the 3-stage corruption-detect-and-rebuild orchestration), `__init__.py` (the public `audit(run_dir, apply_fixes=...)` entry point + the registry of checks/fixes).
- `blocks/` → one module per block family (`primitives.py`, `visuals.py`, `interactives.py`, `chem.py`, `packs.py`), `_shared.py` (the `_esc_math_inner` / `_inline_md` / `_block_md` helpers — currently sprawled/duplicated), `__init__.py` (the block registry + `expand_blocks` + `render_block_catalog` — the latter now reading `block_schemas.py`).
- `lib_blocks_packs.py` (44 KB) folds into `blocks/packs.py`.

### A6 — Autofix-as-defense-in-depth

**Where:** `bart/render/audit/`.

**What:** Keep the autofix pass — it's still the last line of defense. But: if it ever applies more than a threshold (say 20) fixes in a run, add a `severity: warn` `render_warnings.json` entry ("autofix did N repairs — the source-level validation has a gap") so the regression is visible rather than papered over.

### Phase A tests

- `validate_blocks`: fixtures with valid blocks / broken JSON / missing required field / wrong type / HTML-unsafe math → assert the expected `BlockError` list.
- `make_math_html_safe`: `\(q>0\)`, `\(T_1<T_2\)`, `$x$`, `$$y$$`, `τ`, already-safe math, math-looking text in code fences (must not touch) → assert HTML-safe, idempotent output.
- File split: render a fixture packet (`examples/sample_notes.md` through the pipeline) before and after the split → assert byte-identical HTML.
- Regressions: the `bart-mnemonic-card` `Invalid \escape` and the `\(q>0\)` leak from the 2026-05-11 packet → assert validated/sanitized correctly.

---

## Phase B — Reliability hardening

The rest of the "generate content consistently" half.

### B1 — `_run_streaming` read-loop watchdog

**Where:** `bart/backends.py` (`ClaudeCodeBackend._run_streaming`).

**What:** The `for line in proc.stdout:` loop has **no timeout** — only the trailing `proc.wait(timeout=self._timeout_s)` does, and it's only reached after the loop exits. A subprocess that hangs producing zero output (e.g. its connection dies during laptop sleep) → bart blocks indefinitely (observed: 23+ min). Fix: read stdout in a daemon thread that pushes lines onto a `queue.Queue`; the main loop does `queue.get(timeout=idle_timeout)` — if nothing arrives for `idle_timeout` seconds, kill the subprocess and raise `LLMError("`claude` produced no output for Ns on '<label>'")`. Read stderr in a thread too so it's available to report on a hang (today it's only read after `proc.wait()`). New env knobs: `BART_CLI_TIMEOUT_S` (overall, default 600), `BART_CLI_IDLE_TIMEOUT_S` (default 150).

### B2 — No silent partial runs

**Where:** `bart/orchestrator.py`.

**What:** Maintain a structured run record: per artifact (top-level + each daily lesson + each sidecar), an outcome — `ok` / `failed` / `recovered` (needed retry/re-ask) / `skipped` / `fallback` (sidecar used its empty fallback). At end of run: if anything is `failed`, print a clear banner — `PACKET INCOMPLETE — N artifact(s) missing: [01_SCHEMATICS, Day_03]. Recover with: ./run --resume <run_id>` — and exit non-zero (see C3). Today: failed daily lessons are dropped from the output and the run still exits 0 with the "your study packet is ready" box.

### B3 — Uniform sidecar error handling

**Where:** `bart/orchestrator.py`.

**What:** `notation_extractor` and `whimsy_indexer` currently have **no** exception handling — any error in them aborts the whole run; meanwhile `problem_indexer` and `exam_pattern` already have empty-fallbacks. Introduce `_run_sidecar(name, fn, *, fallback, record)` — runs `fn()`, on any exception logs a warning, records `outcome=fallback`, returns `fallback`. Route all sidecars through it.

### B4 — No swallowed continuation errors

**Where:** `bart/agents/author.py`.

**What:** The truncation-fix and block-fix continuations are wrapped in bare `except Exception: pass`. Change to log a warning and record `outcome=recovered`-or-`failed` on the artifact. (Unifies with A2's loop — the block-fix continuation *is* A2's loop.)

### B5 — Retry jitter

**Where:** `bart/backends.py` (the API and local retry loops).

**What:** Add ±25 % random jitter to the exponential-backoff and `Retry-After` waits, so concurrent threads that hit a 429 simultaneously don't all retry in lockstep and collide again.

### B6 — Docstring fix

**Where:** `bart/backends.py` module docstring.

**What:** It says "the Claude Code CLI with `--print` is non-streaming" and "heartbeat … every 30s" — both wrong (it's `--output-format stream-json`; heartbeat is 15 s). Fix while in the file.

### Phase B tests

- B1 watchdog: a mock subprocess that emits nothing → `_run_streaming` raises after `idle_timeout`, not after 600 s; a mock that emits then goes quiet → raises after `idle_timeout` from the last line.
- B3 sidecar wrapper: a sidecar that raises → run continues, fallback returned, `outcome=fallback` recorded, warning logged.
- B2/C3 incomplete-packet: orchestrator with one artifact forced to `failed` → exit code non-zero, banner printed, run record reflects it.

---

## Phase C — Observability + run UX

### C1 — `--include-partial-messages`

**Where:** `bart/backends.py` (command + `_run_streaming` parser).

**What:** Pass `--include-partial-messages` to `claude --print --output-format stream-json`. The CLI then emits `stream_event` deltas (text fragments) as they arrive instead of buffering the whole response into one `assistant` event at the end. Update the parser to accumulate from the deltas → `stream_progress` events now show a real, climbing char count → the run no longer *looks* hung (the dominant cause of "it seems like it's failing" is the CLI buffering everything to the end). Keep handling the final `result` event as authoritative.

### C2 — Run-summary "warnings & fallbacks" section

**Where:** `bart/orchestrator.py` (the end-of-run report) + `bart/telemetry.py` or a small run-record module.

**What:** The end-of-run table currently shows calls / tokens / time / cost. Add a section listing everything from the structured run record that went sideways — sidecars that fell back, artifacts that needed re-asks, blocks that stayed broken, format-audit errors, missing days — so the user sees the whole picture at a glance instead of having to read `run.log` / `render_warnings.json` / `format_audit.json` separately. Those JSON files remain the machine-readable record; the summary reads from the same run record.

### C3 — Exit codes

**Where:** `bart/__main__.py`.

**What:** `0` = clean; `1` = packet generated but incomplete or with `severity: error` findings; `2` = run failed entirely (no packet). Document in the README's troubleshooting section. (Same mechanism as B2.)

### Phase C tests

- C1: feed a `stream_event` delta stream to the parser → assert accumulated text matches and intermediate `stream_progress` events were emitted with increasing counts.
- C2: a run record with a fallback + a missing day → assert the summary renders the warnings section with both.

---

## Phase D — Maintainability

Mostly subsumed by A5 (the file splits). The remainder:

### D1 — De-dupe the teaching-contract text

**Where:** `prompts/author.md` and `bart/orchestrator.py` (`_daily_lesson_brief`).

**What:** The MOTIVATE→NAME→GROUND→CONNECT→CONTRAST→APPLY teaching contract is stated in full in *both* the author system prompt and the per-day brief. Keep it in the system prompt only; the brief references it ("follow the teaching contract from your system prompt for each load-bearing concept") instead of restating it. Reduces drift and per-day token cost.

### D2 — Anything surfaced by the splits

Naming/boundary cleanups discovered while doing A5.

### Phase D tests

Covered by A5's golden-file test.

---

## Sequencing

Recommended order: **B → C → A → D**. B and C are small, low-risk, and immediately visible (no more silent partial packets, no more 23-min zombies, the run stops looking hung). A is the large one and benefits from B/C's run-record infrastructure already being in place (A2's block errors and A6's autofix-overrun warning both feed the run record from B2/C2). D folds into A. Each phase ships and is committed independently; the implementation plan breaks each into ordered steps with test checkpoints.

## Success criteria

- A `./run` on the example fixture (and a real materials set) completes with **0 `failed` artifacts**, exits `0`, and the format audit reports **≤ a handful** of autofixes (vs. ~138 today).
- Injected failure (an agent forced to raise, a sidecar forced to raise, a subprocess forced to hang) → the run surfaces it in the summary, exits non-zero, and does **not** silently drop content.
- A deliberately-malformed block (bad JSON, missing field, `\(q>0\)`) → caught by `validate_blocks`, re-asked, and either fixed or surfaced — never silently rendered broken.
- `format_audit.py` and `lib_blocks.py` no longer exist as monoliths; the largest render module is well under 30 KB; full test suite green; `examples/` fixture renders byte-identically to pre-split HTML.
