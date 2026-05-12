# bart production hardening — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline; recommended here — many tasks edit the same files: `backends.py`, `orchestrator.py`, the `render/` package) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax. Detailed rationale per change is in the spec: `docs/superpowers/specs/2026-05-12-bart-production-hardening-design.md` — cite it (`spec §B1` etc.) rather than restating.

**Goal:** Make bart reliably generate correctly-rendered packets — fix the remaining reliability failure modes, make failures loud, make render-quality bugs structurally hard, and split the two ~90 KB render modules.

**Architecture:** Four phases, landed in order **B → C → A → D**, each independently shippable and committed. B/C build a small structured "run record"; A's new validations feed into it; D folds into A. No change to the orchestrator pipeline shape, agent roster, or block catalog — this is hardening.

**Tech stack:** Python 3.13, `subprocess`/`threading`/`queue`, `pytest`. The `claude` CLI subprocess backend, the `anthropic` SDK backend. python-markdown + KaTeX render pipeline.

**Conventions:** TDD where there's testable logic (write failing test → run-fail → implement → run-pass → commit). `tests/` is `.gitignore`d (dev-only) — test files won't be committed, but **always run `.venv/bin/python -m pytest tests/ -q` before each commit** and the commit only lands if green. Commit messages: short imperative subject, end with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. Work on branch `phase1-hardening` (already created; commits so far: CLI-isolation fix, this spec).

---

## File structure (created / modified across the plan)

**New:**
- `bart/runrecord.py` — the structured run record (artifact outcomes, warnings) the summary & exit code read from. (Phase B/C)
- `bart/render/math_safety.py` — `make_math_html_safe(md) -> md` + `check_math_html_safe(md) -> [Warning]`. (Phase A)
- `bart/render/block_schemas.py` — `BLOCK_SCHEMAS`: single source of truth (name → required/optional/types). (Phase A)
- `bart/render/blocks_validate.py` — `validate_blocks(md, kind, subject) -> [BlockError]`. (Phase A)
- `bart/render/audit/` package — `__init__.py` (public `audit()` + registries), `checks.py`, `fixes.py`, `rebuild.py` (split of `format_audit.py`). (Phase A)
- `bart/render/blocks/` package — already exists as a dir; gains `__init__.py` (registry + `expand_blocks` + `render_block_catalog`), `_shared.py`, `primitives.py`, `visuals.py`, `interactives.py`, `chem.py`, `packs.py` (split of `lib_blocks.py` + `lib_blocks_packs.py`). (Phase A)

**Modified:**
- `bart/backends.py` — read-loop watchdog + idle/overall timeout env knobs + concurrent stderr read (B1); retry jitter (B5); docstring fix (B6); `--include-partial-messages` + delta parsing (C1).
- `bart/orchestrator.py` — `_run_sidecar` wrapper + route all sidecars through it (B3); populate the run record, incomplete-packet banner (B2); run-summary "warnings & fallbacks" section (C2); wire `make_math_html_safe`/`validate_blocks` into the render+author paths (A); de-dupe teaching-contract text in `_daily_lesson_brief` (D1).
- `bart/agents/author.py` — block-fix loop using `validate_blocks` (A2); stop swallowing continuation errors, record outcomes (B4).
- `bart/__main__.py` — exit codes 0/1/2 from the run record (C3).
- `bart/render/__init__.py`, `bart/render/packet.py`, `bart/render/compose.py`, anything importing `format_audit`/`lib_blocks`/`lib_blocks_packs` — update imports for the splits (A5). Old `format_audit.py`/`lib_blocks.py`/`lib_blocks_packs.py` become thin re-export shims (or are deleted if no external refs).
- `prompts/author.md` — math-safety rules with teeth (A4); teaching-contract stays here, brief references it (D1).
- `README.md` — exit-code docs (C3).
- `bart/render/sanitize.py` — fold its `$…$`→`\(…\)` conversion into `math_safety.py` (or have `math_safety` call it); keep a stable entry point. (A4)

---

# Phase B — reliability hardening

### Task B0: structured run record skeleton

**Files:** Create `bart/runrecord.py`. Test: `tests/test_runrecord.py`.

