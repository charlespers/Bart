"""Open-weight model catalog. All entries are Apache 2.0 / MIT-licensed.

Each `Model` describes one (model, format, quant) combination — i.e.
"Qwen3-32B as MLX 4-bit" is a different entry than "Qwen3-32B as GGUF
Q4_K_M", because they live in different HF repos and target different
inference engines.

`pick(platform, tier)` returns the best entry for the user's hardware.
The catalog is exhaustive enough that hardware detection + tier picking
always resolves to a real, downloadable artifact.

Repo IDs verified against Hugging Face as of 2026-04 — see commit message
for verification snapshot. If a repo ID is rotated upstream, the installer
will fall back to a sibling entry in the same tier.
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
    supports_thinking: bool # /think /no_think toggle (Qwen3)
    license: str            # SPDX-ish

    @property
    def is_mlx(self) -> bool:
        return self.format == "mlx"


# Helper: an MLX model on Hugging Face is a directory of files. We fetch the
# whole repo via snapshot_download, so hf_filenames is empty (caller treats
# empty tuple as "snapshot the whole repo").
_MLX_FULL_REPO: tuple[str, ...] = ()


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


CATALOG: tuple[Model, ...] = (
    QWEN3_32B_MLX, QWEN3_32B_GGUF,
    QWEN3_30B_A3B_MLX, QWEN3_30B_A3B_GGUF,
    QWEN3_14B_MLX, QWEN3_14B_GGUF,
    QWEN3_8B_MLX, QWEN3_8B_GGUF,
    QWEN3_4B_MLX, QWEN3_4B_GGUF,
)


def get(key: str) -> Model:
    for m in CATALOG:
        if m.key == key:
            return m
    raise KeyError(f"unknown model key: {key}")


# Tier → ordered list of preferred entries. First entry that fits the platform
# wins. We list MLX first when on Apple Silicon (faster), GGUF first elsewhere.
_TIER_ORDER: dict[str, tuple[Model, ...]] = {
    TIER_HUGE: (QWEN3_32B_MLX, QWEN3_30B_A3B_MLX,
                QWEN3_32B_GGUF, QWEN3_30B_A3B_GGUF),
    TIER_LARGE: (QWEN3_30B_A3B_MLX, QWEN3_30B_A3B_GGUF,
                 QWEN3_14B_MLX, QWEN3_14B_GGUF),
    TIER_MID: (QWEN3_14B_MLX, QWEN3_14B_GGUF),
    TIER_SMALL: (QWEN3_8B_MLX, QWEN3_8B_GGUF),
    TIER_TINY: (QWEN3_4B_MLX, QWEN3_4B_GGUF),
}


def pick(platform: Platform, tier: str) -> Model:
    """Choose the best Model for this platform and tier.

    On Apple Silicon, MLX entries are preferred (native Metal, ~2× faster than
    llama.cpp Metal). Elsewhere, GGUF entries are preferred (llama-cpp-python).
    Tier ordering is the source of truth — we walk it and pick the first entry
    whose format matches the accelerator and whose min_usable_gb fits.
    """
    candidates = _TIER_ORDER.get(tier, _TIER_ORDER[TIER_TINY])
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
    # Last resort — return the smallest catalog entry that matches accelerator.
    fallback = QWEN3_4B_MLX if platform.is_apple_silicon else QWEN3_4B_GGUF
    return fallback


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
