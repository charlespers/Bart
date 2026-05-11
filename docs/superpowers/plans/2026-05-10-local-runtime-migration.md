# Local Runtime Migration: Replace Gemma+Ollama with Qwen3 + MLX/llama.cpp

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken Gemma+Ollama local backend with a Qwen3-family model running on MLX (Apple Silicon priority) or llama-cpp-python (CUDA/CPU fallback), with grammar-constrained JSON, prefix caching, retry/heartbeat resilience, and 100% automated setup so the user only types `./run`.

**Architecture:**
- New module `bart/local_runtime/` owns hardware detection, model catalog, engine + weight installation, server lifecycle, and an OpenAI-compatible client.
- New `LocalBackend` in `bart/backends.py` replaces `OllamaBackend`. It speaks the same protocol as the Anthropic backends so the orchestrator does not change.
- Apple Silicon → `mlx-lm` (native unified memory; ~2-3× faster than llama.cpp on M-series). CUDA / Linux / CPU → `llama-cpp-python[server]` with GBNF grammars for JSON.
- Models bundled by tier: Qwen3.6-27B (≥24 GB unified or 24 GB VRAM), Qwen3-Coder-30B-A3B MoE (≥18 GB), Qwen3-14B (12-16 GB), Qwen3-8B (8-12 GB), Qwen3-4B (CPU / minimum). All Apache 2.0.
- Setup wizard detects hardware and picks a tier with no manual model pick required.

**Tech Stack:**
- `mlx-lm` (Apple) — `pip install mlx-lm`, ships with `mlx_lm.server` exposing OpenAI-compatible API.
- `llama-cpp-python[server]` (CUDA/CPU) — `pip install 'llama-cpp-python[server]'`, ships with OpenAI-compatible server with built-in GBNF.
- `huggingface_hub` for downloads with resume + checksums.
- `openai` Python client (lightweight) or stdlib `urllib`-based client for HTTP calls.
- Existing rich/pydantic/pytest stack.

---

## File Structure

| Path | Purpose |
|---|---|
| `bart/local_runtime/__init__.py` | Public API: `prepare(cfg, console) -> RuntimeHandle`, `RuntimeHandle.url`, `.shutdown()`. |
| `bart/local_runtime/hardware.py` | Detect platform (darwin-arm64 / linux-cuda / linux-cpu / darwin-intel), unified RAM or VRAM, recommend tier. |
| `bart/local_runtime/models.py` | Static catalog: name → HF repo + filename + format (MLX/GGUF) + min RAM + preferred quant per platform. |
| `bart/local_runtime/installer.py` | Pip-installs missing engine package into current venv. Downloads weights via huggingface_hub. Disk-space check. Resume support. |
| `bart/local_runtime/server.py` | Starts mlx-lm or llama-cpp-python server as subprocess. Picks free port. Wait-for-ready healthcheck. atexit cleanup. |
| `bart/local_runtime/client.py` | OpenAI-compatible HTTP client. Streaming, retry+backoff, heartbeat events, num_predict capping. |
| `bart/local_runtime/grammar.py` | JSON-Schema → GBNF compiler. Used by LocalBackend when `response_format="json"` and a schema is provided. |
| `bart/local_runtime/cache.py` | 24h cache file + on-disk model directory. Replaces local_setup cache logic. |
| `bart/backends.py` | Add `LocalBackend`. Remove `OllamaBackend` and ~600 lines of dead Gemma code. |
| `bart/config.py` | Setup wizard — automated tier pick. Remove Gemma copy. `auth_mode` becomes `"local"`. |
| `bart/local_setup.py` | **Delete.** Functionality moves to `bart/local_runtime/`. |
| `bart/telemetry.py` | Remove gemma rows, add qwen3-family rows ($0 cost). |
| `bart/agents/base.py` | Remove local_mode_primer prepend (Qwen has system role). |
| `bart/agents/local_chunked.py` | Re-target to LocalBackend. |
| `bart/agents/author.py:156` | Block-fix uses `system_for_kind` not `self.system_prompt`. |
| `prompts/local_mode_primer.md` | **Delete.** Qwen doesn't need the primer. |
| `bart/__main__.py` | Replace `cleanup` Gemma copy with local_runtime cache cleanup. |
| `requirements.txt` | Add `huggingface_hub`. mlx-lm and llama-cpp-python are platform-conditional, installed by installer.py at runtime. |
| `tests/test_local_runtime.py` | New: hardware detection, model catalog, grammar compiler. |
| `tests/test_local_setup.py` | **Delete** (replaced by test_local_runtime.py). |

