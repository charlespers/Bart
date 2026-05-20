"""Render a ReelScript into a 9:16 MP4 with ffmpeg + Pillow.

The renderer composes one PNG per beat (hook + scenes) at 1080x1920 using
Pillow, then feeds them through ffmpeg as a concat list timed to the audio.
This avoids a headless-browser dependency, has no network requirements, and
produces deterministic output.

The frame layout: cream background with a soft terracotta wash at the top,
the `bart.` wordmark just below the wash, the studywithbart.com footer at
the bottom, and the beat text centered in the stage between them.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .script import ReelScript

WIDTH, HEIGHT = 1080, 1920          # 9:16 vertical
FPS = 30
HOOK_MAX_SECONDS = 2.4
TAIL_SECONDS = 0.5                  # breathing room after the voiceover ends

# Brand palette — mirrors bart/branding.py
CREAM = (244, 241, 234)
INK = (34, 31, 27)
ACCENT = (201, 100, 66)
ACCENT_SOFT = (232, 138, 106)

# macOS system font candidates — first that exists wins.
_SERIF_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Baskerville.ttc",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/System/Library/Fonts/NewYork.ttf",
    "/System/Library/Fonts/Times.ttc",
)
_SANS_CANDIDATES = (
    "/System/Library/Fonts/Avenir Next.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


class RenderError(RuntimeError):
    pass


@dataclass
class Beat:
    """One on-screen slide: text + duration in seconds."""
    text: str
    seconds: float
    kind: str           # "hook" | "scene"


# ── Font loading ─────────────────────────────────────────────────────

def _load_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _serif(size: int) -> ImageFont.FreeTypeFont:
    return _load_font(_SERIF_CANDIDATES, size)


def _sans(size: int) -> ImageFont.FreeTypeFont:
    return _load_font(_SANS_CANDIDATES, size)


# ── Layout helpers ───────────────────────────────────────────────────

def _wrap_to_fit(
    text: str,
    font: ImageFont.FreeTypeFont,
    draw: ImageDraw.ImageDraw,
    max_width: int,
) -> List[str]:
    """Word-wrap `text` so each line fits within `max_width` pixels."""
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        width = draw.textlength(trial, font=font)
        if width <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    candidates: tuple[str, ...],
    *,
    max_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    line_spacing: float = 1.15,
) -> tuple[ImageFont.FreeTypeFont, List[str], int]:
    """Pick the largest font size that wraps `text` within the box.

    Returns (font, wrapped lines, line height in px).
    """
    size = max_size
    while size >= min_size:
        font = _load_font(candidates, size)
        lines = _wrap_to_fit(text, font, draw, max_width)
        # Measure height using the font ascent/descent so multi-line text
        # spacing is consistent across glyph runs.
        bbox = font.getbbox("Ag")
        line_h = int((bbox[3] - bbox[1]) * line_spacing)
        total_h = line_h * len(lines)
        if total_h <= max_height:
            return font, lines, line_h
        size -= 8
    font = _load_font(candidates, min_size)
    lines = _wrap_to_fit(text, font, draw, max_width)
    bbox = font.getbbox("Ag")
    line_h = int((bbox[3] - bbox[1]) * line_spacing)
    return font, lines, line_h


def _base_canvas() -> Image.Image:
    """Cream background with a radial terracotta wash at the top."""
    img = Image.new("RGB", (WIDTH, HEIGHT), CREAM)
    # Soft accent wash from the top — use a separate layer with a big blur.
    wash = Image.new("RGB", (WIDTH, HEIGHT), CREAM)
    wd = ImageDraw.Draw(wash)
    # A bright blob centered at the top, blurred heavily to feel like a wash.
    cx, cy = WIDTH // 2, -200
    for r, alpha in ((900, 28), (700, 36), (500, 44), (300, 52)):
        # Approximate radial blend by drawing concentric circles in a tinted
        # color whose distance from CREAM is `alpha`.
        tint = tuple(
            int(c + (a - c) * (alpha / 255.0))
            for c, a in zip(CREAM, ACCENT_SOFT)
        )
        wd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tint)
    wash = wash.filter(ImageFilter.GaussianBlur(80))
    img.paste(wash, (0, 0))
    return img


def _draw_chrome(img: Image.Image) -> None:
    """Draw the persistent wordmark + footer on the canvas."""
    draw = ImageDraw.Draw(img)
    # Wordmark
    wm_font = _serif(82)
    wm = "bart"
    wm_w = draw.textlength(wm, font=wm_font)
    dot_w = draw.textlength(".", font=wm_font)
    total = wm_w + dot_w
    x = (WIDTH - total) // 2
    y = 110
    draw.text((x, y), wm, font=wm_font, fill=INK)
    draw.text((x + wm_w, y), ".", font=wm_font, fill=ACCENT)

    # Footer
    foot_font = _sans(44)
    foot = "studywithbart.com"
    fw = draw.textlength(foot, font=foot_font)
    draw.text(((WIDTH - fw) // 2, HEIGHT - 170), foot, font=foot_font,
              fill=ACCENT)


def _render_beat(beat: Beat, out_path: Path) -> None:
    """Render a single PNG for a beat (hook or scene)."""
    img = _base_canvas()
    _draw_chrome(img)
    draw = ImageDraw.Draw(img)

    # Stage box: between chrome top (~y=280) and chrome bottom (~y=HEIGHT-250).
    box_top, box_bottom = 380, HEIGHT - 320
    box_left, box_right = 90, WIDTH - 90
    max_width = box_right - box_left
    max_height = box_bottom - box_top

    if beat.kind == "hook":
        font, lines, line_h = _fit_text(
            draw, beat.text, _SERIF_CANDIDATES,
            max_size=130, min_size=64,
            max_width=max_width, max_height=max_height,
        )
    else:
        font, lines, line_h = _fit_text(
            draw, beat.text, _SANS_CANDIDATES,
            max_size=98, min_size=54,
            max_width=max_width, max_height=max_height,
        )

    total_h = line_h * len(lines)
    y = box_top + (max_height - total_h) // 2
    for line in lines:
        w = draw.textlength(line, font=font)
        x = box_left + (max_width - w) // 2
        draw.text((x, y), line, font=font, fill=INK)
        y += line_h

    img.save(out_path, "PNG")


# ── Scene → beat planning ────────────────────────────────────────────

def _plan_beats(script: ReelScript, audio_seconds: float) -> List[Beat]:
    """Time the hook + each scene to the actual audio length."""
    total = audio_seconds + TAIL_SECONDS
    hook_dur = min(HOOK_MAX_SECONDS, total * 0.16)
    body_total = max(total - hook_dur, 0.1)

    ordered = sorted(script.scenes, key=lambda s: s.order)
    weights = [max(s.seconds, 0.1) for s in ordered]
    weight_sum = sum(weights)

    beats = [Beat(text=script.hook, seconds=hook_dur, kind="hook")]
    for s, w in zip(ordered, weights):
        beats.append(Beat(text=s.on_screen_text, seconds=body_total * (w / weight_sum),
                          kind="scene"))
    return beats


# ── ffmpeg orchestration ─────────────────────────────────────────────

def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise RenderError(
            f"`{tool}` is not installed. Install it with `brew install ffmpeg`."
        )
    return path


def _build_concat_list(frames: List[tuple[Path, float]], out: Path) -> None:
    """ffmpeg concat demuxer file — one PNG per beat with explicit duration."""
    lines: List[str] = []
    for path, dur in frames:
        lines.append(f"file '{path.as_posix()}'")
        lines.append(f"duration {dur:.3f}")
    # The concat demuxer ignores the final `duration` so the last frame must
    # be repeated for it to be honored.
    lines.append(f"file '{frames[-1][0].as_posix()}'")
    out.write_text("\n".join(lines) + "\n")


def render_reel(
    script: ReelScript,
    audio_path: Path,
    audio_seconds: float,
    out_path: Path,
    *,
    thumbnail_path: Optional[Path] = None,
) -> Path:
    """Render `script` + `audio_path` into a 9:16 MP4 at `out_path`.

    If `thumbnail_path` is given, also writes a JPG snapshot of the hook
    frame there — used by `upload.md` as the Reels cover image preview.
    """
    _require("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    beats = _plan_beats(script, audio_seconds)

    with tempfile.TemporaryDirectory(prefix="reel-frames-") as tmpdir:
        tmp = Path(tmpdir)
        frames: List[tuple[Path, float]] = []
        for i, beat in enumerate(beats):
            png = tmp / f"beat_{i:02d}.png"
            _render_beat(beat, png)
            frames.append((png, beat.seconds))

        if thumbnail_path is not None:
            # Save the hook PNG (with a `.jpg` extension as requested) — IG
            # accepts JPEG/PNG covers; we'll re-encode with ffmpeg for size.
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-i", str(frames[0][0]),
                 "-q:v", "3", str(thumbnail_path)],
                check=True, capture_output=True, text=True,
            )

        concat_list = tmp / "concat.txt"
        _build_concat_list(frames, concat_list)

        # Compose: concat PNGs at FPS (with explicit per-frame duration via
        # the concat demuxer), overlay the audio track, re-encode to H.264 +
        # AAC at Instagram-friendly settings.
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-i", str(audio_path),
            "-vf", f"fps={FPS},format=yuv420p,scale={WIDTH}:{HEIGHT}:flags=lanczos",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True,
                           timeout=600)
        except subprocess.CalledProcessError as e:
            raise RenderError(f"ffmpeg render failed:\n{e.stderr or e.stdout}") from e
        except subprocess.TimeoutExpired as e:
            raise RenderError("ffmpeg render timed out after 10 minutes.") from e

    return out_path


# Kept for back-compat with anything that imported the old name.
def fill_template(*_args, **_kwargs) -> str:  # pragma: no cover
    raise RenderError(
        "fill_template() is no longer part of the renderer — render_reel() "
        "now produces frames directly with Pillow."
    )
