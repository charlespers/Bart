"""Gemma 4 catalog + device-aware picker tests.

Covers the `family="gemma4"` path added to `bart.local_runtime.models`:
the catalog is well-formed, `pick()` auto-selects the best-fitting Gemma 4
variant for the detected device, and the legacy Qwen3 default is preserved.
"""
from __future__ import annotations

from bart.local_runtime.hardware import (
    Platform,
    TIER_HUGE,
    TIER_LARGE,
    TIER_MID,
    TIER_SMALL,
    TIER_TINY,
)
from bart.local_runtime import models as M


def _mac(ram_gb: float) -> Platform:
    """An Apple-Silicon platform with `ram_gb` unified memory."""
    return Platform(os="darwin", arch="arm64", accelerator="metal",
                     ram_gb=ram_gb, vram_gb=None)


def _cuda(vram_gb: float) -> Platform:
    return Platform(os="linux", arch="x86_64", accelerator="cuda",
                    ram_gb=64.0, vram_gb=vram_gb)


def _cpu(ram_gb: float) -> Platform:
    return Platform(os="linux", arch="x86_64", accelerator="cpu",
                    ram_gb=ram_gb, vram_gb=None)


# ── Catalog shape ──────────────────────────────────────────────────────

def test_gemma4_family_present_in_catalog():
    gemma = [m for m in M.CATALOG if m.family == "gemma4"]
    # 4 sizes × {mlx, gguf} = 8 entries.
    assert len(gemma) == 8
    keys = {m.key for m in gemma}
    for size in ("1b", "4b", "12b", "27b"):
        assert f"gemma4-{size}-mlx-4bit" in keys
        assert f"gemma4-{size}-gguf-q4km" in keys


def test_gemma4_entries_well_formed():
    for m in M.CATALOG:
        if m.family != "gemma4":
            continue
        assert m.license == "gemma"
        assert m.format in ("mlx", "gguf")
        assert m.bytes_on_disk > 0
        assert m.min_usable_gb > 0
        assert m.context_window >= 32_768
        assert "gemma-4" in m.hf_repo
        # MLX entries snapshot the whole repo; GGUF names a single file.
        if m.format == "gguf":
            assert m.hf_filenames and m.hf_filenames[0].endswith(".gguf")
        else:
            assert m.hf_filenames == ()


def test_get_resolves_gemma4_keys():
    m = M.get("gemma4-27b-mlx-4bit")
    assert m.family == "gemma4"
    assert m.is_mlx


# ── Family normalization ───────────────────────────────────────────────

def test_normalize_family_aliases():
    for alias in ("gemma", "gemma4", "gemma-4", "GEMMA 4", " Gemma4 "):
        assert M.normalize_family(alias) == "gemma4"
    for alias in ("qwen", "qwen3", "QWEN3"):
        assert M.normalize_family(alias) == "qwen3"
    # auto / empty / unknown → the default family.
    for alias in ("", "auto", "default", None, "llama-9000"):
        assert M.normalize_family(alias) == M.DEFAULT_FAMILY == "qwen3"


# ── Picker: family routing ─────────────────────────────────────────────

def test_pick_auto_keeps_qwen3_default():
    # No family arg, or "auto"/"" → unchanged legacy behavior (Qwen3).
    for fam in ("auto", "", "default"):
        m = M.pick(_mac(36.0), TIER_HUGE, family=fam)
        assert m.family == "qwen3"
    assert M.pick(_mac(36.0), TIER_HUGE).family == "qwen3"


def test_pick_gemma4_returns_gemma4():
    for tier in (TIER_HUGE, TIER_LARGE, TIER_MID, TIER_SMALL, TIER_TINY):
        m = M.pick(_mac(36.0), tier, family="gemma4")
        assert m.family == "gemma4"


# ── Picker: device-aware sizing ────────────────────────────────────────

def test_pick_gemma4_scales_with_device_memory():
    # Bigger machine → bigger Gemma 4 variant.
    big = M.pick(_mac(36.0), TIER_HUGE, family="gemma4")     # 27 GB usable
    mid = M.pick(_mac(16.0), TIER_MID, family="gemma4")      # 12 GB usable
    tiny = M.pick(_mac(6.0), TIER_TINY, family="gemma4")     # 4.5 GB usable
    assert big.bytes_on_disk > mid.bytes_on_disk > tiny.bytes_on_disk
    assert "27b" in big.key
    assert "1b" in tiny.key


def test_pick_gemma4_mlx_only_on_apple_silicon():
    # CUDA / CPU hosts must never receive an MLX entry.
    assert not M.pick(_cuda(24.0), TIER_HUGE, family="gemma4").is_mlx
    assert not M.pick(_cpu(32.0), TIER_MID, family="gemma4").is_mlx
    # Apple Silicon prefers MLX.
    assert M.pick(_mac(36.0), TIER_HUGE, family="gemma4").is_mlx


def test_pick_gemma4_fits_usable_memory():
    # The picked model's min_usable_gb must fit the platform (0.5 GB slack).
    for ram in (4.0, 8.0, 16.0, 24.0, 48.0):
        p = _mac(ram)
        from bart.local_runtime.hardware import recommended_tier
        m = M.pick(p, recommended_tier(p), family="gemma4")
        assert m.min_usable_gb <= p.usable_gb + 0.5


def test_pick_gemma4_tiny_device_still_resolves():
    # A 2 GB machine can't fit anything comfortably — picker must still
    # return a real, downloadable Gemma 4 artifact (last-resort fallback).
    m = M.pick(_cpu(2.0), TIER_TINY, family="gemma4")
    assert m.family == "gemma4"
    assert not m.is_mlx  # CPU host
    assert M.get(m.key) is m  # it's a real catalog entry


# ── hf_resolve_url ─────────────────────────────────────────────────────

def test_hf_resolve_url_for_gemma4():
    mlx = M.get("gemma4-12b-mlx-4bit")
    assert M.hf_resolve_url(mlx).endswith("/config.json")
    gguf = M.get("gemma4-12b-gguf-q4km")
    assert M.hf_resolve_url(gguf).endswith(".gguf")
