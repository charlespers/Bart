"""Preflight checks — `outreach doctor`.

Each command runs the relevant subset of these before doing real work, so
failures surface as one actionable line, not a stack trace deep in a
subprocess. A check is one of: OK, WARN (degraded but runnable), FAIL.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import List

from .config import OutreachConfig
from .paths import MUSIC

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _check_python_deps() -> Check:
    required = ("requests", "bs4", "anthropic", "pydantic", "rich", "PIL")
    missing = [m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        return Check("python deps", FAIL,
                     f"missing: {', '.join(missing)} — run `pip install -r outreach/requirements.txt`")
    return Check("python deps", OK,
                 "requests, bs4, anthropic, pydantic, rich, Pillow present")


def _check_ffmpeg() -> Check:
    have = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t)]
    if len(have) < 2:
        return Check("ffmpeg", FAIL, "ffmpeg/ffprobe missing — run `brew install ffmpeg`")
    return Check("ffmpeg", OK, "ffmpeg + ffprobe present")


def _check_mascot() -> Check:
    if not shutil.which("rsvg-convert"):
        return Check("mascot", WARN,
                     "rsvg-convert not found — Reels render text-only "
                     "(no bart-loaf). Install with `brew install librsvg`.")
    return Check("mascot", OK, "rsvg-convert present (bart-loaf renders)")


def _check_music() -> Check:
    exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".aiff"}
    tracks = [p for p in MUSIC.glob("*") if p.suffix.lower() in exts] if MUSIC.exists() else []
    if not tracks:
        return Check("music bed", WARN,
                     f"no tracks in {MUSIC} — Reels will be voice-only until you add some")
    return Check("music bed", OK, f"{len(tracks)} royalty-free track(s)")


def _check_anthropic(cfg: OutreachConfig) -> Check:
    kind, _ = cfg.resolve_anthropic_auth()
    if kind == "oauth":
        return Check("anthropic auth", OK,
                     "Claude Code OAuth token (subscription billing)")
    if kind == "cli":
        return Check("anthropic auth", OK,
                     "`claude` CLI on PATH (subscription billing)")
    if kind == "api_key":
        return Check("anthropic auth", OK, "api key (config / env / bart config)")
    return Check("anthropic auth", FAIL,
                 "no auth — install the `claude` CLI, or set "
                 "CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY / anthropic_api_key")


def _check_tts(cfg: OutreachConfig) -> Check:
    if cfg.tts_provider == "kokoro":
        if importlib.util.find_spec("kokoro_onnx") is None:
            return Check("tts", FAIL,
                         "provider=kokoro but kokoro_onnx not installed — "
                         "run `pip install -r outreach/requirements.txt`")
        from .audio import _KOKORO_CACHE, _KOKORO_FILES
        missing = [n for n in _KOKORO_FILES if not (_KOKORO_CACHE / n).exists()]
        if missing:
            return Check("tts", WARN,
                         f"provider=kokoro — model files will download on "
                         f"first `generate` (~350 MB): {', '.join(missing)}")
        return Check("tts", OK,
                     f"provider=kokoro (local neural, cached in {_KOKORO_CACHE})")
    if cfg.tts_provider == "say":
        if shutil.which("say"):
            return Check("tts", OK, "provider=say (macOS built-in, no key needed)")
        return Check("tts", FAIL, "provider=say but `say` not found (macOS only)")
    if cfg.resolve_tts_key():
        return Check("tts", OK, f"provider={cfg.tts_provider}, key resolved")
    return Check("tts", FAIL, f"provider={cfg.tts_provider} needs tts_api_key")


def _check_graph_config(cfg: OutreachConfig) -> Check:
    missing = [f for f in ("meta_access_token", "ig_user_id", "public_video_base_url")
               if not getattr(cfg, f)]
    if missing:
        return Check("graph api config", WARN,
                     f"not set: {', '.join(missing)} — `generate` works, `publish` needs these")
    return Check("graph api config", OK, "token, ig_user_id, public_video_base_url set")


def _check_token_live(cfg: OutreachConfig) -> Check:
    if not (cfg.meta_access_token and cfg.ig_user_id):
        return Check("graph api token", WARN, "skipped — config incomplete")
    try:
        from .publish import verify_token
        username = verify_token(cfg)
        return Check("graph api token", OK, f"valid — account @{username}")
    except Exception as e:  # noqa: BLE001 - report any failure as a check line
        return Check("graph api token", FAIL, str(e))


def run_doctor(cfg: OutreachConfig, *, check_token: bool = False) -> List[Check]:
    checks = [
        _check_python_deps(),
        _check_ffmpeg(),
        _check_mascot(),
        _check_music(),
        _check_anthropic(cfg),
        _check_tts(cfg),
        _check_graph_config(cfg),
    ]
    if check_token:
        checks.append(_check_token_live(cfg))
    return checks
