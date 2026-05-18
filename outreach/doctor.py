"""Preflight checks — `outreach doctor`.

Each command runs the relevant subset of these before doing real work, so
failures surface as one actionable line, not a stack trace deep in a
subprocess. A check is one of: OK, WARN (degraded but runnable), FAIL.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
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


def _node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().lstrip("v").split(".")[0])
    except (ValueError, subprocess.SubprocessError):
        return None


def _check_python_deps() -> Check:
    missing = [m for m in ("requests", "bs4", "anthropic", "pydantic", "rich")
               if importlib.util.find_spec(m) is None]
    if missing:
        return Check("python deps", FAIL,
                     f"missing: {', '.join(missing)} — run `pip install -r outreach/requirements.txt`")
    return Check("python deps", OK, "requests, bs4, anthropic, pydantic, rich present")


def _check_node() -> Check:
    major = _node_major()
    if major is None:
        return Check("node", FAIL, "Node.js not found — HyperFrames needs Node 22+")
    if major < 22:
        return Check("node", FAIL, f"Node {major} found — HyperFrames needs 22+")
    return Check("node", OK, f"Node {major}")


def _check_npx() -> Check:
    if not shutil.which("npx"):
        return Check("npx", FAIL, "npx not found — comes with Node.js")
    return Check("npx", OK, "present (hyperframes runs via `npx hyperframes`)")


def _check_ffmpeg() -> Check:
    have = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t)]
    if len(have) < 2:
        return Check("ffmpeg", FAIL, "ffmpeg/ffprobe missing — run `brew install ffmpeg`")
    return Check("ffmpeg", OK, "ffmpeg + ffprobe present")


def _check_music() -> Check:
    exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".aiff"}
    tracks = [p for p in MUSIC.glob("*") if p.suffix.lower() in exts] if MUSIC.exists() else []
    if not tracks:
        return Check("music bed", WARN,
                     f"no tracks in {MUSIC} — Reels will be voice-only until you add some")
    return Check("music bed", OK, f"{len(tracks)} royalty-free track(s)")


def _check_anthropic(cfg: OutreachConfig) -> Check:
    if cfg.resolve_anthropic_key():
        return Check("anthropic key", OK, "resolved (config / env / bart config)")
    return Check("anthropic key", FAIL,
                 "no key — set anthropic_api_key, $ANTHROPIC_API_KEY, or bart's config")


def _check_tts(cfg: OutreachConfig) -> Check:
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
        _check_node(),
        _check_npx(),
        _check_ffmpeg(),
        _check_music(),
        _check_anthropic(cfg),
        _check_tts(cfg),
        _check_graph_config(cfg),
    ]
    if check_token:
        checks.append(_check_token_live(cfg))
    return checks
