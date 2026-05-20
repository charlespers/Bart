"""Compose the upload-ready folder for a generated Reel.

Each `queue/<date>/` ends up containing everything a human needs to:
  - inspect the result (`reel.mp4`, `thumbnail.jpg`),
  - copy-paste the caption (`caption.txt`),
  - copy-paste the hashtags (`hashtags.txt`),
  - follow a one-page upload checklist (`upload.md`).

The Reel is still posted by the API in `publish.py` once approved — this
module just makes the manual fallback (or the review step) trivial.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .paths import ItemPaths
from .script import ReelScript


@dataclass
class MediaProbe:
    """The subset of ffprobe output we actually care about."""
    duration_s: float
    width: int
    height: int
    has_audio: bool
    audio_codec: str
    video_codec: str
    bitrate_kbps: int


def probe_media(path: Path) -> MediaProbe:
    """Inspect a rendered file with ffprobe. Used to verify outputs."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return MediaProbe(
        duration_s=float(fmt.get("duration", 0.0)),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        has_audio=bool(audio),
        audio_codec=str(audio.get("codec_name", "")),
        video_codec=str(video.get("codec_name", "")),
        bitrate_kbps=int(int(fmt.get("bit_rate", 0)) / 1000) if fmt.get("bit_rate") else 0,
    )


def write_caption_files(item: ItemPaths, script: ReelScript) -> None:
    """Write caption.txt and hashtags.txt — the two paste-ready files."""
    item.caption_path.write_text(script.caption_with_tags)
    tags = " ".join(f"#{t}" for t in script.hashtags)
    item.hashtags_path.write_text(tags + "\n")


def write_upload_checklist(
    item: ItemPaths,
    script: ReelScript,
    probe: MediaProbe,
    *,
    had_music: bool,
) -> None:
    """Drop a copy/paste cheat sheet next to the MP4."""
    on_screen_lines = "\n".join(
        f"  {s.order}. ({s.seconds:.1f}s) {s.on_screen_text}"
        for s in sorted(script.scenes, key=lambda s: s.order)
    )
    tags = " ".join(f"#{t}" for t in script.hashtags)
    caption = script.caption.strip()
    # The system prompt instructs Claude to end the caption with the URL,
    # but it sometimes drops it — backfill so the paste-ready text is
    # always brand-correct.
    if "studywithbart.com" not in caption.lower():
        caption = f"{caption} studywithbart.com"

    md = f"""# Upload — {item.date}

`reel.mp4`  ·  {probe.duration_s:.1f}s  ·  {probe.width}×{probe.height}
audio: {probe.audio_codec or 'none'}  video: {probe.video_codec}
music bed: {'yes' if had_music else 'no (voice-only)'}

---

## Caption (paste into IG)

{caption}

{tags}

---

## On-screen beats

  hook · "{script.hook}"
{on_screen_lines}

---

## Files in this folder

  reel.mp4        the post itself
  audio.m4a       voice + music bed (baked into reel.mp4)
  voice.m4a       voiceover only (for reuse / re-render)
  thumbnail.jpg   cover-image preview (first hook frame)
  caption.txt     caption + hashtags, ready to paste
  hashtags.txt    hashtags only
  script.json     the Claude-generated script (audit trail)
  status.json     review-gate state (draft → approved → published)
  upload.md       this file
  source_cache.json  studywithbart.com facts the script was grounded in

---

## Publish

  ./run-outreach approve --date {item.date}
  ./run-outreach publish --date {item.date}
"""
    item.upload_path.write_text(md)
