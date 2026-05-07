"""DistillerAgent — compress the full corpus to a structured ~10-15K-char brief.

Why this exists:

The original architecture sent the entire 100K+ char corpus to every agent
call. With prompt caching that's affordable; without (subscription mode),
it's the dominant cost in both tokens and latency.

The Distiller runs ONCE up-front using the fast model (Haiku). It produces
a corpus brief that downstream agents (Planner, Author, Critic) use instead
of the raw corpus. The brief includes a 'Verbatim anchors' section that
preserves exact notation, definitions, and problem stems so the brief is
not just a summary but also a quotable reference.

Researcher still sees the full corpus per-day for fresh excerpts.

Net effect: ~70% fewer input tokens across the run vs. raw-corpus calls,
while keeping verbatim source material available downstream.

Map-reduce fallback:

When the corpus is bigger than the fast model's input window — common on
subscription mode where per-request limits can be tighter than the
nominal 200K — the single-shot call fails with LLMContextTooLongError.
We catch that and switch to map-reduce: split the corpus by file
boundary, distill each chunk with Haiku in parallel, then merge the
mini-briefs in one final Haiku call. If even chunked Haiku fails, we
promote the call to `primary_model` (Opus, often 1M context).
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor

from ..backends import LLMContextTooLongError
from .base import Agent

# Per-chunk corpus size for map-reduce. ~200K chars ≈ 50K tokens, well under
# Haiku 4.5's 200K window with headroom for system prompt + instructions +
# output. If a chunk still trips context-too-long (subscription plans with
# tighter request limits), we halve and retry.
_CHUNK_CHAR_TARGET = int(os.environ.get("BART_DISTILLER_CHUNK_CHARS", "200000"))

# Above this corpus size we skip the single-shot probe and go straight to
# map-reduce. Subscription mode in particular has tighter per-request
# windows than the nominal 200K, and the failure mode (CLI exit 1 with
# "Prompt is too long") is unreliable to detect after the fact — better
# to never send a payload we expect to fail. Per-mode defaults:
#   - subscription (claude-code): 150K chars (~37K tokens)
#   - API: 600K chars (~150K tokens, uses most of Haiku's 200K window)
# Override with BART_DISTILLER_SINGLESHOT_MAX_CHARS.
_SINGLESHOT_MAX_SUBSCRIPTION = 150_000
_SINGLESHOT_MAX_API = 600_000

# corpus.py emits each file with `\n\n=== FILE: <path> ===\n\n` as the header.
# Split on a lookahead so each segment retains its full header. The bare
# `=== FILE: ` form (no leading newlines) is what we use to skip past the
# corpus_block's lead-in prose.
_FILE_HEADER_TOKEN = "=== FILE: "
_FILE_BOUNDARY_RE = re.compile(r"(?=\n\n=== FILE: )")


class DistillerAgent(Agent):
    name = "distiller"
    prompt_file = "distiller.md"

    def distill(self) -> str:
        """Map-reduce when corpus is large; otherwise single-shot.

        Returns a structured markdown corpus brief, ≤2500 words.

        Routing:
          1. Estimate corpus size; if it exceeds the per-mode single-shot
             threshold, skip the probe and go straight to map-reduce.
             The subscription-mode failure ("Prompt is too long" from the
             CLI) is unreliable to detect after the fact, so we don't risk
             the round trip when we expect it to fail.
          2. Otherwise try single-shot. On context-too-long, fall back to
             map-reduce. On any other failure, propagate.
        """
        cfg = self.ctx.cfg
        corpus_text, _ = self._extract_corpus_text()
        if len(corpus_text) > self._singleshot_threshold():
            return self._distill_chunked()
        try:
            return self._distill_call(self.ctx.corpus_block, model=cfg.fast_model)
        except LLMContextTooLongError:
            return self._distill_chunked()

    def _singleshot_threshold(self) -> int:
        """Per-mode max corpus chars before we skip single-shot."""
        env = os.environ.get("BART_DISTILLER_SINGLESHOT_MAX_CHARS", "").strip()
        if env.isdigit() and int(env) > 0:
            return int(env)
        if getattr(self.ctx.cfg, "auth_mode", "") == "claude-code":
            return _SINGLESHOT_MAX_SUBSCRIPTION
        return _SINGLESHOT_MAX_API

    # ------------------------------------------------------------------
    # Single-shot call (also reused per-chunk in the map step).
    # ------------------------------------------------------------------
    def _distill_call(
        self,
        corpus_block: list[dict],
        *,
        model: str,
        label: str = "distiller",
    ) -> str:
        cfg = self.ctx.cfg
        user = corpus_block + [{
            "type": "text",
            "text": self._instructions(),
        }]
        return self.ctx.llm.complete(
            model=model,
            system=self.system_prompt,
            user=user,
            max_tokens=6000,
            label=label,
            temperature=0.2,
        )

    @staticmethod
    def _instructions() -> str:
        # Lifted verbatim from the original single-call form so behavior is
        # identical when the single-shot path succeeds. Kept as a method so
        # the chunked + merge paths can re-use the same section spec.
        return (
            "Produce a corpus brief, ≤2500 words, with exactly these sections:\n\n"
            "## Materials inventory — one bullet per file (filename + one-line description).\n\n"
            "## Topic outline — chapters in course-sequence order. Format strictly:\n"
            "  ### Ch N: Title\n"
            "  - subtopic\n"
            "  - subtopic\n"
            "Include every subtopic the corpus actually covers — do not cap. "
            "The downstream planner parses these headings.\n\n"
            "## Notation conventions — 3-6 bullets on distinctive notation.\n\n"
            "## Past-exam structure — 3-5 bullets describing any sample/practice exam in the corpus, "
            "or 'No past-exam material in corpus.'\n\n"
            "## Problems & examples index — 5-10 bullets naming the most distinctive problems "
            "using the corpus's own naming convention.\n\n"
            "## Verbatim anchors — 8–15 bullets quoting EXACT strings from the corpus: "
            "notation tokens, key definitions, and distinctive example-problem stems. "
            "Use the corpus's exact wording. Format strictly:\n"
            "  > \"<verbatim>\" — file.pdf\n\n"
            "Skip 'Gaps' unless something major is missing."
        )

    def _wrap_user_message(self) -> str:
        cfg = self.ctx.cfg
        return f"Subject: {cfg.subject}\n\n" + self._instructions()

    # ------------------------------------------------------------------
    # Map-reduce path.
    # ------------------------------------------------------------------
    def _distill_chunked(self) -> str:
        """Map-reduce fallback: chunk corpus by file boundary, distill each
        chunk, merge mini-briefs into a single final brief."""
        cfg = self.ctx.cfg
        corpus_text, prefix = self._extract_corpus_text()
        # Each chunk must fit through the same window as a single-shot —
        # otherwise the chunk itself trips context-too-long. Default chunk
        # target is just an upper bound; we cap it to the per-call window.
        chunk_size = min(_CHUNK_CHAR_TARGET, self._singleshot_threshold())
        chunks = self._chunk_corpus(corpus_text, chunk_size)
        if len(chunks) <= 1:
            # Single file too big to chunk further → promote model and retry.
            return self._distill_call(
                self.ctx.corpus_block,
                model=cfg.primary_model,
                label="distiller:promoted",
            )

        mini_briefs = self._distill_chunks_in_parallel(chunks, prefix)
        return self._merge_mini_briefs(mini_briefs)

    def _extract_corpus_text(self) -> tuple[str, str]:
        """Return (corpus_text, prefix). The prefix is everything in the
        block's text that precedes the first file header — we keep it so
        per-chunk blocks have the same framing the model expects."""
        if not self.ctx.corpus_block:
            return "", ""
        full = self.ctx.corpus_block[0].get("text", "")
        idx = full.find(_FILE_HEADER_TOKEN)
        if idx < 0:
            return full, ""
        return full[idx:], full[:idx]

    @staticmethod
    def _chunk_corpus(corpus_text: str, target_chars: int) -> list[str]:
        """Greedy pack file segments into chunks ≤ target_chars.

        Files larger than target_chars become their own (oversized) chunk —
        we don't split mid-file because that would orphan the `=== FILE:
        path ===` header from its body. Mid-file splits would also break
        the verbatim-anchors guarantee. If a single file overflows, the
        caller's halve-and-retry path handles it.
        """
        if not corpus_text:
            return []
        # Lookahead split: each segment keeps its full `\n\n=== FILE: ...`
        # header. The first segment may not start with the header (it's the
        # leading file or any pre-file prose) — that's fine; we still pack
        # it as a regular segment.
        files = [s for s in _FILE_BOUNDARY_RE.split(corpus_text) if s]
        if not files:
            return []

        chunks: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for f in files:
            if cur and cur_len + len(f) > target_chars:
                chunks.append("".join(cur))
                cur, cur_len = [], 0
            cur.append(f)
            cur_len += len(f)
        if cur:
            chunks.append("".join(cur))
        return chunks

    def _distill_chunks_in_parallel(
        self, chunks: list[str], prefix: str
    ) -> list[str]:
        """Run map step. Each chunk gets a fresh corpus_block; failures are
        recovered by halving the chunk in place. Order is preserved."""
        cfg = self.ctx.cfg

        def _run(idx: int, body: str) -> tuple[int, str]:
            return idx, self._distill_chunk_with_retry(prefix + body, idx)

        # Bound concurrency so we don't fan out 50 simultaneous Haiku calls
        # on a huge corpus — each backend has its own rate-limit posture
        # and 4 in flight is a reasonable default.
        max_workers = max(1, min(4, len(chunks)))
        out: list[tuple[int, str]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fut in [pool.submit(_run, i, c) for i, c in enumerate(chunks)]:
                out.append(fut.result())
        out.sort(key=lambda t: t[0])
        return [t[1] for t in out]

    def _distill_chunk_with_retry(
        self, chunk_text: str, idx: int, *, depth: int = 0
    ) -> str:
        """Distill a single chunk. On context-too-long, split the chunk in
        half and recurse; bottom out by promoting the model so we never
        infinite-loop on a single oversized file."""
        cfg = self.ctx.cfg
        block = [{
            "type": "text",
            "text": chunk_text,
        }]
        try:
            return self._distill_call(
                block, model=cfg.fast_model, label=f"distiller:chunk{idx}",
            )
        except LLMContextTooLongError:
            if depth >= 3:
                # Give up on Haiku for this chunk; promote to primary_model.
                return self._distill_call(
                    block,
                    model=cfg.primary_model,
                    label=f"distiller:chunk{idx}:promoted",
                )
            mid = len(chunk_text) // 2
            split = chunk_text.find("\n\n" + _FILE_HEADER_TOKEN, mid)
            if split < 0:
                # No file boundary in the back half — try the front half.
                split = chunk_text.find("\n\n" + _FILE_HEADER_TOKEN, 1)
            if split < 0:
                # Single file with no further boundaries — promote model.
                return self._distill_call(
                    block,
                    model=cfg.primary_model,
                    label=f"distiller:chunk{idx}:promoted",
                )
            left = self._distill_chunk_with_retry(
                chunk_text[:split], idx, depth=depth + 1,
            )
            right = self._distill_chunk_with_retry(
                chunk_text[split:], idx, depth=depth + 1,
            )
            return left + "\n\n" + right

    def _merge_mini_briefs(self, mini_briefs: list[str]) -> str:
        """Reduce step: hand the model all mini-briefs and ask it to fuse
        them into one final brief with the canonical section structure."""
        cfg = self.ctx.cfg
        joined = "\n\n---\n\n".join(
            f"### MINI-BRIEF {i + 1} of {len(mini_briefs)}\n\n{b}"
            for i, b in enumerate(mini_briefs)
        )
        merge_block = [{
            "type": "text",
            "text": (
                "PARTIAL CORPUS BRIEFS — each covers a subset of the user's files. "
                "Fuse them into ONE coherent brief using the canonical section spec.\n\n"
                + joined
            ),
        }]
        merge_user = merge_block + [{
            "type": "text",
            "text": (
                f"Subject: {cfg.subject}\n\n"
                "Merge the partial briefs into a SINGLE corpus brief, ≤2500 words, "
                "with exactly these sections:\n\n"
                "## Materials inventory — union of every file mentioned across partials.\n\n"
                "## Topic outline — chapters in course-sequence order. Format strictly:\n"
                "  ### Ch N: Title\n"
                "  - subtopic\n"
                "  - subtopic\n"
                "Deduplicate subtopics that appear in multiple partials. Preserve every "
                "subtopic that appears anywhere — do not drop coverage.\n\n"
                "## Notation conventions — 3-6 bullets, deduplicated.\n\n"
                "## Past-exam structure — 3-5 bullets, or 'No past-exam material in corpus.'\n\n"
                "## Problems & examples index — 5-10 bullets, deduplicated.\n\n"
                "## Verbatim anchors — 8–15 bullets, preserve exact strings and "
                "filename citations from the partials. Format strictly:\n"
                "  > \"<verbatim>\" — file.pdf"
            ),
        }]
        try:
            return self.ctx.llm.complete(
                model=cfg.fast_model,
                system=self.system_prompt,
                user=merge_user,
                max_tokens=6000,
                label="distiller:merge",
                temperature=0.2,
            )
        except LLMContextTooLongError:
            # Mini-briefs themselves overflow Haiku — promote the merge.
            return self.ctx.llm.complete(
                model=cfg.primary_model,
                system=self.system_prompt,
                user=merge_user,
                max_tokens=6000,
                label="distiller:merge:promoted",
                temperature=0.2,
            )