---

## Task Breakdown

### Task A: Scaffold local_runtime module + hardware detection

**Files:**
- Create: `bart/local_runtime/__init__.py`
- Create: `bart/local_runtime/hardware.py`
- Create: `tests/test_local_runtime.py`

- [ ] Write failing test for `hardware.detect()` returning a `Platform` dataclass with `os`, `arch`, `accelerator`, `ram_gb`, `vram_gb`.
- [ ] Implement `hardware.py`: probe `platform.system()`, `platform.machine()`, `psutil.virtual_memory().total`, `nvidia-smi` for VRAM. Return `Platform(os="darwin", arch="arm64", accelerator="metal", ram_gb=64.0, vram_gb=None)` on M-series Mac.
- [ ] Add `recommended_tier(p: Platform) -> str` mapping platform to model size key.
- [ ] Verify tests pass.
- [ ] Commit: `feat(local_runtime): add hardware detection and tier recommendation`

### Task B: Model catalog

**Files:**
- Create: `bart/local_runtime/models.py`

- [ ] Define `Model` dataclass: `key`, `display_name`, `hf_repo`, `hf_filename`, `format` ("mlx" | "gguf"), `bytes_on_disk`, `min_ram_gb`, `context_window`, `supports_tools`, `supports_thinking`.
- [ ] Populate catalog with Qwen3.6-27B / Qwen3-Coder-30B-A3B / Qwen3-14B / Qwen3-8B / Qwen3-4B in both MLX 4-bit and GGUF Q4_K_M (Unsloth dynamic where available). Include Mistral-Small-3.2-24B as ultimate fallback.
- [ ] Write `pick_model(platform, tier) -> Model` that combines platform.accelerator with tier to choose MLX vs GGUF.
- [ ] Test: each tier resolves to a valid model on each platform.
- [ ] Commit: `feat(local_runtime): add model catalog with Qwen3 family`

### Task C: Cache layer

**Files:**
- Create: `bart/local_runtime/cache.py`

- [ ] `cache_dir()` returns `~/.cache/bart/models/` (XDG-respecting on Linux).
- [ ] `is_downloaded(model) -> bool` checks expected files exist + size matches.
- [ ] `touch(model)` / `evict_stale(ttl=24h)` carry over from `local_setup.py` but operate on filesystem layout, not Ollama API.
- [ ] Tests for each.
- [ ] Commit: `feat(local_runtime): add filesystem cache for downloaded weights`

### Task D: Installer (engine + weights)

**Files:**
- Create: `bart/local_runtime/installer.py`
- Modify: `requirements.txt` — add `huggingface_hub>=0.24`.

- [ ] `ensure_engine(platform) -> str` — returns the engine package name. If not importable, runs `subprocess.run([sys.executable, "-m", "pip", "install", pkg])`. Apple→`mlx-lm`, others→`llama-cpp-python[server]`. Use `--quiet --progress-bar off`.
- [ ] `ensure_weights(model, console) -> Path` — uses `huggingface_hub.hf_hub_download` with `local_dir=cache_dir() / model.key`, `resume_download=True`. Stream a Rich progress bar.
- [ ] Pre-download disk-space check using `shutil.disk_usage`.
- [ ] Tests with mocked subprocess + mocked HF download.
- [ ] Commit: `feat(local_runtime): automated engine + weights installer`

### Task E: Server lifecycle

**Files:**
- Create: `bart/local_runtime/server.py`

- [ ] `start_server(model, port, n_ctx, n_threads) -> Server` — subprocess for `mlx_lm.server` or `python -m llama_cpp.server`. Captures stderr to a log file. Polls `/v1/models` until 200 (max 60s).
- [ ] `Server.shutdown()` SIGTERM, then SIGKILL after 5s.
- [ ] Register `atexit` handler so Ctrl-C doesn't leave orphans.
- [ ] Pick free port via `socket.bind(("",0)).getsockname()[1]`.
- [ ] Test with stub server binary.
- [ ] Commit: `feat(local_runtime): subprocess-managed inference server`

