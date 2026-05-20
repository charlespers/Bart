"""Build the Reel's audio track: TTS voiceover + a royalty-free music bed.

The design rule is "never silent" — every Reel ships with spoken narration
mixed over a quiet music bed. TTS providers are pluggable:

  kokoro      local open-source neural TTS (Kokoro-82M ONNX). Free,
              offline once cached. **Default** — sounds dramatically more
              human than `say`.
  say         macOS built-in, offline, zero cost — robotic but works
              everywhere without a model download.
  elevenlabs  https://elevenlabs.io  (needs an API key)
  openai      https://platform.openai.com  (needs an API key)

Music beds are user-supplied: drop royalty-free tracks into outreach/music/.
Posts published through the Graph API cannot use Instagram's licensed audio
catalog, so the music must be license-free or original.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

from .config import OutreachConfig
from .paths import MUSIC

# Kokoro voice catalog — `af_heart` is the warmest, most human-sounding
# US-English female voice in the v1.0 release. Override via tts_voice.
_KOKORO_DEFAULT_VOICE = "af_heart"

# Model files are downloaded once and cached under XDG_CACHE_HOME (default
# ~/.cache). 350 MB total; we re-use the cache across runs and projects.
_KOKORO_CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "bart-outreach" / "kokoro"
)
_KOKORO_FILES = {
    "kokoro-v1.0.onnx":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.0/voices-v1.0.bin",
}

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

def kokoro_model_paths() -> tuple[Path, Path]:
    """Return the (model, voices) paths, downloading them if missing."""
    _KOKORO_CACHE.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, url in _KOKORO_FILES.items():
        target = _KOKORO_CACHE / name
        if not target.exists() or target.stat().st_size == 0:
            tmp = target.with_suffix(target.suffix + ".part")
            try:
                with requests.get(url, stream=True, timeout=600) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            if chunk:
                                fh.write(chunk)
                tmp.rename(target)
            except requests.RequestException as e:
                tmp.unlink(missing_ok=True)
                raise AudioError(
                    f"Kokoro model download failed ({name}): {e}. "
                    f"You can fetch it manually with `curl -L -o "
                    f"{target} {url}` and re-run."
                ) from e
        paths.append(target)
    return paths[0], paths[1]


# The Kokoro session is heavy (~350 MB ONNX). Cache one per process so
# repeated synth calls reuse it.
_KOKORO_INSTANCE = None


def _kokoro_instance():
    global _KOKORO_INSTANCE
    if _KOKORO_INSTANCE is not None:
        return _KOKORO_INSTANCE
    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        raise AudioError(
            "kokoro-onnx is not installed. Run "
            "`pip install -r outreach/requirements.txt` "
            "(or switch tts_provider to 'say')."
        ) from e
    model, voices = kokoro_model_paths()
    _KOKORO_INSTANCE = Kokoro(str(model), str(voices))
    return _KOKORO_INSTANCE


def _tts_kokoro(text: str, out: Path, voice: str, speed: float) -> None:
    """Synthesize with Kokoro-82M and encode to m4a via ffmpeg."""
    try:
        import soundfile as sf
    except ImportError as e:
        raise AudioError(
            "soundfile is not installed. Run "
            "`pip install -r outreach/requirements.txt` "
            "(or switch tts_provider to 'say')."
        ) from e
    kokoro = _kokoro_instance()
    samples, sr = kokoro.create(
        text,
        voice=voice or _KOKORO_DEFAULT_VOICE,
        speed=speed,
        lang="en-us",
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = Path(tmp.name)
    try:
        sf.write(str(wav), samples, sr)
        _to_m4a(wav, out)
    finally:
        wav.unlink(missing_ok=True)


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
    if cfg.tts_provider == "kokoro":
        _tts_kokoro(text, out, cfg.tts_voice, cfg.tts_speed)
        return
    if cfg.tts_provider == "say":
        _tts_say(text, out, cfg.tts_voice)
        return
    key = cfg.resolve_tts_key()
    if not key:
        raise AudioError(
            f"tts_provider '{cfg.tts_provider}' needs an API key. Set "
            f"tts_api_key in .outreach_config.json (or switch to 'kokoro' / 'say')."
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
