# Bart tokenomics Tier 1 — design

**Date:** 2026-05-09
**Status:** Approved by user; ready for implementation plan
**Goal:** Cut typical run cost ~60–70% with no quality regression

## Problem

A typical 36-day run hits ~60 Opus calls + ~60 Haiku calls and frequently exhausts API rate-limit windows. Three first-principles findings explain the bulk of the spend:

1. **Per-day Researcher full-corpus pass is additive to TopicDistiller.** The orchestrator runs TopicDistiller (one batched Haiku call producing all per-day study cards — explicitly framed in the code as "replaces 36 separate researcher calls" at `orchestrator.py:375`), and then *also* runs the per-day Researcher full-corpus pass at `orchestrator.py:419` unless `BART_SKIP_RESEARCHER=1` is set or the run is local. The Author receives both. On API mode this is 36× full-corpus reads with no clear marginal value.
2. **All daily lessons use Opus.** `author.py:83` selects `cfg.primary_model` (Opus) for every artifact including the 36 daily lessons. Sonnet 4.6 is ~5× cheaper and matches Opus on this kind of templated structured authoring.
3. **Prompt-cache TTL is the default 5-minute ephemeral.** A 30-min run thrashes the cache. Anthropic now supports `cache_control: {type: "ephemeral", ttl: "1h"}`, which stretches the cache window across the entire run for the corpus + system blocks.

## Scope (Tier 1 — this spec only)

Out of scope (deferred to future tiers): token-bucket scheduler, per-model concurrency caps, surgical reviser, local-mode batching.

### Change 1 — Skip per-day Researcher by default

**Where:** `bart/orchestrator.py:430-442` (the `local_skip` / `BART_SKIP_RESEARCHER` gate).

**What:** Invert the default. The per-day full-corpus Researcher becomes opt-in via `BART_RUN_FULL_RESEARCHER=1` (env) or a new `cfg.deep_research: bool = False` config field. TopicDistiller carries the load by default for both API and local modes.

**Why this is safe:** TopicDistiller was added explicitly to replace this pass and is described in the code as such. Local mode already operates this way and produces shippable lessons. The Author still has: brief (16 KB), per-day study card (~600 tokens from TopicDistiller), notation card, problem index, exam patterns, whimsy index, day skeleton. The full-corpus excerpts only add verbatim quotes — useful for high-rigor courses but not load-bearing.

**Quality fallback:** A single config flag (`deep_research: true`) restores the old behavior. Document it in the wizard as "use full-corpus per-day grounding (slower, higher cost, more verbatim excerpts)."

**Saving:** ~36 Haiku full-corpus calls per run. Each call sends ~100K-token corpus (cached after first call, but still pays cache-read + ~6K output × 36). Estimated -30–40% of total Haiku spend; -8–12% of total API cost; major reduction in rate-limit pressure since these are the burst-fanned 6-concurrent calls at `orchestrator.py:1014`.

### Change 2 — Sonnet 4.6 for daily lessons

**Where:** `bart/config.py` (add field), `bart/agents/author.py:83` (route).

**What:**
- Add `daily_model: str = "claude-sonnet-4-6"` to `Config`.
- In `author.py`, when `is_daily=True` and no `BART_TOP_LEVEL_MODEL_OVERRIDE` is set, use `cfg.daily_model` instead of `cfg.primary_model`.
- The truncation-fix continuation at `author.py:104` and the existing block-fix call at `author.py:139` already cascade off `model` and `cfg.fast_model` respectively — both inherit correctly.
- Top-level artifacts (schematics, whimsy, short guide, practice exam) keep Opus. Daily reviewers stay on Opus (see Tier 3 for surgical reviser).

**Why this is safe:** Daily lessons are templated bart-block emission against a tight skeleton. Sonnet 4.6 is at parity with Opus on instruction-following for structured output. The block-fix fallback already uses Haiku, so even Sonnet's output goes through a quality floor.

**Saving:** 36 Opus calls → 36 Sonnet calls. Sonnet is 5× cheaper input, ~5× cheaper output. The daily Author is the dominant line item — this is the biggest single dollar lever.

**Migration:** Existing config files load via Pydantic with the new default; no migration script needed.