### Task F: GBNF grammar from JSON Schema

**Files:**
- Create: `bart/local_runtime/grammar.py`

- [ ] `schema_to_gbnf(schema: dict) -> str` — handle the JSON Schema subset the agents actually use: object with named properties (string/integer/array of string/object), required vs optional, enum, simple oneOf. Emit GBNF with `root ::= object`.
- [ ] Test against the actual schemas in `bart/agents/problem_indexer.py`, `exam_pattern.py`, `topic_distiller.py`, `whimsy_indexer.py`.
- [ ] Smoke test: feed grammar to `llama_cpp.LlamaGrammar.from_string()` and confirm it parses (skip in CI if llama_cpp not installed).
- [ ] Commit: `feat(local_runtime): JSON Schema to GBNF compiler`

### Task G: OpenAI-compatible client

**Files:**
- Create: `bart/local_runtime/client.py`

- [ ] `LocalClient(base_url)` with `complete(model, messages, max_tokens, temperature, response_format=None, schema=None, stop=None, stream=True, on_heartbeat=None) -> str`.
- [ ] Use `urllib` (no extra dep) — POST `/v1/chat/completions` with SSE stream parsing.
- [ ] Cap `max_tokens` against `n_ctx - approx_input_tokens - 256` to fix audit bug #2.
- [ ] Retry: 4 attempts, exponential backoff, honor `Retry-After`. Distinguish 5xx (retry) from 4xx (fail fast).
- [ ] Heartbeat: emit `("heartbeat", {"chars": n})` every 5s of streaming.
- [ ] If `schema` set, attach `grammar` (llama-cpp) or `response_format={"type":"json_schema","json_schema":{...}}` (mlx-lm).
- [ ] Estimate tokens with `tiktoken.get_encoding("cl100k_base")` if installed, else `len(text)/3.5`.
- [ ] Tests with httpserver fixture.
- [ ] Commit: `feat(local_runtime): OpenAI-compatible streaming client with retry`

### Task H: RuntimeHandle + public API

**Files:**
- Modify: `bart/local_runtime/__init__.py`

- [ ] Export `prepare(cfg, console) -> RuntimeHandle`. Internally: detect hardware → pick model → ensure engine + weights → start server → return handle.
- [ ] `RuntimeHandle` has `.client`, `.model_key`, `.context_window`, `.shutdown()`.
- [ ] Wire end-of-run cleanup so the server stops and weights stay cached for 24h.
- [ ] Commit: `feat(local_runtime): top-level prepare() entry point`

### Task I: LocalBackend in backends.py

**Files:**
- Modify: `bart/backends.py` — add `LocalBackend`, delete `OllamaBackend`.

