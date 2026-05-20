"""Tier registry — resolve a tier name to its TierSpec.

The orchestrator stays tier-agnostic: it asks for ``get_tier(name)``
and uses the spec to find prompts, agent roster, model routing, and
quality-floor settings. Adding a tier means dropping a folder under
``architectures/`` and registering it here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

ARCHITECTURES_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TierSpec:
    """Everything a tier exposes to the orchestrator.

    Tiers are *contracts*, not subclasses: the orchestrator looks up
    prompt files by ``role`` and trusts the tier to have a prompt for
    every role it needs. Missing roles fall back to the pro tier (the
    canonical baseline) — keeps mid/local minimal without forcing them
    to ship a prompt for every niche role.
    """

    name: str
    description: str
    prompts_dir: Path
    # Roles the tier itself implements (others fall back to pro).
    roles: tuple[str, ...]
    # Default model routing (callers may override per environment).
    primary_model: str
    daily_model: str
    fast_model: str
    # Hard ceiling on prompt size in characters, used by quality tests
    # to prevent regressions toward bloat. Local enforces the strictest
    # ceiling; pro is uncapped (0 = no cap).
    max_prompt_chars: int = 0
    # Quality-floor settings — every artifact must clear these or the
    # harness re-prompts (up to ``max_repair_passes`` attempts).
    min_block_density: float = 0.6  # bart-blocks per 1000 chars (mid floor)
    max_repair_passes: int = 2
    # Atomic-mode: when True, large artifacts are decomposed into
    # block-by-block emissions (only local tier opts in by default).
    atomic_emission: bool = False
    # Whether outputs must include verbatim corpus citations on every
    # load-bearing claim. Local tier wires this through guardrails.
    require_verbatim_citations: bool = False

    def prompt_path(self, role: str) -> Path:
        """Resolve a prompt file. Falls back to pro tier if missing."""
        candidate = self.prompts_dir / f"{role}.md"
        if candidate.exists():
            return candidate
        # Fallback: the pro tier is authoritative for any role a tier
        # doesn't override. Mid + local both inherit pro's tail roles
        # this way (whimsy_indexer, problem_indexer when not fused).
        if self.name != "pro":
            pro_fallback = ARCHITECTURES_ROOT / "pro" / "prompts" / f"{role}.md"
            if pro_fallback.exists():
                return pro_fallback
        raise FileNotFoundError(
            f"Prompt '{role}' not found for tier '{self.name}' "
            f"(looked at {candidate} and pro fallback)"
        )

    def load_prompt(self, role: str) -> str:
        return self.prompt_path(role).read_text(encoding="utf-8").strip()

    def all_prompt_files(self) -> list[Path]:
        """Every .md the tier owns (excluding skeletons subdir)."""
        if not self.prompts_dir.exists():
            return []
        return sorted(p for p in self.prompts_dir.glob("*.md") if p.is_file())


_TIERS: dict[str, TierSpec] = {
    "pro": TierSpec(
        name="pro",
        description=(
            "Full-fidelity architecture for frontier API models. "
            "13 specialist agents, rich teaching contract, full block "
            "catalog. Highest quality, highest token cost."
        ),
        prompts_dir=ARCHITECTURES_ROOT / "pro" / "prompts",
        roles=(
            "author", "critic", "distiller", "exam_pattern",
            "notation_extractor", "planner", "problem_indexer",
            "researcher", "reviewer", "reviser", "solver",
            "topic_distiller", "whimsy_indexer",
        ),
        primary_model="claude-opus-4-7",
        daily_model="claude-sonnet-4-6",
        fast_model="claude-haiku-4-5-20251001",
        max_prompt_chars=0,
        min_block_density=0.8,
        max_repair_passes=2,
        atomic_emission=False,
        require_verbatim_citations=False,
    ),
    "mid": TierSpec(
        name="mid",
        description=(
            "Fused + pruned architecture for mid-tier models "
            "(Haiku, Llama-3-70B, Mixtral, Qwen-32B). Five roles instead "
            "of thirteen; prompts compressed ~60% while preserving the "
            "teaching contract and block discipline."
        ),
        prompts_dir=ARCHITECTURES_ROOT / "mid" / "prompts",
        roles=(
            "author", "planner", "corpus_indexer",
            "reviewer", "exam_kit", "researcher",
        ),
        primary_model="claude-haiku-4-5-20251001",
        daily_model="claude-haiku-4-5-20251001",
        fast_model="claude-haiku-4-5-20251001",
        max_prompt_chars=3500,
        min_block_density=0.6,
        max_repair_passes=3,
        atomic_emission=False,
        require_verbatim_citations=True,
    ),
    "local": TierSpec(
        name="local",
        description=(
            "Hallucination-proof harness for local open-weight models "
            "(Qwen3-4B/8B, Gemma3-7B). Atomic emission: one block at a "
            "time, JSON-schema constrained, every claim citation-gated. "
            "Quality harness iterates until the artifact passes."
        ),
        prompts_dir=ARCHITECTURES_ROOT / "local" / "prompts",
        roles=(
            "author_block", "planner", "corpus_indexer",
            "reviewer", "exam_kit", "researcher",
            "self_check",
        ),
        primary_model="qwen3-8b",
        daily_model="qwen3-8b",
        fast_model="qwen3-4b",
        max_prompt_chars=1500,
        min_block_density=0.5,
        max_repair_passes=4,
        atomic_emission=True,
        require_verbatim_citations=True,
    ),
}


@lru_cache(maxsize=8)
def get_tier(name: str) -> TierSpec:
    """Resolve a tier name. Raises ``KeyError`` for unknown tiers."""
    if name not in _TIERS:
        raise KeyError(
            f"Unknown tier '{name}' — pick one of {sorted(_TIERS)}"
        )
    return _TIERS[name]


def list_tiers() -> Iterable[str]:
    return tuple(_TIERS)
