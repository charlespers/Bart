"""AuthorAgent — generates a long-form artifact from a brief."""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from .base import Agent
from . import block_density

logger = logging.getLogger(__name__)

# Match a ```bart-<name>\n<body>``` fence (mirrors block_expand._FENCE_RE).
_FENCE_RE = re.compile(r"(?ms)^```bart-([a-z0-9-]+)[^\n]*\n.*?```\s*$")

# Hard cap on block-fix re-ask iterations.
_MAX_BLOCK_FIX_PASSES = 2


def _noop_warning(detail: str) -> None:  # default on_warning callback
    return None


def _fence_spans(text: str) -> list[tuple[int, str, int, int]]:
    """Return ``(index, name, start, end)`` for every bart-fence in ``text``."""
    return [
        (i, m.group(1), m.start(), m.end())
        for i, m in enumerate(_FENCE_RE.finditer(text))
    ]


def _extract_fences(text: str) -> list[tuple[str, str]]:
    """Return ``(name, whole_fence_text)`` for every bart-fence in ``text``."""
    return [(m.group(1), m.group(0)) for m in _FENCE_RE.finditer(text)]


def _build_reask_message(errs: list, broken_indices: list[int], n_too_few: int) -> str:
    """Compose the targeted re-ask body naming each problem."""
    lines = ["Your previous output had these problems with its `bart-*` blocks:"]
    # Group errors by block index for readability.
    by_index: dict[int, list] = {}
    for e in errs:
        by_index.setdefault(e.block_index, []).append(e)
    for idx in sorted(i for i in by_index if i >= 0):
        for e in by_index[idx]:
            lines.append(f"- block {idx} (bart-{e.fence_name}): {e.kind} — {e.detail}")
    for e in by_index.get(-1, []):
        lines.append(f"- too few bart-{e.fence_name}: {e.detail}")
    lines.append("")
    parts = []
    if broken_indices:
        parts.append(
            f"re-emit the {len(broken_indices)} corrected block(s) above, "
            f"in that order (block {', then block '.join(str(i) for i in broken_indices)})"
        )
    if n_too_few:
        parts.append(f"then emit the {n_too_few} missing block(s)")
    lines.append(
        "Output ONLY " + (" and ".join(parts) if parts else "the corrected blocks")
        + " — each as a complete ```bart-<name>``` fenced JSON block, nothing "
        "else: no prose, no headings, no preamble. Math delimiters are "
        r"\(...\) / \[...\] (never $...$); inside math write \lt / \gt, never "
        "bare < or >; never \\uXXXX escapes."
    )
    return "\n".join(lines)