### Change 3 — 1h prompt-cache TTL on corpus + brief

**Where:** `bart/backends.py:389` (the `_cache_block` helper that wraps text into ephemeral cache blocks).

**What:** Change the cache_control to `{"type": "ephemeral", "ttl": "1h"}` for blocks tagged as long-lived (corpus, brief, system prompts). Keep 5-min default for short-lived contextual blocks. Detect via the `cache_block` callsite — pass an explicit `ttl` argument, default 5m.

**Why this is safe:** 1h ephemeral cache is a paid Anthropic feature with documented behavior. Falls back gracefully if the SDK doesn't recognize the field.

**Saving:** Eliminates cache-rewrite cost on multi-call sequences that span >5 min (which is most of them — daily lessons run sequentially, each Author taking ~30s, so the corpus cache previously expired between days 10 and 11).

### Change 4 — Smarter truncation continuation

**Where:** `bart/agents/author.py:99-120`.

**What:** Two small fixes:
- Use `min(max_tokens, max_tokens - len(text)/4)` for the continuation `max_tokens` budget — currently re-requests the full budget regardless of how much was already produced.
- Add a guard: skip the continuation entirely if the artifact kind has a known short-output budget (e.g., short_guide can legitimately be <800 chars in some sections).

**Why this is safe:** Both are bounded by existing fallbacks (block-fix still runs).

**Saving:** Modest — eliminates wasted Opus output tokens on the ~20% of calls that hit the truncation path with mostly-complete output.

## Non-changes (explicitly preserved)

- Disk cache (`backends.py:_cache_key`) — already SHA256-keyed on (model, system, user, max_tokens). Untouched.
- Existing prompt-cache `cache_control: ephemeral` headers — extended (Change 3), not removed.
- Block-fix Haiku correction at `author.py:139` — already uses `cfg.fast_model`. Untouched.
- TopicDistiller batched call — already optimal. Untouched.
- `--turbo` and `BART_SKIP_BLOCK_FIX` env knobs — preserved.

## Risk register

| Risk | Mitigation |
|---|---|
| Sonnet 4.6 daily lessons miss the rigor of Opus on advanced courses | `daily_model` is configurable. User can set to `claude-opus-4-7` if they want the old behavior. Wizard exposes the choice. |
| Skipping per-day Researcher costs verbatim-quote richness | `deep_research: true` config opt-in restores it. |
| 1h TTL pricing differs from 5-min | Anthropic's docs treat this as the same ephemeral tier with a longer window — no cost change per the SDK. If we discover a pricing surprise, revert one line. |
| Continuation budget shrinkage hurts quality on legitimately long outputs | Floor the continuation budget at 1500 tokens. |

## Verification

1. Run `./run --dry-run` to confirm the orchestrator boots and all imports resolve.
2. Run a real 6-day fixture against API mode and confirm:
   - Daily lessons are written by Sonnet (label in telemetry: `author:daily_lesson` with `model=claude-sonnet-4-6`)
   - No per-day researcher call appears (unless `deep_research=true`)
   - Cache headers include `ttl: 1h` on corpus block (visible in backend debug logging)
3. Diff lesson outputs against the last cached run — quality spot-check 3 daily lessons by hand.

## Files to touch

- `bart/config.py` — add `daily_model` and `deep_research` fields
- `bart/agents/author.py` — route `is_daily` to `cfg.daily_model`; adjust continuation `max_tokens`
- `bart/orchestrator.py` — invert `BART_SKIP_RESEARCHER` default; honor `cfg.deep_research`
- `bart/backends.py` — extend `_cache_block` to accept a TTL; default 5m, opt-in 1h on long-lived blocks; thread TTL through call sites that pass corpus/brief/system
- `bart/local_setup.py` or wizard — surface the new fields with sensible defaults

## Implementation notes

- All changes are additive (new fields default to the new behavior). No data migration.
- Telemetry should record the new model selection and whether the deep researcher ran. The existing `_on_event("retry"...)` pattern at `backends.py:448` is a good template.
- Tests: there's a `tests/` directory; check for any test that hardcodes `claude-opus-4-7` and update.
