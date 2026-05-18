"""Open-weight model catalog.

Each `Model` describes one (model, format, quant) combination — i.e.
"Qwen3-32B as MLX 4-bit" is a different entry than "Qwen3-32B as GGUF
Q4_K_M", because they live in different HF repos and target different
inference engines.

Two families are catalogued:

  - **Qwen3** (Apache 2.0) — the default. Native tool calling + JSON mode,
    so structured artifacts are highly reliable.
  - **Gemma 4** (Gemma Terms of Use) — Google's open-weight family. Many
    sizes (1B / 4B / 12B / 27B); `pick(..., family="gemma4")` walks them
    and auto-selects the best variant for the detected device. Gemma repos
    on Hugging Face are gated — the installer surfaces a clear message
    pointing at HUGGING_FACE_HUB_TOKEN when a download is refused.

`pick(platform, tier, family=...)` returns the best entry for the user's
hardware. The catalog is exhaustive enough that hardware detection + tier
picking always resolves to a real, downloadable artifact.

Repo IDs follow each family's established Hugging Face naming convention
(`mlx-community/<model>-4bit`, `unsloth/<model>-GGUF`). If a repo ID is
rotated upstream, the installer surfaces an actionable HF_REPO_MISSING.
"""
from __future__ import annotations

from dataclasses import dataclass

from .hardware import (
    Platform,
    TIER_HUGE,
    TIER_LARGE,
    TIER_MID,
    TIER_SMALL,
    TIER_TINY,
)


@dataclass(frozen=True)
class Model:
    key: str                # stable identifier used in config
    display_name: str       # human label
    hf_repo: str            # Hugging Face "org/repo"
    hf_filenames: tuple[str, ...]  # files to fetch (single GGUF or full MLX dir)
    format: str             # "mlx" or "gguf"
    bytes_on_disk: int      # approximate, for disk-space precheck
    min_usable_gb: float    # min Platform.usable_gb to run comfortably at 4-bit
    context_window: int     # native context (we may run smaller)
    supports_tools: bool    # native tool-use template
    supports_thinking: bool # /think /no_think toggle (Qwen3) or thought channel (Gemma 4)
    license: str            # SPDX-ish
    family: str = "qwen3"   # model family: "qwen3" | "gemma4"

    @property
    def is_mlx(self) -> bool:
        return self.format == "mlx"


# Helper: an MLX model on Hugging Face is a directory of files. We fetch the
# whole repo via snapshot_download, so hf_filenames is empty (caller treats
# empty tuple as "snapshot the whole repo").
_MLX_FULL_REPO: tuple[str, ...] = ()


# ═══════════════════════════════════════════════════════════════════════
# Qwen3 family (Apache 2.0)
# ═══════════════════════════════════════════════════════════════════════

# --- HUGE tier (≥22 GB usable, e.g. M-series Mac with 32+ GB unified) -----