def _splice_block_fixes(text: str, errs: list, reask_response: str) -> str:
    """Splice the re-ask's corrected fences back into ``text``.

    Corrections for ``block_index >= 0`` errors replace the fence at that
    position (matched by name when possible, else positionally in order).
    Fences corresponding to ``too_few`` errors are appended at the end.
    Returns ``text`` unchanged if the re-ask yielded no usable fences.
    """
    returned = _extract_fences(reask_response)
    if not returned:
        return text

    broken_indices = sorted({e.block_index for e in errs if e.block_index >= 0})
    n_too_few = len({(e.fence_name, e.detail) for e in errs if e.block_index == -1})

    spans = _fence_spans(text)
    span_by_index = {idx: (name, s, e) for idx, name, s, e in spans}

    used = [False] * len(returned)
    # 1. Replacements for broken blocks — by name first.
    replacements: dict[int, str] = {}  # text-fence-index → new fence text
    for bi in broken_indices:
        if bi not in span_by_index:
            continue
        want_name = span_by_index[bi][0]
        pick = next(
            (k for k, (nm, _) in enumerate(returned) if nm == want_name and not used[k]),
            None,
        )
        if pick is None:
            # Fall back to the next unused returned fence regardless of name.
            pick = next((k for k in range(len(returned)) if not used[k]), None)
        if pick is None:
            continue
        used[pick] = True
        replacements[bi] = returned[pick][1]

    # Apply replacements right-to-left so earlier spans' offsets stay valid.
    if replacements:
        out = text
        for bi in sorted(replacements, reverse=True):
            _, s, e = span_by_index[bi]
            out = out[:s] + replacements[bi] + out[e:]
        text = out

    # 2. Append the leftover returned fences (the `too_few` additions, plus
    #    anything extra the model emitted) — capped at the count we asked for
    #    when we know it, else just take whatever's left.
    leftovers = [returned[k][1] for k in range(len(returned)) if not used[k]]
    if n_too_few:
        leftovers = leftovers[:n_too_few] if leftovers else leftovers
    if leftovers:
        text = text.rstrip() + "\n\n" + "\n\n".join(leftovers).lstrip() + "\n"
    return text


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
        on_warning: Callable[[str], None] = _noop_warning,
    ) -> str:
        cfg = self.ctx.cfg
        if on_warning is None:
            on_warning = _noop_warning

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
            except Exception as e:  # noqa: BLE001 — best-effort, but surface it
                logger.warning(
                    "truncation-fix continuation failed for %s: %s", label, e
                )
                on_warning(f"truncation-fix continuation failed for {label}: {e}")

        # ── Block validation + targeted re-ask ───────────────────────
        # Validate every bart-* fence the model emitted (broken JSON,
        # missing required field, wrong field type, HTML-unsafe math,
        # unknown block name) AND fold in the block-*density* floor
        # (too few of a required block-type). On any errors, do up to
        # `_MAX_BLOCK_FIX_PASSES` targeted re-asks against `cfg.fast_model`
        # naming each problem and asking for ONLY the corrected/missing
        # fences; splice them back in by index; re-validate. Whatever
        # stays broken after the loop is kept (the post-render autofix
        # pass is the safety net) but surfaced via `on_warning` so it
        # shows up in the run-record warnings panel — instead of being
        # silently rendered broken.
        #
        # `on_density` (a legacy telemetry hook) still gets the density
        # report. `BART_SKIP_BLOCK_FIX=1` disables the re-ask loop
        # entirely (used by --turbo).
        from ..render.blocks_validate import validate_blocks

        if on_density is not None:
            try:
                on_density(block_density.evaluate(artifact_kind, text))
            except Exception:  # noqa: BLE001
                pass

        errs = validate_blocks(text, artifact_kind, cfg.subject)
        if errs and os.environ.get("BART_SKIP_BLOCK_FIX") == "1":
            # --turbo opted out of the fix loop — say so once, loudly, instead
            # of either flooding the panel or going silent.
            on_warning(
                f"{len(errs)} bart-block issue(s) — block-fix re-ask skipped "
                f"(BART_SKIP_BLOCK_FIX=1); post-render autofix is the only net"
            )
            errs = []
        if errs:
            for _pass in range(_MAX_BLOCK_FIX_PASSES):
                if not errs:
                    break
                broken_indices = sorted({e.block_index for e in errs if e.block_index >= 0})
                n_too_few = len({(e.fence_name, e.detail) for e in errs if e.block_index == -1})
                reask = _build_reask_message(errs, broken_indices, n_too_few)
                try:
                    add = self.ctx.llm.complete(
                        model=cfg.fast_model,  # Haiku is plenty for re-emitting blocks
                        # Pass the kind-augmented system prompt so the re-ask
                        # has the bart-* schema examples (otherwise it emits
                        # plain text we'd discard).
                        system=system_for_kind,
                        user=[{
                            "type": "text",
                            "text": (
                                f"Artifact: {artifact_kind} (subject {cfg.subject})\n\n"
                                f"{reask}"
                            ),
                        }],
                        max_tokens=2500,  # enough for a few re-emitted blocks
                        label=f"{label}:block_fix:{_pass + 1}",
                        temperature=0.3,
                    )
                except Exception as e:  # noqa: BLE001 — best-effort, surface it
                    logger.warning("block-fix re-ask failed for %s: %s", label, e)
                    on_warning(f"block-fix re-ask failed for {label}: {e}")
                    break
                if not add or "```bart-" not in add:
                    # No usable fences came back — stop retrying.
                    break
                text = _splice_block_fixes(text, errs, add)
                errs = validate_blocks(text, artifact_kind, cfg.subject)

        # Whatever's still wrong after the loop: keep `text`, but surface it.
        for e in errs:
            on_warning(
                f"block {e.block_index} (bart-{e.fence_name}): {e.kind} — {e.detail}"
            )

        return text
