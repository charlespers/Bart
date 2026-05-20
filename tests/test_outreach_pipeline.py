"""Tests for the Instagram outreach pipeline — pure-logic units only.

Network calls (studywithbart.com, the Graph API) and subprocesses
(HyperFrames, ffmpeg) are deliberately not exercised here; those are
verified against real credentials/tools, never mocked into a false pass.
"""
from __future__ import annotations

import json

import pytest

from outreach.config import OutreachConfig
from outreach.review import InvalidTransition, Status
from outreach.script import ReelScript, _extract_json


# ── ReelScript validation ────────────────────────────────────────────

def _good_script_dict() -> dict:
    return {
        "title": "before and after notes",
        "hook": "Your notes are a mess. Bart isn't.",
        "scenes": [
            {"order": 1, "on_screen_text": "Drop in your lecture PDFs",
             "narration": "You start with the lecture PDFs and slides you already have.",
             "seconds": 7},
            {"order": 2, "on_screen_text": "Get a 7-day plan",
             "narration": "Bart turns them into a day-by-day study packet in about ten minutes.",
             "seconds": 7},
            {"order": 3, "on_screen_text": "Every claim cites your notes",
             "narration": "Every line points back to your own materials, so nothing is generic filler.",
             "seconds": 7},
        ],
        "caption": "Stop reorganizing notes and start studying. studywithbart.com",
        "hashtags": ["StudyTips", "#examprep", "collegelife"],
    }


def test_reelscript_parses_and_normalizes_hashtags():
    s = ReelScript(**_good_script_dict())
    assert s.hashtags == ["studytips", "examprep", "collegelife"]
    assert s.total_seconds == pytest.approx(21)
    assert s.voiceover.startswith("You start with")
    assert "#studytips" in s.caption_with_tags


def test_reelscript_rejects_wrong_scene_count():
    bad = _good_script_dict()
    bad["scenes"] = bad["scenes"][:2]
    with pytest.raises(Exception):
        ReelScript(**bad)


def test_reelscript_rejects_too_few_hashtags():
    bad = _good_script_dict()
    bad["hashtags"] = ["only", "two"]
    with pytest.raises(Exception):
        ReelScript(**bad)


def test_voiceover_orders_scenes():
    d = _good_script_dict()
    d["scenes"] = list(reversed(d["scenes"]))  # shuffle input order
    s = ReelScript(**d)
    assert s.voiceover.startswith("You start with")  # still scene order 1 first


# ── _extract_json tolerance ──────────────────────────────────────────

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_with_prose():
    assert _extract_json('Here you go:\n{"a": 1}\nHope that helps') == {"a": 1}


def test_extract_json_none_raises():
    with pytest.raises(ValueError):
        _extract_json("no json at all here")


# ── queue state machine ──────────────────────────────────────────────

def test_status_draft_to_approved_to_published():
    st = Status(date="2026-05-19")
    assert st.state == "draft"
    st.transition("approved")
    assert st.state == "approved"
    st.transition("published")
    assert st.state == "published"
    assert len(st.history) == 2


def test_status_cannot_publish_from_draft():
    st = Status(date="2026-05-19")
    with pytest.raises(InvalidTransition):
        st.transition("published")


def test_status_published_is_terminal():
    st = Status(date="2026-05-19")
    st.transition("approved")
    st.transition("published")
    with pytest.raises(InvalidTransition):
        st.transition("rejected")


def test_status_rejected_is_terminal():
    st = Status(date="2026-05-19")
    st.transition("rejected")
    with pytest.raises(InvalidTransition):
        st.transition("approved")


def test_status_unknown_state_rejected():
    st = Status(date="2026-05-19")
    with pytest.raises(InvalidTransition):
        st.transition("live")


# ── config ───────────────────────────────────────────────────────────

def test_config_rejects_bad_tts_provider():
    with pytest.raises(Exception):
        OutreachConfig(tts_provider="robovoice")


def test_config_graph_base_uses_version():
    cfg = OutreachConfig(graph_api_version="v21.0")
    assert cfg.graph_base == "https://graph.facebook.com/v21.0"