QWEN3_32B_MLX = Model(
    key="qwen3-32b-mlx-4bit",
    display_name="Qwen3 32B (MLX 4-bit)",
    hf_repo="mlx-community/Qwen3-32B-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(18.5 * 1024**3),
    min_usable_gb=22.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

QWEN3_32B_GGUF = Model(
    # Unsloth's UD-Q4_K_XL is dynamic-quant: <1% loss vs Q8_0 on the LLM
    # benchmark suite at Q4 file size. See unsloth.ai/docs.
    key="qwen3-32b-gguf-q4km",
    display_name="Qwen3 32B (GGUF UD-Q4_K_XL)",
    hf_repo="unsloth/Qwen3-32B-GGUF",
    hf_filenames=("Qwen3-32B-UD-Q4_K_XL.gguf",),
    format="gguf",
    bytes_on_disk=int(19.8 * 1024**3),
    min_usable_gb=22.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

# --- LARGE tier (16-22 GB usable). MoE: 30B params on disk, 3.3B active per
#     token — very fast on Apple Silicon. ----------------------------------

QWEN3_30B_A3B_MLX = Model(
    key="qwen3-30b-a3b-mlx-4bit",
    display_name="Qwen3-Coder-30B-A3B Instruct (MLX 4-bit, MoE)",
    hf_repo="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(17.0 * 1024**3),
    min_usable_gb=16.0,
    context_window=262_144,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

QWEN3_30B_A3B_GGUF = Model(
    key="qwen3-30b-a3b-gguf-q4km",
    display_name="Qwen3-Coder-30B-A3B Instruct (GGUF Q4_K_M, MoE)",
    hf_repo="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
    hf_filenames=("Qwen3-Coder-30B-A3B-Instruct-UD-Q4_K_XL.gguf",),
    format="gguf",
    bytes_on_disk=int(18.0 * 1024**3),
    min_usable_gb=16.0,
    context_window=262_144,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

# --- MID tier (11-16 GB usable) -----------------------------------------

QWEN3_14B_MLX = Model(
    key="qwen3-14b-mlx-4bit",
    display_name="Qwen3 14B (MLX 4-bit)",
    hf_repo="mlx-community/Qwen3-14B-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(8.5 * 1024**3),
    min_usable_gb=11.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

QWEN3_14B_GGUF = Model(
    key="qwen3-14b-gguf-q4km",
    display_name="Qwen3 14B (GGUF UD-Q4_K_XL)",
    hf_repo="unsloth/Qwen3-14B-GGUF",
    hf_filenames=("Qwen3-14B-UD-Q4_K_XL.gguf",),
    format="gguf",
    bytes_on_disk=int(9.0 * 1024**3),
    min_usable_gb=11.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

# --- SMALL tier (7-11 GB usable) ----------------------------------------

QWEN3_8B_MLX = Model(
    key="qwen3-8b-mlx-4bit",
    display_name="Qwen3 8B (MLX 4-bit)",
    hf_repo="mlx-community/Qwen3-8B-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(4.6 * 1024**3),
    min_usable_gb=7.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

QWEN3_8B_GGUF = Model(
    key="qwen3-8b-gguf-q4km",
    display_name="Qwen3 8B (GGUF UD-Q4_K_XL)",
    hf_repo="unsloth/Qwen3-8B-GGUF",
    hf_filenames=("Qwen3-8B-UD-Q4_K_XL.gguf",),
    format="gguf",
    bytes_on_disk=int(5.0 * 1024**3),
    min_usable_gb=7.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

# --- TINY tier (under 7 GB usable — last resort) -----------------------

QWEN3_4B_MLX = Model(
    key="qwen3-4b-mlx-4bit",
    display_name="Qwen3 4B (MLX 4-bit)",
    hf_repo="mlx-community/Qwen3-4B-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(2.4 * 1024**3),
    min_usable_gb=4.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)

QWEN3_4B_GGUF = Model(
    key="qwen3-4b-gguf-q4km",
    display_name="Qwen3 4B (GGUF UD-Q4_K_XL)",
    hf_repo="unsloth/Qwen3-4B-GGUF",
    hf_filenames=("Qwen3-4B-UD-Q4_K_XL.gguf",),
    format="gguf",
    bytes_on_disk=int(2.7 * 1024**3),
    min_usable_gb=4.0,
    context_window=131_072,
    supports_tools=True,
    supports_thinking=True,
    license="apache-2.0",
)


# ═══════════════════════════════════════════════════════════════════════
# Gemma 4 family (Gemma Terms of Use)
#
# Google ships Gemma 4 in four instruction-tuned sizes — 1B / 4B / 12B /
# 27B. The community mirrors them as `mlx-community/gemma-4-<size>-it-4bit`
# (Apple Silicon) and `unsloth/gemma-4-<size>-it-GGUF` (llama.cpp). Gemma
# has no native tool-call template (we drive structured output via the
# GBNF grammar path instead), but Gemma 4 does emit a `<|channel>thought`
# block when its thinking mode is active — the output sanitizer strips it.
#
# Gemma repos are gated on Hugging Face: the first download asks the user
# to accept the licence / supply HUGGING_FACE_HUB_TOKEN. The installer's
# validate_repo_reachable() turns the 401/403 into an actionable message.
# ═══════════════════════════════════════════════════════════════════════

# --- HUGE / LARGE tier — Gemma 4 27B -----------------------------------

GEMMA4_27B_MLX = Model(
    key="gemma4-27b-mlx-4bit",
    display_name="Gemma 4 27B Instruct (MLX 4-bit)",
    hf_repo="mlx-community/gemma-4-27b-it-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(15.5 * 1024**3),
    min_usable_gb=20.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

GEMMA4_27B_GGUF = Model(
    key="gemma4-27b-gguf-q4km",
    display_name="Gemma 4 27B Instruct (GGUF Q4_K_M)",
    hf_repo="unsloth/gemma-4-27b-it-GGUF",
    hf_filenames=("gemma-4-27b-it-Q4_K_M.gguf",),
    format="gguf",
    bytes_on_disk=int(16.5 * 1024**3),
    min_usable_gb=20.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

# --- MID tier — Gemma 4 12B --------------------------------------------

GEMMA4_12B_MLX = Model(
    key="gemma4-12b-mlx-4bit",
    display_name="Gemma 4 12B Instruct (MLX 4-bit)",
    hf_repo="mlx-community/gemma-4-12b-it-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(7.0 * 1024**3),
    min_usable_gb=10.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

GEMMA4_12B_GGUF = Model(
    key="gemma4-12b-gguf-q4km",
    display_name="Gemma 4 12B Instruct (GGUF Q4_K_M)",
    hf_repo="unsloth/gemma-4-12b-it-GGUF",
    hf_filenames=("gemma-4-12b-it-Q4_K_M.gguf",),
    format="gguf",
    bytes_on_disk=int(7.3 * 1024**3),
    min_usable_gb=10.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

# --- SMALL tier — Gemma 4 4B -------------------------------------------

GEMMA4_4B_MLX = Model(
    key="gemma4-4b-mlx-4bit",
    display_name="Gemma 4 4B Instruct (MLX 4-bit)",
    hf_repo="mlx-community/gemma-4-4b-it-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(2.6 * 1024**3),
    min_usable_gb=5.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

GEMMA4_4B_GGUF = Model(
    key="gemma4-4b-gguf-q4km",
    display_name="Gemma 4 4B Instruct (GGUF Q4_K_M)",
    hf_repo="unsloth/gemma-4-4b-it-GGUF",
    hf_filenames=("gemma-4-4b-it-Q4_K_M.gguf",),
    format="gguf",
    bytes_on_disk=int(2.7 * 1024**3),
    min_usable_gb=5.0,
    context_window=131_072,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

# --- TINY tier — Gemma 4 1B (last resort; 32K context) -----------------

GEMMA4_1B_MLX = Model(
    key="gemma4-1b-mlx-4bit",
    display_name="Gemma 4 1B Instruct (MLX 4-bit)",
    hf_repo="mlx-community/gemma-4-1b-it-4bit",
    hf_filenames=_MLX_FULL_REPO,
    format="mlx",
    bytes_on_disk=int(0.8 * 1024**3),
    min_usable_gb=3.0,
    context_window=32_768,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)

GEMMA4_1B_GGUF = Model(
    key="gemma4-1b-gguf-q4km",
    display_name="Gemma 4 1B Instruct (GGUF Q4_K_M)",
    hf_repo="unsloth/gemma-4-1b-it-GGUF",
    hf_filenames=("gemma-4-1b-it-Q4_K_M.gguf",),
    format="gguf",
    bytes_on_disk=int(0.9 * 1024**3),
    min_usable_gb=3.0,
    context_window=32_768,
    supports_tools=False,
    supports_thinking=True,
    license="gemma",
    family="gemma4",
)


CATALOG: tuple[Model, ...] = (
    # Qwen3
    QWEN3_32B_MLX, QWEN3_32B_GGUF,
    QWEN3_30B_A3B_MLX, QWEN3_30B_A3B_GGUF,
    QWEN3_14B_MLX, QWEN3_14B_GGUF,
    QWEN3_8B_MLX, QWEN3_8B_GGUF,
    QWEN3_4B_MLX, QWEN3_4B_GGUF,
    # Gemma 4
    GEMMA4_27B_MLX, GEMMA4_27B_GGUF,
    GEMMA4_12B_MLX, GEMMA4_12B_GGUF,
    GEMMA4_4B_MLX, GEMMA4_4B_GGUF,
    GEMMA4_1B_MLX, GEMMA4_1B_GGUF,
)


# Recognised family aliases → canonical family key. "auto" means "let the
# picker use its default family" (Qwen3).
_FAMILY_ALIASES: dict[str, str] = {
    "": "auto",
    "auto": "auto",
    "default": "auto",
    "qwen": "qwen3",
    "qwen3": "qwen3",
    "gemma": "gemma4",
    "gemma4": "gemma4",
    "gemma-4": "gemma4",
    "gemma 4": "gemma4",
}

DEFAULT_FAMILY = "qwen3"


def normalize_family(family: str | None) -> str:
    """Map a user-supplied family string to a canonical key.

    Returns "qwen3" or "gemma4". Unknown values fall back to the default
    family rather than raising — a stale config should never hard-fail a run.
    """
    key = _FAMILY_ALIASES.get((family or "").strip().lower(), "auto")
    return DEFAULT_FAMILY if key == "auto" else key


def families() -> tuple[str, ...]:
    """Catalogued families, in display order."""
    return ("qwen3", "gemma4")


def get(key: str) -> Model:
    for m in CATALOG:
        if m.key == key:
            return m
    raise KeyError(f"unknown model key: {key}")


# Per-family tier → ordered list of preferred entries. First entry that fits
# the platform wins. MLX listed first (preferred on Apple Silicon); the picker
# falls back to GGUF when MLX doesn't fit or the host isn't Apple Silicon.
_QWEN3_TIER_ORDER: dict[str, tuple[Model, ...]] = {
    TIER_HUGE: (QWEN3_32B_MLX, QWEN3_30B_A3B_MLX,
                QWEN3_32B_GGUF, QWEN3_30B_A3B_GGUF),
    TIER_LARGE: (QWEN3_30B_A3B_MLX, QWEN3_30B_A3B_GGUF,
                 QWEN3_14B_MLX, QWEN3_14B_GGUF),
    TIER_MID: (QWEN3_14B_MLX, QWEN3_14B_GGUF),
    TIER_SMALL: (QWEN3_8B_MLX, QWEN3_8B_GGUF),
    TIER_TINY: (QWEN3_4B_MLX, QWEN3_4B_GGUF),
}

_GEMMA4_TIER_ORDER: dict[str, tuple[Model, ...]] = {
    TIER_HUGE: (GEMMA4_27B_MLX, GEMMA4_27B_GGUF,
                GEMMA4_12B_MLX, GEMMA4_12B_GGUF),
    TIER_LARGE: (GEMMA4_27B_MLX, GEMMA4_27B_GGUF,
                 GEMMA4_12B_MLX, GEMMA4_12B_GGUF),
    TIER_MID: (GEMMA4_12B_MLX, GEMMA4_12B_GGUF,
               GEMMA4_4B_MLX, GEMMA4_4B_GGUF),
    TIER_SMALL: (GEMMA4_4B_MLX, GEMMA4_4B_GGUF),
    TIER_TINY: (GEMMA4_1B_MLX, GEMMA4_1B_GGUF,
                GEMMA4_4B_MLX, GEMMA4_4B_GGUF),
}

_FAMILY_TIER_ORDER: dict[str, dict[str, tuple[Model, ...]]] = {
    "qwen3": _QWEN3_TIER_ORDER,
    "gemma4": _GEMMA4_TIER_ORDER,
}


def _smallest_for_family(family: str, apple_silicon: bool) -> Model:
    """Smallest catalogued entry for a family, matching the accelerator.

    Used as the last-resort return from `pick()` so a machine with very
    little memory still resolves to a real, downloadable artifact.
    """
    fmt = "mlx" if apple_silicon else "gguf"
    fam_models = [m for m in CATALOG if m.family == family and m.format == fmt]
    return min(fam_models, key=lambda m: m.bytes_on_disk)


def pick(platform: Platform, tier: str, family: str = "auto") -> Model:
    """Choose the best Model for this platform, tier, and family.

    `family`: "qwen3", "gemma4", or "auto"/"" (→ the default family, Qwen3).
    Aliases like "gemma" / "gemma-4" are accepted — see `normalize_family`.
    Gemma 4 has many sizes; this walks the family's tier order and returns
    the largest variant that fits the detected device.

    On Apple Silicon, MLX entries are preferred (native Metal, ~2× faster
    than llama.cpp Metal). Elsewhere, GGUF entries are required (MLX needs
    Apple Silicon). Tier ordering is the source of truth — we walk it and
    pick the first entry whose format matches the accelerator and whose
    min_usable_gb fits.
    """
    fam = normalize_family(family)
    tier_order = _FAMILY_TIER_ORDER[fam]
    candidates = tier_order.get(tier, tier_order[TIER_TINY])
    prefer_mlx = platform.is_apple_silicon

    # Two passes: first try the preferred format, then accept either.
    for require_mlx in (prefer_mlx, None):
        for m in candidates:
            if require_mlx is True and not m.is_mlx:
                continue
            if require_mlx is False and m.is_mlx:
                continue
            if m.is_mlx and not platform.is_apple_silicon:
                # MLX needs Apple Silicon. Skip MLX entries on other platforms.
                continue
            if m.min_usable_gb > platform.usable_gb + 0.5:
                # 0.5 GB slack so a 16 GB Mac can pick a 16 GB model.
                continue
            return m
    # Last resort — the smallest catalog entry in this family that matches
    # the accelerator. Guarantees pick() always resolves to a real artifact.
    return _smallest_for_family(fam, platform.is_apple_silicon)


def hf_resolve_url(m: Model, filename: str | None = None) -> str:
    """The huggingface.co URL where a sentinel file for this model lives.

    Used by validators (and the doctor command) to confirm the catalog
    entry resolves to a real, public file before we tell the user we're
    about to spend ~15 minutes downloading. If our catalog ever drifts
    from upstream renames, this is the canary.
    """
    sentinel = filename
    if sentinel is None:
        if m.format == "mlx" or not m.hf_filenames:
            sentinel = "config.json"
        else:
            sentinel = m.hf_filenames[0]
    return f"https://huggingface.co/{m.hf_repo}/resolve/main/{sentinel}"