- [ ] `LocalBackend(telemetry, runtime: RuntimeHandle, on_event=None)` implements the same `complete(model, system, user, max_tokens, label, ...)` shape as the Anthropic backends.
- [ ] System message goes through as `{"role":"system"}` (Qwen supports natively).
- [ ] When `response_format="json"` and a schema is in scope, builds GBNF and passes to client.
- [ ] On retry-after-echo (re-emit) or empty response, retry once with `temperature += 0.1` but **keep** json-mode/grammar (fixes audit bug #5).
- [ ] Heartbeat events forwarded to `on_event("heartbeat", {...})` so orchestrator UI shows progress.
- [ ] Delete the entire `OllamaBackend` class (~280 lines) and its helpers `_hard_num_ctx_cap`, `_KV_BYTES_PER_TOKEN`, `_WEIGHTS_BYTES`, `_input_overlap_ratio`, `_sanitize_local_output` if unused after delete.
- [ ] Tests: golden-path complete() with mocked client; retry-on-5xx; grammar attached on json mode.
- [ ] Commit: `refactor(backends): replace OllamaBackend with LocalBackend`

### Task J: Setup wizard rewrite

**Files:**
- Modify: `bart/config.py`

- [ ] Replace the Gemma model picker block (lines ~140-310) with: detect hardware → show "we'll use {model.display_name} for you" → require user to confirm Y/N → save.
- [ ] Drop `auth_mode == "ollama-local"` rename to `"local"`.
- [ ] Drop the `_remap_old_gemma_placeholders` migration (clean break).
- [ ] Provide a hidden `--advanced` flag that lets power-users override model_key.
- [ ] Tests for each platform-tier path.
- [ ] Commit: `feat(config): hardware-driven setup wizard, drop Gemma copy`

### Task K: Migrate agents

**Files:**
- Modify: `bart/agents/base.py`
- Modify: `bart/agents/local_chunked.py`
- Modify: `bart/agents/author.py` line ~156
- Delete: `prompts/local_mode_primer.md`

- [ ] `base.py`: drop the `auth_mode == "ollama-local"` primer prepend block. The system prompt is now just the file's contents.
- [ ] `local_chunked.py`: it currently checks `auth_mode == "ollama-local"` to gate map-reduce. Switch to `auth_mode == "local"` and `cfg.context_window < 32_000`.
- [ ] `author.py`: in `_block_fix_continuation`, pass `system=system_for_kind` (not `self.system_prompt`) so the kind_catalog is included.
- [ ] Delete `prompts/local_mode_primer.md`.
- [ ] Commit: `refactor(agents): drop local_mode_primer, fix block_fix system prompt`

### Task L: Delete legacy files + update __main__/telemetry

**Files:**
- Delete: `bart/local_setup.py`
- Modify: `bart/telemetry.py` — drop Gemma rows, add Qwen3 rows ($0).
- Modify: `bart/__main__.py` — replace `cleanup` cmd, drop `local_setup` import.
- Delete: `tests/test_local_setup.py`

- [ ] Confirm no external callers of `local_setup` survive (`grep -r local_setup bart/ tests/`).
- [ ] Wire `cleanup` cmd to `local_runtime.cache.purge_all()`.
- [ ] Commit: `refactor: remove legacy local_setup module and Gemma telemetry`

### Task M: Orchestrator parallel handling

**Files:**
- Modify: `bart/orchestrator.py`

- [ ] When `cfg.auth_mode == "local"`, set `effective_max_parallel = runtime.parallel_slots` (1 for mlx-lm, configurable for llama-cpp-python). Document.
- [ ] Add a clear warning if user passes `--max-parallel 4` but server only supports 1.
- [ ] Commit: `fix(orchestrator): cap parallel calls to local server slot count`

### Task N: Tests + smoke run

**Files:**
- Modify/create: `tests/test_local_runtime.py` (already partial from earlier tasks)
- Modify: `tests/test_concurrency_guard.py` — drop ollama-specific cases.
- Create or update: integration smoke

- [ ] Unit tests pass: `pytest tests/ -q`.
- [ ] Smoke test: `./run setup` then a 1-day `./run --days 1` against a small model (Qwen3-4B) to keep CI fast — verify packet renders, JSON valid, no truncation.
- [ ] Commit: `test: rewrite local-mode tests for new runtime`

### Task O: README + docs

**Files:**
- Modify: `README.md`

- [ ] Replace Ollama setup section with the new "just run ./run" flow.
- [ ] Document the model catalog and where weights live (`~/.cache/bart/models/`).
- [ ] Update the "speed presets" section if any of `--fast`/`--turbo` semantics shifted.
- [ ] Commit: `docs: update README for new local runtime`

---

## Self-Review Checklist (post-write)

- All ~46 audit findings have a target task: (1) system-prompt drop fixed by LocalBackend (Task I), (2) num_predict cap fixed by client (Task G), (3) retry/backoff added (Task G/I), (4) streaming + heartbeat added (Task G), (5) JSON-mode preserved on retry (Task I) and grammar-enforced (Task F), (6) parallel serialization addressed (Task M), (7) token estimator improved (Task G via tiktoken), (8) primer removed (Task K), (9) echo threshold deleted along with old code (Task I — replaced by deterministic grammar), (10) block-fix kind_catalog (Task K).
- No placeholders. Each step shows what to write.
- Type consistency: `RuntimeHandle.client` / `.model_key` / `.shutdown()` referenced consistently.

## Out of Scope (follow-up plans)

- Speculative decoding (Qwen3-1.7B draft model). Track as separate plan once base path is stable.
- TurboQuant integration. Wait for upstream llama.cpp PRs to merge.
- ExLlamaV2 path for max-speed RTX 4090. Add when a CUDA user reports it's needed.
- Hybrid pipeline (Phi-4-reasoning for math, Qwen for structuring). Add as Task P after baseline stabilizes.