def test_config_resolve_anthropic_key_prefers_explicit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    cfg = OutreachConfig(anthropic_api_key="sk-explicit")
    assert cfg.resolve_anthropic_key() == "sk-explicit"


def test_config_resolve_anthropic_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    cfg = OutreachConfig()
    assert cfg.resolve_anthropic_key() == "sk-from-env"


def _disable_cli(monkeypatch):
    """Make `claude` CLI invisible to `shutil.which` for auth-resolution tests."""
    import outreach.config as config_mod
    monkeypatch.setattr(config_mod.shutil, "which", lambda name: None)


def test_config_resolve_anthropic_auth_prefers_oauth_env(monkeypatch):
    # Claude Code OAuth (subscription billing) wins over every api-key source.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    cfg = OutreachConfig(anthropic_api_key="sk-explicit")
    assert cfg.resolve_anthropic_auth() == ("oauth", "sk-ant-oat01-abc")


def test_config_resolve_anthropic_auth_prefers_cli_when_no_oauth(monkeypatch):
    # With no OAuth token, the `claude` CLI (subscription) wins over an API key.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    import outreach.config as config_mod
    monkeypatch.setattr(
        config_mod.shutil, "which",
        lambda name: "/usr/local/bin/claude" if name == "claude" else None,
    )
    cfg = OutreachConfig()
    assert cfg.resolve_anthropic_auth() == ("cli", "/usr/local/bin/claude")


def test_config_resolve_anthropic_auth_falls_back_to_api_key(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    _disable_cli(monkeypatch)
    cfg = OutreachConfig()
    assert cfg.resolve_anthropic_auth() == ("api_key", "sk-from-env")


def test_config_resolve_anthropic_auth_prefer_api_key_flag(monkeypatch):
    # prefer_api_key=True flips the order so the API key beats CLI/oauth.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-abc")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    cfg = OutreachConfig(prefer_api_key=True)
    assert cfg.resolve_anthropic_auth() == ("api_key", "sk-from-env")


def test_config_resolve_anthropic_auth_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _disable_cli(monkeypatch)
    cfg = OutreachConfig()
    assert cfg.resolve_anthropic_auth() == ("none", "")


def test_generate_script_raises_without_auth():
    # Validation runs before any network call, so no API key required to test.
    from outreach.script import ScriptError, generate_script
    from outreach.source import ProductFacts

    with pytest.raises(ScriptError, match="auth"):
        generate_script(
            ProductFacts(source="test"),
            auth=("none", ""),
            model="claude-sonnet-4-6",
        )


def test_config_roundtrips_through_json():
    cfg = OutreachConfig(ig_user_id="123", tts_provider="openai")
    restored = OutreachConfig(**json.loads(cfg.model_dump_json()))
    assert restored.ig_user_id == "123"
    assert restored.tts_provider == "openai"


# ── renderer plan / beat layout ──────────────────────────────────────

def test_plan_beats_covers_audio_plus_tail():
    from outreach.render import TAIL_SECONDS, _plan_beats

    script = ReelScript(**_good_script_dict())
    beats = _plan_beats(script, audio_seconds=21.0)
    # one hook + three scenes
    assert len(beats) == 4
    assert beats[0].kind == "hook"
    assert all(b.kind == "scene" for b in beats[1:])
    total = sum(b.seconds for b in beats)
    assert abs(total - (21.0 + TAIL_SECONDS)) < 1e-6


def test_plan_beats_hook_capped():
    from outreach.render import HOOK_MAX_SECONDS, _plan_beats

    script = ReelScript(**_good_script_dict())
    beats = _plan_beats(script, audio_seconds=60.0)
    # The hook never exceeds its hard cap, no matter how long the audio.
    assert beats[0].seconds <= HOOK_MAX_SECONDS + 1e-6


def test_render_beat_emits_legible_png(tmp_path):
    from outreach.render import Beat, _render_beat

    out = tmp_path / "beat.png"
    _render_beat(Beat(text="Your notes are a mess. Bart isn't.",
                      seconds=2.0, kind="hook"), out)
    assert out.exists()
    # PNG magic header — confirms Pillow actually wrote an image.
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