- [ ] Write `tests/test_runrecord.py`: a `RunRecord` collects `record_artifact(name, outcome)` where outcome ∈ {`ok`,`failed`,`recovered`,`skipped`,`fallback`}, `record_warning(severity, source, detail)`; `.has_failures()` true iff any artifact `failed`; `.has_errors()` true iff any artifact failed or any `severity=="error"` warning; `.summary_lines()` returns the human list of things-that-went-sideways. Assert each.
- [ ] Run: `.venv/bin/python -m pytest tests/test_runrecord.py -v` → FAIL (no module).
- [ ] Implement `bart/runrecord.py`: a small dataclass-y `RunRecord` (no deps beyond stdlib). `Orchestrator` will hold one instance.
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `feat(runrecord): structured per-run outcome+warning record`.

### Task B1: `_run_streaming` read-loop watchdog + timeout env knobs + concurrent stderr

**Files:** Modify `bart/backends.py` (`ClaudeCodeBackend._run_streaming`, `__init__`, module top for env reads). Test: extend `tests/test_concurrency_guard.py` or new `tests/test_cli_watchdog.py`. Spec §B1.

- [ ] Write `tests/test_cli_watchdog.py`: monkeypatch `subprocess.Popen` to return a fake proc whose `stdout` blocks (an iterator that sleeps forever) — assert `_run_streaming` raises `LLMError` with "no output" within ~`idle_timeout`+slack, not 600 s. Second case: stdout yields a couple lines then blocks → still raises after `idle_timeout` from the last line. Use a tiny `idle_timeout` (1–2 s) via env.
- [ ] Run → FAIL (blocks forever / wrong error).
- [ ] Implement: module-level `_CLI_TIMEOUT_S = int(os.environ.get("BART_CLI_TIMEOUT_S", "600"))`, `_CLI_IDLE_TIMEOUT_S = int(os.environ.get("BART_CLI_IDLE_TIMEOUT_S", "150"))`. In `_run_streaming`: spawn a daemon thread that iterates `proc.stdout` pushing `(line)` onto a `queue.Queue`, and a sentinel `None` on EOF; spawn another daemon thread reading all of `proc.stderr` into a list. Main loop: `q.get(timeout=_CLI_IDLE_TIMEOUT_S)` — on `queue.Empty` → `proc.kill()`, join briefly, raise `LLMError(f"`claude` produced no output for {idle}s on '{label}' (stderr: {stderr[:400] or 'empty'})")`. Also enforce an absolute deadline (`time.monotonic()` start + `_CLI_TIMEOUT_S`). On `None` sentinel → break, then `proc.wait(timeout=5)`. Return accumulated text + stderr + returncode as before.
- [ ] Run the watchdog test → PASS. Run `tests/test_claude_cli_isolation.py` (uses a fake proc with `io.StringIO` stdout — make sure the new thread-based reader still works with that fixture; adjust the fixture or the reader to iterate `proc.stdout` line-wise either way). Run full suite → green.
- [ ] Commit: `fix(backends): read-loop watchdog on the claude CLI stream — kill+raise on idle, not hang forever`.

### Task B2: orchestrator populates the run record + incomplete-packet banner + exit signal

**Files:** Modify `bart/orchestrator.py`. Test: new `tests/test_incomplete_packet.py`. Spec §B2.

- [ ] Write `tests/test_incomplete_packet.py`: construct an `Orchestrator` (or the smallest unit that does artifact-loop accounting) with the LLM monkeypatched so one daily lesson always raises; run; assert: the run completes (other artifacts present), the `RunRecord` has that artifact `failed`, `record.has_failures()` is true, and the "PACKET INCOMPLETE" banner text was emitted. (May need a small refactor to make the artifact loop testable in isolation — extract `_finalize(run_record)` if needed.)
- [ ] Run → FAIL.
- [ ] Implement: thread a `RunRecord` through the run; every artifact-generating path records `ok`/`failed`/`recovered`/`skipped`; the day-level serial-retry pass records `failed` for still-missing days instead of just logging; at the end, if `record.has_failures()`, print the banner (artifact list + `./run --resume <id>`); return the record up to `__main__` (for C3).
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `feat(orchestrator): track artifact outcomes; surface incomplete packets, stop exiting 0 on dropped days`.

### Task B3: uniform sidecar error handling

**Files:** Modify `bart/orchestrator.py`. Test: new `tests/test_sidecar_fallback.py`. Spec §B3.

