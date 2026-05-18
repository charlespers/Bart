"""Outreach pipeline configuration.

Stored as `.outreach_config.json` at the repo root, chmod 600 (it holds an
Instagram Graph API token and, optionally, a TTS key). No account password
is ever stored or used — the Graph API authenticates with OAuth access
tokens only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from .paths import CONFIG_PATH, REPO_ROOT

# TTS providers the pipeline knows how to drive. `say` is macOS's built-in
# offline voice — zero cost, no key — so the whole pipeline runs end to end
# before the user signs up for anything.
TTS_PROVIDERS = ("say", "elevenlabs", "openai")


class OutreachConfig(BaseModel):
    # ─── Instagram Graph API (publishing) ───
    # A long-lived access token and the Instagram *user* id of the
    # Professional account. Created by the user in the Meta Developer
    # console — the pipeline cannot mint these.
    meta_access_token: str = ""
    ig_user_id: str = ""
    graph_api_version: str = "v21.0"

    # Rendered MP4s must be reachable at a public HTTPS URL for the Graph
    # API to ingest them. The publish step expects the file for <date> at
    # `{public_video_base_url}/{date}.mp4` unless --video-url overrides it.
    public_video_base_url: str = ""

    # ─── Content generation ───
    studywithbart_url: str = "https://studywithbart.com"
    anthropic_api_key: str = ""  # falls back to env / .bart_config.json
    anthropic_model: str = "claude-sonnet-4-6"

    # ─── Audio ───
    tts_provider: str = "say"
    tts_api_key: str = ""           # required for elevenlabs / openai
    tts_voice: str = ""             # provider-specific; "" = provider default
    music_volume: float = Field(default=0.14, ge=0.0, le=1.0)

    @field_validator("tts_provider")
    @classmethod
    def _valid_provider(cls, v: str) -> str:
        if v not in TTS_PROVIDERS:
            raise ValueError(f"tts_provider must be one of {TTS_PROVIDERS}")
        return v

    # ─── Derived helpers ───
    @property
    def graph_base(self) -> str:
        return f"https://graph.facebook.com/{self.graph_api_version}"

    def resolve_anthropic_key(self) -> str:
        """Find an Anthropic key: explicit config → env → bart's config."""
        if self.anthropic_api_key:
            return self.anthropic_api_key
        env = os.environ.get("ANTHROPIC_API_KEY", "")
        if env:
            return env
        bart_cfg = REPO_ROOT / ".bart_config.json"
        if bart_cfg.exists():
            try:
                data = json.loads(bart_cfg.read_text())
                if data.get("auth_mode") == "api" and data.get("api_key"):
                    return str(data["api_key"])
            except (json.JSONDecodeError, OSError):
                pass
        return ""

    def resolve_tts_key(self) -> str:
        if self.tts_api_key:
            return self.tts_api_key
        env_var = {
            "elevenlabs": "ELEVENLABS_API_KEY",
            "openai": "OPENAI_API_KEY",
        }.get(self.tts_provider, "")
        return os.environ.get(env_var, "") if env_var else ""


def load_config() -> Optional[OutreachConfig]:
    if not CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return OutreachConfig(**data)
    except (json.JSONDecodeError, ValidationError, OSError):
        return None


def save_config(cfg: OutreachConfig) -> None:
    CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def load_or_init() -> OutreachConfig:
    """Return the saved config, or write a fresh template and return it.

    On first run this drops an `.outreach_config.json` skeleton next to the
    bart config so the user has a documented file to fill in. `doctor`
    reports exactly which fields still need values.
    """
    cfg = load_config()
    if cfg is not None:
        return cfg
    cfg = OutreachConfig()
    save_config(cfg)
    return cfg
