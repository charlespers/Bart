"""AuthorAgent — generates a long-form artifact from a brief."""
from __future__ import annotations

import os
from typing import Any

from .base import Agent
from . import block_density


class AuthorAgent(Agent):
    name = "author"
    prompt_file = "author.md"

    # Truncation floor — only catches genuinely truncated output. The
    # quality floor (bart-block density) is checked separately.
    _MIN_LENGTH = 800

    def write(
        self,
        artifact_kind: str,
        brief: str,
        max_tokens: int = 16000,
        temperature: float = 0.7,
        label_suffix: str = "",
        on_density: callable = None,  # type: ignore[valid-type]
        research_slice: str = "",
        exam_patterns_block: str = "",
    ) -> str:
        cfg = self.ctx.cfg

        # Build a kind-augmented system prompt: base author.md + the slim
        # per-artifact catalog. This goes in the SYSTEM payload so prompt
        # caching gives ~90% input-token discount on subsequent calls of
        # the same artifact kind. Block catalog + skeleton no longer live
        # in the user-message brief (which changes per call).
        try:
            from ..render.block_expand import catalog_for as _catalog_for
            kind_catalog = _catalog_for(artifact_kind, subject=cfg.subject)
        except Exception:  # noqa: BLE001
            kind_catalog = ""
        if kind_catalog:
            system_for_kind = (
                self.system_prompt
                + f"\n\n# Block catalog for `{artifact_kind}` (use these)\n\n"
                + kind_catalog
            )
        else:
            system_for_kind = self.system_prompt

        user = self.ctx.corpus_block + [{
            "type": "text",
            "text": (
                f"ARTIFACT: {artifact_kind}\n"
                f"Subject: {cfg.subject} · Level: {cfg.student_level} · Style: {cfg.style} · "
                f"Daily hours: {cfg.daily_hours}\n"
                f"User guidance: {cfg.guidance}\n\n"
                f"BRIEF\n{brief}"
            ),
        }]
        if research_slice.strip():
            user.append({
                "type": "text",
                "text": (
                    "RESEARCH SLICE — verbatim corpus excerpts for this artifact's topic. "
                    "Quote these exactly when they fit; do not paraphrase.\n\n"
                    f"{research_slice}"
                ),
            })
        if exam_patterns_block.strip():
            user.append({
                "type": "text",
                "text": (
                    "EXAM PATTERNS — patterned on the user's past exams. "
                    "Match the style, phrasing, and difficulty distribution shown here.\n\n"
                    f"{exam_patterns_block}"
                ),
            })
        # Model routing:
        #   - daily lessons → cfg.daily_model (Sonnet by default; ~5× cheaper
        #     than Opus and at parity for templated bart-block emission).
        #     Block-fix at the bottom of write() still uses Haiku as a
        #     quality floor regardless.
        #   - top-level artifacts → cfg.primary_model (Opus by default).
        #   - BART_TOP_LEVEL_MODEL_OVERRIDE forces a specific model on
        #     top-level artifacts only (used by --turbo).
        #   - BART_DAILY_MODEL_OVERRIDE forces a specific model on daily
        #     lessons (escape hatch for power users).
        is_daily = artifact_kind == "daily_lesson"
        if is_daily:
            daily_override = os.environ.get("BART_DAILY_MODEL_OVERRIDE", "")
            model = daily_override or cfg.daily_model
        else:
            override = os.environ.get("BART_TOP_LEVEL_MODEL_OVERRIDE", "")
            model = override or cfg.primary_model
        label = f"author:{artifact_kind}{':' + label_suffix if label_suffix else ''}"

        text = self.ctx.llm.complete(
            model=model,
            system=system_for_kind,
            user=user,
            max_tokens=max_tokens,
            label=label,
            temperature=temperature,
        )

        # ── Truncation continuation ──────────────────────────────────
        # If the output is suspiciously short, the model probably stopped
        # mid-stream. One cheap continuation usually recovers it.
        # Budget the continuation against what's left rather than re-asking
        # for the full max_tokens — saves output tokens on near-complete
        # generations that just dipped below the floor.
        if len(text) < self._MIN_LENGTH:
            # ~4 chars per output token is the standard heuristic.
            already_tokens = max(0, len(text) // 4)
            remaining = max(1500, max_tokens - already_tokens)
            try:
                continuation = self.ctx.llm.complete(
                    model=model,
                    system=system_for_kind,
                    user=user + [
                        {"type": "text", "text":
                            f"PREVIOUS PARTIAL OUTPUT (truncated):\n\n{text}\n\n"
                            f"Continue from where this left off. Output ONLY the continuation."
                        },
                    ],
                    max_tokens=remaining,
                    label=label + ":truncation_fix",
                    temperature=temperature,
                )
                if continuation and len(continuation) > 200:
                    text = text.rstrip() + "\n\n" + continuation.lstrip()
            except Exception:  # noqa: BLE001
                pass  # Best-effort.

        # ── Quality-floor (block-density) continuation ───────────────
        # If the output is missing required design blocks, fire ONE
        # focused correction call asking only for the missing blocks.
        # The continuation prompt does NOT re-send the original brief or
        # the corpus — those are already established context. We send
        # just the artifact summary + the correction prompt.
        report = block_density.evaluate(artifact_kind, text)
        if on_density is not None:
            try:
                on_density(report)
            except Exception:  # noqa: BLE001
                pass
        # `BART_SKIP_BLOCK_FIX=1` disables this entirely (used by --turbo).
        if (
            not report.healthy
            and report.missing
            and os.environ.get("BART_SKIP_BLOCK_FIX") != "1"
        ):
            correction = report.correction_prompt()
            try:
                add = self.ctx.llm.complete(
                    model=cfg.fast_model,  # Haiku is plenty for emitting blocks
                    system=self.system_prompt,
                    user=[{
                        "type": "text",
                        "text": (
                            f"Artifact: {artifact_kind} (subject {cfg.subject})\n\n"
                            f"{correction}"
                        ),
                    }],
                    max_tokens=1500,  # tight: only the missing blocks
                    label=label + ":block_fix",
                    temperature=0.4,
                )
                # Only graft on if the addition contains actual fences
                if add and "```bart-" in add:
                    text = text.rstrip() + "\n\n" + add.lstrip()
            except Exception:  # noqa: BLE001
                pass  # Best-effort.

        return text
