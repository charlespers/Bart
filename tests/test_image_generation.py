"""Custom image-generation tests — bart/imagegen.py + the bart-figure block.

Network is never touched: image generation is disabled by default, so these
tests exercise the disabled/placeholder paths, backend selection, and the
block renderer without hitting pollinations.ai or a local SD server.
"""
from __future__ import annotations

import os

import pytest

from bart import imagegen
from bart.render.blocks.figure import figure
from bart.render.blocks.expand import expand_blocks
from bart.render.blocks._registry import _BLOCK_REGISTRY
from bart.render.block_schemas import BLOCK_SCHEMAS


@pytest.fixture(autouse=True)
def _no_image_env(monkeypatch):
    """Every test starts with image generation OFF and no SD server."""
    monkeypatch.delenv("BART_IMAGE_GEN", raising=False)
    monkeypatch.delenv("BART_IMAGE_SD_URL", raising=False)


# ── Enable flag ────────────────────────────────────────────────────────

def test_disabled_by_default():
    assert imagegen.image_generation_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), ("nope", False),
])
def test_enable_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("BART_IMAGE_GEN", val)
    assert imagegen.image_generation_enabled() is expected


def test_generate_returns_none_when_disabled():
    # Disabled → no network call, no image.
    assert imagegen.generate_image("a neuron diagram") is None
    assert imagegen.figure_data_uri("a neuron diagram") is None


def test_empty_prompt_never_generates(monkeypatch):
    monkeypatch.setenv("BART_IMAGE_GEN", "1")
    assert imagegen.generate_image("   ") is None


# ── Backend selection ──────────────────────────────────────────────────

def test_default_backend_is_pollinations():
    b = imagegen.get_backend()
    assert b.name == "pollinations"
    assert b.available() is True


def test_local_sd_backend_selected_when_configured(monkeypatch):
    monkeypatch.setenv("BART_IMAGE_SD_URL", "http://127.0.0.1:7860")
    b = imagegen.get_backend()
    assert b.name == "local-sd"
    assert b.base_url == "http://127.0.0.1:7860"


# ── Helpers ────────────────────────────────────────────────────────────

def test_mime_detection():
    assert imagegen._detect_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert imagegen._detect_mime(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert imagegen._detect_mime(b"RIFF????WEBPmore") == "image/webp"


def test_cache_key_is_deterministic_and_dimension_sensitive():
    a = imagegen._cache_key("pollinations", "a cat", 768, 432)
    b = imagegen._cache_key("pollinations", "a cat", 768, 432)
    c = imagegen._cache_key("pollinations", "a cat", 640, 640)
    assert a == b
    assert a != c


def test_seed_is_stable_per_prompt():
    assert imagegen._seed_for("photosynthesis") == imagegen._seed_for("photosynthesis")
    assert imagegen._seed_for("a") != imagegen._seed_for("b")


# ── bart-figure block renderer ─────────────────────────────────────────

def test_figure_registered_and_schemad():
    assert "figure" in _BLOCK_REGISTRY
    assert "figure" in BLOCK_SCHEMAS
    assert "prompt" in BLOCK_SCHEMAS["figure"].required


def test_figure_renders_placeholder_when_disabled():
    html = figure(prompt="a labeled diagram of a plant cell", caption="Fig. 1")
    assert "b-figure-placeholder" in html
    assert "a labeled diagram of a plant cell" in html
    assert "Fig. 1" in html
    # No real image embedded when generation is off.
    assert "data:image" not in html


def test_figure_handles_missing_prompt():
    html = figure(prompt="")
    assert "b-figure-placeholder" in html
    assert "no prompt given" in html


def test_figure_aspect_ratios():
    for aspect in ("16:9", "4:3", "1:1", "3:2"):
        html = figure(prompt="x", aspect=aspect)
        assert "--fig-aspect" in html
    # Unknown aspect falls back, never crashes.
    assert "b-figure" in figure(prompt="x", aspect="bogus")


def test_figure_expands_through_block_pipeline():
    md = (
        "intro\n\n"
        "```bart-figure\n"
        '{"prompt": "the water cycle", "caption": "how water moves"}\n'
        "```\n\n"
        "outro\n"
    )
    res = expand_blocks(md)
    assert res.counts.get("figure") == 1
    assert not res.warnings
    assert "the water cycle" in res.text


def test_figure_catalog_gated_on_image_generation(monkeypatch):
    from bart.render.blocks.catalog import catalog_for
    monkeypatch.delenv("BART_IMAGE_GEN", raising=False)
    assert "bart-figure" not in catalog_for("daily_lesson", "Biology")
    monkeypatch.setenv("BART_IMAGE_GEN", "1")
    assert "bart-figure" in catalog_for("daily_lesson", "Biology")
