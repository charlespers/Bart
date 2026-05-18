"""Build the Reel's audio track: TTS voiceover + a royalty-free music bed.

The design rule is "never silent" — every Reel ships with spoken narration
mixed over a quiet music bed. TTS providers are pluggable:

  say         macOS built-in, offline, zero cost — the default and the
              path that lets the whole pipeline run with no paid keys.
  elevenlabs  https://elevenlabs.io  (needs an API key)
  openai      https://platform.openai.com  (needs an API key)

Music beds are user-supplied: drop royalty-free tracks into outreach/music/.
Posts published through the Graph API cannot use Instagram's licensed audio
catalog, so the music must be license-free or original.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from .config import OutreachConfig
from .paths import MUSIC

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".aiff"}


class AudioError(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise AudioError(
            f"`{tool}` is not installed. Install it with `brew install ffmpeg` "
            f"(ffmpeg ships ffprobe too)."
        )
    return path


def probe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    _require("ffprobe")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


# ── TTS providers ───────────────────────────────────────────────────

def _tts_say(text: str, out: Path, voice: str) -> None:
    if not shutil.which("say"):
        raise AudioError("`say` is macOS-only. Pick a different tts_provider.")
    _require("ffmpeg")
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
        aiff = Path(tmp.name)
    try:
        cmd = ["say", "-o", str(aiff)]
        if voice:
            cmd += ["-v", voice]
        cmd.append(text)
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        _to_m4a(aiff, out)
    finally:
        aiff.unlink(missing_ok=True)


def _tts_elevenlabs(text: str, out: Path, voice: str, key: str) -> None:
    voice_id = voice or "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel" default
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "accept": "audio/mpeg"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
        timeout=120,
    )
    if resp.status_code != 200:
        raise AudioError(f"ElevenLabs TTS failed [{resp.status_code}]: {resp.text[:200]}")
    mp3 = out.with_suffix(".tts.mp3")
    mp3.write_bytes(resp.content)
    _to_m4a(mp3, out)
    mp3.unlink(missing_ok=True)


def _tts_openai(text: str, out: Path, voice: str, key: str) -> None:
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-4o-mini-tts", "voice": voice or "alloy", "input": text},
        timeout=120,
    )
    if resp.status_code != 200:
        raise AudioError(f"OpenAI TTS failed [{resp.status_code}]: {resp.text[:200]}")
    mp3 = out.with_suffix(".tts.mp3")
    mp3.write_bytes(resp.content)
    _to_m4a(mp3, out)
    mp3.unlink(missing_ok=True)


def _to_m4a(src: Path, dst: Path) -> None:
    _require("ffmpeg")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-c:a", "aac", "-b:a", "192k", str(dst)],
        check=True, capture_output=True, text=True,
    )


def synthesize_voice(text: str, out: Path, cfg: OutreachConfig) -> None:
    """Render `text` to an m4a voiceover at `out` using the configured TTS."""
    if cfg.tts_provider == "say":
        _tts_say(text, out, cfg.tts_voice)
        return
    key = cfg.resolve_tts_key()
    if not key:
        raise AudioError(
            f"tts_provider '{cfg.tts_provider}' needs an API key. Set "
            f"tts_api_key in .outreach_config.json (or switch to 'say')."
        )
    if cfg.tts_provider == "elevenlabs":
        _tts_elevenlabs(text, out, cfg.tts_voice, key)
    elif cfg.tts_provider == "openai":
        _tts_openai(text, out, cfg.tts_voice, key)
    else:  # pragma: no cover - config validation prevents this
        raise AudioError(f"unknown tts_provider {cfg.tts_provider!r}")


# ── Music bed + mix ─────────────────────────────────────────────────

def pick_music_bed() -> Path | None:
    """Return a random royalty-free track from outreach/music/, or None."""
    if not MUSIC.exists():
        return None
    tracks = [p for p in MUSIC.iterdir() if p.suffix.lower() in _AUDIO_EXTS]
    return random.choice(tracks) if tracks else None


def build_track(voice: Path, out: Path, *, music_volume: float) -> bool:
    """Mix the voiceover with a music bed into `out`.

    Returns True if a music bed was mixed in, False if the track is
    voice-only (no music available — degraded but not fatal; the design
    says "never silent", and a voiceover satisfies that).
    """
    _require("ffmpeg")
    voice_len = probe_duration(voice)
    bed = pick_music_bed()

    if bed is None:
        shutil.copyfile(voice, out)
        return False

    # Music ducked under the voice, trimmed to the voiceover length with a
    # short fade-out so it never ends abruptly.
    fade_start = max(0.0, voice_len - 1.5)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(voice),
            "-i", str(bed),
            "-filter_complex",
            f"[1:a]volume={music_volume},afade=t=out:st={fade_start:.2f}:d=1.5[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
            str(out),
        ],
        check=True, capture_output=True, text=True,
    )
    return True