- [ ] Write `tests/test_sidecar_fallback.py`: a `_run_sidecar("notation", fn=lambda: 1/0, fallback="", record=rr)` → returns `""`, logs a warning, `rr` has a `fallback` outcome / `warn` entry for "notation". And a passing fn returns its value with `ok`.
- [ ] Run → FAIL.
- [ ] Implement `_run_sidecar(name, fn, *, fallback, record, logger)`; route `notation_extractor`, `whimsy_indexer`, `problem_indexer`, `exam_pattern`, `topic_distiller` through it (the latter three already have ad-hoc fallbacks — replace with this; `notation`/`whimsy` previously had none).
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `fix(orchestrator): every sidecar gets a fallback; a failing notation/whimsy index no longer aborts the run`.

### Task B4: stop swallowing continuation errors in the author

**Files:** Modify `bart/agents/author.py`. Test: extend `tests/test_author_wiring.py`. Spec §B4.

- [ ] Write a test: make the continuation LLM call raise → assert the author still returns the original text **and** logs a warning **and** records `recovered`/`failed` on the artifact (via a passed-in callback or the run record). (If the run-record plumbing into the author is awkward, pass a small `on_warning(detail)` callback from the orchestrator.)
- [ ] Run → FAIL.
- [ ] Implement: replace the two `except Exception: pass` with `except Exception as e: logger.warning("…continuation failed for %s: %s", label, e); on_warning(...)`. (This task is partly superseded by A2, which rewrites the block-fix continuation — keep the truncation-fix one here and let A2 handle block-fix.)
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `fix(author): log+surface continuation failures instead of swallowing them`.

### Task B5 + B6: retry jitter; docstring fix

**Files:** Modify `bart/backends.py`. Test: extend an existing backends test (or skip a dedicated test for jitter — it's a 2-line change; just assert the wait is within [0.75·base, 1.25·base] in a unit test if cheap). Spec §B5, §B6.

- [ ] (Optional) test: `_jittered(base)` returns a value in `[0.75*base, 1.25*base]`.
- [ ] Implement: a `_jittered(seconds)` helper; apply it in the API-backend and local-backend retry waits (the exponential-backoff and `Retry-After`-derived sleeps). Fix the `backends.py` module docstring (it's stream-json, heartbeat is 15 s).
- [ ] Run full suite → green.
- [ ] Commit: `fix(backends): jitter retry backoff; correct stale module docstring`.

---

# Phase C — observability + run UX

### Task C1: `--include-partial-messages` + delta parsing

**Files:** Modify `bart/backends.py` (`ClaudeCodeBackend.complete` command list; `_run_streaming` event handling). Test: extend `tests/test_cli_watchdog.py` or new `tests/test_stream_partial.py`. Spec §C1.

- [ ] Write a test: feed `_run_streaming`'s parser (via a fake proc) a sequence of `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"foo"}}}` lines then a `{"type":"result","result":"foobar"}` → assert it accumulates "foo"+"bar" from deltas, emits ≥2 `stream_progress` events with increasing `chars`, and the final returned text is the `result` value (authoritative). Also keep the old `{"type":"assistant",...}` path working (some CLI versions / fallback).
- [ ] Run → FAIL.
- [ ] Implement: add `"--include-partial-messages"` to the command (after `--verbose`). In `_run_streaming`, handle `etype == "stream_event"` → dig out `event.event.delta.text` for `content_block_delta`/`text_delta` and append. Leave `assistant` and `result` handling intact.
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `feat(backends): --include-partial-messages so the claude CLI streams text deltas — run stops looking hung`.

### Task C2: run-summary "warnings & fallbacks" section

**Files:** Modify `bart/orchestrator.py` (the end-of-run report). Test: extend `tests/test_incomplete_packet.py` or new `tests/test_run_summary.py`. Spec §C2.

- [ ] Write a test: a `RunRecord` with a `fallback` (notation) + a missing day + an `error` warning → the rendered summary string contains all three (one line each, clearly labelled).
- [ ] Run → FAIL.
- [ ] Implement: after the metrics table, if `record.summary_lines()` non-empty, print a "⚠ warnings & fallbacks" panel listing them.
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `feat(orchestrator): run summary lists every fallback/retry/error, not just metrics`.

### Task C3: exit codes

**Files:** Modify `bart/__main__.py`; `README.md`. Test: new `tests/test_exit_codes.py` (or assert on the value `Orchestrator.run` returns). Spec §C3.

- [ ] Write a test: `run()` returning a record with no failures → exit 0; with a `failed` artifact (packet still made) → exit 1; a run that raised before any output → exit 2.
- [ ] Run → FAIL.
- [ ] Implement: `__main__` reads the returned `RunRecord` (or catches the top-level exception) → `sys.exit(0|1|2)`. Add a short "exit codes" subsection to the README troubleshooting.
- [ ] Run the test → PASS. Run full suite → green.
- [ ] Commit: `feat(cli): exit 0/1/2 for clean/incomplete/failed runs; document it`.

---

# Phase A — render-pipeline robustness

### Task A0: `block_schemas.py` — single source of truth

**Files:** Create `bart/render/block_schemas.py`. Modify `bart/render/block_expand.py` (`render_block_catalog` to read it). Test: `tests/test_block_schemas.py`. Spec §A3.

- [ ] Write `tests/test_block_schemas.py`: `BLOCK_SCHEMAS` has an entry for every name in the existing block registry (`_BLOCK_REGISTRY`); every schema entry's `required` fields are a subset of what the corresponding renderer accepts; `render_block_catalog("daily_lesson", subject="x")` produces a string mentioning each daily-lesson block's name and its required fields.
- [ ] Run → FAIL.
- [ ] Implement `BLOCK_SCHEMAS` (transcribe from the current hand-written catalog + the renderer signatures — this is mechanical but must be complete; iterate against the test until every registered block is covered). Rewrite `render_block_catalog` to generate from it. Keep the per-artifact whitelists and the subject-keyword domain variants.
- [ ] Run the test → PASS. Run full suite → green (esp. `test_distiller_prompt.py`, `test_exam_pattern.py`, anything that snapshots the catalog).
- [ ] Commit: `refactor(render): single source of truth for block schemas; catalog generated from it`.

### Task A1: `math_safety.py` — make HTML-safe, and check

**Files:** Create `bart/render/math_safety.py`. Modify `bart/render/sanitize.py` (delegate / keep stable entry). Test: `tests/test_math_safety.py`. Spec §A4.

- [ ] Write `tests/test_math_safety.py`: `make_math_html_safe("…\\(q>0\\)…")` → `…\\(q \\gt 0\\)…`; `"\\(T_1<T_2\\)"` → `\\(T_1 \\lt T_2\\)`; `"$x$"` → `\\(x\\)`; `"$$y$$"` → `\\[y\\]` (block, with blank lines); `"\\u03c4"` inside math → `τ`; already-safe input → unchanged; `` `code with $x$` `` (fenced/inline code) → untouched; idempotent (apply twice = once). `check_math_html_safe` on residual `$x$` → returns a Warning naming it.
- [ ] Run → FAIL.
- [ ] Implement `make_math_html_safe(md) -> md` and `check_math_html_safe(md) -> list[Warning]` (the existing `sanitize.py` `$…$`-conversion and math-`<>`-escape logic is most of the raw material — move/adapt it here; have `sanitize.py` call `math_safety` so there's one place). Note: it must protect code spans/fences and existing `\(...\)`/`\[...\]` while transforming.
- [ ] Run the test → PASS. Run `tests/test_math_fixes_extra.py`, `tests/test_raw_latex_leak.py`, `tests/test_block_lenient.py` → green (adjust if they assumed the old code path).
- [ ] Commit: `feat(render): math_safety — HTML-safe math (\\lt/\\gt, no $...$, no \\uXXXX) before render; loud check on residue`.

### Task A2: `blocks_validate.py` + agent-side block-fix loop

**Files:** Create `bart/render/blocks_validate.py`. Modify `bart/agents/author.py` (replace the count-only block-density continuation). Test: `tests/test_blocks_validate.py`, extend `tests/test_author_wiring.py`. Spec §A1, §A2.

- [ ] Write `tests/test_blocks_validate.py`: feed markdown with — a valid `bart-formula-card`; one with broken JSON (trailing-comma-after-last → must be tolerated; truly unparseable → `BlockError(kind="json")`); one missing required `tex` → `BlockError(kind="missing_field")`; one with `\(q>0\)` in a string → `BlockError(kind="unsafe_math")` (or none, if A1's sanitizer already ran first and made it safe — decide: `validate_blocks` runs *after* `make_math_html_safe`, so it should only flag genuinely-unsafe residue; test accordingly). Assert the `BlockError` list.
- [ ] Run → FAIL.
- [ ] Implement `validate_blocks(md, kind, subject) -> list[BlockError]` (lenient JSON parse like `block_expand` does; schema check against `BLOCK_SCHEMAS`; math-safety residue check).
- [ ] Write the author test: an artifact whose first generation has a block with broken JSON → the author's block-fix loop does a targeted re-ask (mock the `fast_model` call to return a corrected block) → final text has the fix spliced in; if the re-ask still fails → original kept + a warning recorded via the `on_warning` callback.
- [ ] Implement in `author.py`: after generation, `errs = validate_blocks(text, kind, subject)`; if `errs`, build a re-ask message naming each error and the offending fence, call `fast_model`, splice corrected fences back by index; loop ≤2×; on residual errors, keep text + `on_warning`. Fold the existing "too few blocks" density check into the same loop (a too-few-blocks "error" → re-ask for the missing ones).
- [ ] Run all the new + touched tests → PASS. Run full suite → green.
- [ ] Commit: `feat(render+author): validate every bart-block at generation; targeted re-ask on broken JSON/missing-field/unsafe-math`.

### Task A3: wire math-safety + validation into the orchestrator render path

**Files:** Modify `bart/orchestrator.py` (and/or `bart/render/packet.py`) — call `make_math_html_safe` on each artifact's markdown before render & block-expansion (and after block-expansion on the prose, since blocks emit math); push `check_math_html_safe` residue + `validate_blocks` residue into the `RunRecord`/`render_warnings.json`. Test: extend `tests/test_corpus*.py` or a small render-path test. Spec §A4.

- [ ] Write a test: a fixture markdown with a `$x$` and a `\(q>0\)` → after the render path, the HTML has neither (it's `\(x\)` / `\(q \gt 0\)`), and `render_warnings.json` got a note that conversions happened (info severity), no `error`.
- [ ] Run → FAIL.
- [ ] Implement the wiring (one obvious place: wherever `sanitize.normalize_md` is currently called — `make_math_html_safe` becomes part of that pre-render normalization; the post-block-expansion re-pass too).
- [ ] Run the test + full suite → green.
- [ ] Commit: `feat(render): math-safety + block-validation residue flows into render_warnings + run record`.

### Task A4: split `format_audit.py` → `render/audit/` package

**Files:** Create `bart/render/audit/{__init__,checks,fixes,rebuild}.py`. Modify importers (`bart/render/__init__.py`, `bart/render/packet.py`, `bart/__main__.py`'s `format` command, `bart/commands.py`). Delete (or shim) `bart/render/format_audit.py`. Test: golden-file render test (new `tests/test_render_golden.py`) + existing format tests. Spec §A5.

- [ ] Write `tests/test_render_golden.py`: render `examples/sample_notes.md` through the full pipeline (or the smallest reproducible packet build) → capture the HTML of each output file → store as a golden on first run (or compute now, before the split). The test asserts post-split HTML == pre-split HTML byte-for-byte.
- [ ] Run it now (pre-split) to capture the golden.
- [ ] Mechanically split `format_audit.py`: move the ~33 `_check_*` fns → `checks.py`; the ~17 `_fix_*` fns + their ordering list → `fixes.py`; the 3-stage `audit()` orchestration + corruption-detect + rebuild-from-markdown → `rebuild.py`; `__init__.py` re-exports `audit` and the public surface. Keep internal regex/constants with their function. Update all importers. Replace `format_audit.py` with `from .audit import *  # back-compat shim` or delete it if nothing imports it directly.
- [ ] Run `tests/test_render_golden.py` → PASS (byte-identical). Run full suite → green.
- [ ] Commit: `refactor(render): split format_audit.py (94 KB) into render/audit/{checks,fixes,rebuild}`.

### Task A5: split `lib_blocks.py` + `lib_blocks_packs.py` → `render/blocks/` package

**Files:** Create/extend `bart/render/blocks/{__init__,_shared,primitives,visuals,interactives,chem,packs}.py`. Modify `bart/render/block_expand.py` (imports the registry from `blocks/`), importers. Delete (or shim) `lib_blocks.py`, `lib_blocks_packs.py`. Test: `tests/test_render_golden.py` (same golden). Spec §A5.

- [ ] Mechanically split: group the ~30+ block renderer fns by family into the `blocks/*.py` modules; move `_esc_math_inner`/`_inline_md`/`_block_md` (and any sibling helpers) into `_shared.py` (single copy); `blocks/__init__.py` builds `_BLOCK_REGISTRY` (name → fn) from the family modules and exposes `expand_blocks`, `render_block_catalog` (the latter delegating to `block_schemas.py` from A0). `block_expand.py` becomes thin (or merges into `blocks/__init__.py`). Update importers; shim/delete the old files.
- [ ] Run `tests/test_render_golden.py` → PASS (byte-identical). Run full suite → green.
- [ ] Commit: `refactor(render): split lib_blocks.py + lib_blocks_packs.py (134 KB) into render/blocks/{primitives,visuals,interactives,chem,packs,_shared}`.

### Task A6: autofix-overrun warning

**Files:** Modify `bart/render/audit/rebuild.py` (or wherever the fix count is tallied). Test: extend a format test. Spec §A6.

- [ ] Write a test: run the audit with `apply_fixes=True` on a markdown crafted to trigger >20 fixes → `format_audit.json`/run record gets a `severity=warn` "autofix did N repairs — source-level validation has a gap" entry.
- [ ] Run → FAIL.
- [ ] Implement: tally `fixes_applied`; if `> AUTOFIX_WARN_THRESHOLD` (20), append the warning.
- [ ] Run the test + full suite → green.
- [ ] Commit: `feat(render): warn when the autofix pass does an unusual number of repairs`.

---

# Phase D — maintainability leftovers

### Task D1: de-dupe the teaching-contract text

**Files:** Modify `prompts/author.md` (keep the full contract here), `bart/orchestrator.py` (`_daily_lesson_brief` — reference it, don't restate). Test: extend `tests/test_author_wiring.py` / a brief-building test. Spec §D1.

- [ ] Write/extend a test: the daily-lesson brief no longer contains the verbatim "MOTIVATE → NAME → GROUND" block (it's in the system prompt); it does contain a short pointer ("follow the teaching contract from your system prompt"). The system prompt still contains the full contract.
- [ ] Run → FAIL.
- [ ] Implement: trim the duplicated paragraph from `_daily_lesson_brief`, leave the pointer.
- [ ] Run the test + full suite → green.
- [ ] Commit: `refactor(prompts): teaching contract lives in the author system prompt; the brief references it`.

### Task D2: cleanups surfaced during A4/A5

- [ ] Address any naming/boundary issues found while splitting (e.g. dead code, mis-scoped helpers). Small, opportunistic. Commit separately if material.

---

## Final verification (after all phases)

- [ ] `.venv/bin/python -m pytest tests/ -q` → all green.
- [ ] `cp examples/sample_notes.md materials/ && ./run --dry-run` → boots, extracts, plans, no errors (then remove the fixture).
- [ ] `./run preview docs/superpowers/specs/2026-05-12-bart-production-hardening-design.md --no-open` → renders without errors (sanity-checks the render pipeline end to end on a real markdown doc).
- [ ] `git log --oneline phase1-hardening` → the commit history is clean and phase-grouped.
- [ ] Summarize for the user; offer to merge to `main` (don't merge without asking).

---

## Plan self-review

- **Spec coverage:** A1✓(A2 task), A2✓(A2), A3✓(A0), A4✓(A1,A3), A5✓(A4,A5), A6✓(A6); B1✓(B1), B2✓(B2), B3✓(B3), B4✓(B4), B5✓(B5+B6), B6✓(B5+B6); C1✓(C1), C2✓(C2), C3✓(C3); D1✓(D1), D2✓(D2). Run record (spec §B2/C2) → Task B0. All spec changes have a task.
- **Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Task code is described at the what+where level (deliberate — see the plan header; the spec has the why; the implementer here has codebase context). The two "mechanical split" tasks (A4, A5) are genuinely mechanical (move functions, update imports) and gated by a byte-identical golden test — that's the right safety net for a pure restructure.
- **Type consistency:** `RunRecord` API (`record_artifact`, `record_warning`, `has_failures`, `has_errors`, `summary_lines`) used consistently in B0/B2/B3/B4/C2/C3. `BlockError(block_index, fence_name, kind, detail)` and `validate_blocks(md, kind, subject)` consistent across A2/A3. `make_math_html_safe`/`check_math_html_safe` consistent across A1/A3. `BLOCK_SCHEMAS` consistent across A0/A2. `on_warning` callback into the author consistent across B4/A2. OK.
