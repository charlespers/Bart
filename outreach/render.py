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

# The bart-loaf mascot SVG lives at <repo>/assets/bart-loaf.svg. Rasterized
# once per process, cached at module level.
_LOAF_SVG = Path(__file__).resolve().parent.parent / "assets" / "bart-loaf.svg"
_LOAF_CACHE: dict[int, Image.Image] = {}

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


def _load_loaf(target_height: int) -> Optional[Image.Image]:
    """Rasterize the bart-loaf SVG to RGBA at `target_height` (cached)."""
    if target_height in _LOAF_CACHE:
        return _LOAF_CACHE[target_height]
    if not _LOAF_SVG.exists():
        return None
    rsvg = shutil.which("rsvg-convert")
    if rsvg is None:
        # Pillow alone can't parse arbitrary SVGs — without rsvg we silently
        # render text-only (the chrome still draws the `bart.` wordmark, so
        # the brand is preserved). Doctor warns about this so the user can
        # `brew install librsvg` for the full visual.
        return None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        png = Path(tmp.name)
    try:
        subprocess.run(
            [rsvg, "-h", str(target_height), "-a", str(_LOAF_SVG),
             "-o", str(png)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        img = Image.open(png).convert("RGBA").copy()
    finally:
        png.unlink(missing_ok=True)
    _LOAF_CACHE[target_height] = img
    return img


def _paste_loaf(canvas: Image.Image, beat_index: int, kind: str) -> None:
    """Composite the bart-loaf onto `canvas` with per-beat variation.

    Position alternates left ↔ right between scenes; the hook sits dead
    centered and slightly larger. Subtle per-beat rotation gives the
    mascot a "bob" between beats — xfade interpolates the position, so
    the character appears to walk/turn between scenes.
    """
    if kind == "hook":
        height = 460
        rotate = -4
        x_anchor = "center"
    else:
        height = 360
        # Alternate sides: scene 1 → left, scene 2 → right, scene 3 → left.
        rotate = 6 if beat_index % 2 == 0 else -6
        x_anchor = "left" if beat_index % 2 == 1 else "right"
    loaf = _load_loaf(height)
    if loaf is None:
        return
    rotated = loaf.rotate(rotate, resample=Image.BICUBIC, expand=True)
    rw, rh = rotated.size
    y = 240
    if x_anchor == "center":
        x = (WIDTH - rw) // 2
    elif x_anchor == "left":
        x = 80
    else:
        x = WIDTH - 80 - rw
    canvas.alpha_composite(rotated, (x, y))


def _base_canvas() -> Image.Image:
    """Cream background with a radial terracotta wash at the top."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), CREAM + (255,))
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
    return img  # RGBA so alpha_composite from the mascot blends correctly


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


def _highlight_word(text: str, kind: str) -> tuple[str, str | None]:
    """Split `text` into (prefix, accent_tail).

    For scenes, the trailing word (with its punctuation) is colored in
    the brand accent — the renderer paints it terracotta on the line it
    ends up on. Hook stays solid INK; the whole hook is the headline.

    We deliberately use the single last word: anything longer would risk
    getting split across a wrap boundary, where the highlight would be
    silently dropped because the suffix no longer matches a single line.
    """
    if kind == "hook":
        return text, None
    stripped = text.rstrip(" .!?,;:")
    trailing_punct = text[len(stripped):]
    words = stripped.split()
    if len(words) < 3:
        return text, None
    tail = words[-1] + trailing_punct
    return " ".join(words[:-1]) + " ", tail


def _render_beat(beat: Beat, beat_index: int, out_path: Path) -> None:
    """Render a single PNG for a beat (hook or scene)."""
    img = _base_canvas()
    _draw_chrome(img)
    _paste_loaf(img, beat_index, beat.kind)
    draw = ImageDraw.Draw(img)

    # Text box sits BELOW the mascot zone (which lives between y≈240 and
    # y≈720). Footer at y≈HEIGHT-250.
    box_top, box_bottom = 820, HEIGHT - 320
    box_left, box_right = 90, WIDTH - 90
    max_width = box_right - box_left
    max_height = box_bottom - box_top

    if beat.kind == "hook":
        font, lines, line_h = _fit_text(
            draw, beat.text, _SERIF_CANDIDATES,
            max_size=120, min_size=58,
            max_width=max_width, max_height=max_height,
        )
    else:
        font, lines, line_h = _fit_text(
            draw, beat.text, _SANS_CANDIDATES,
            max_size=92, min_size=48,
            max_width=max_width, max_height=max_height,
        )

    _, accent_tail = _highlight_word(beat.text, beat.kind)

    total_h = line_h * len(lines)
    y = box_top + (max_height - total_h) // 2
    for idx, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        x = box_left + (max_width - w) // 2
        is_last_line = idx == len(lines) - 1
        if accent_tail and is_last_line and line.endswith(accent_tail.rstrip()):
            # Draw the line with the accent_tail in brand-accent color.
            head = line[: -len(accent_tail.rstrip())]
            head_w = draw.textlength(head, font=font)
            draw.text((x, y), head, font=font, fill=INK)
            draw.text((x + head_w, y), accent_tail.rstrip(),
                      font=font, fill=ACCENT)
        else:
            draw.text((x, y), line, font=font, fill=INK)
        y += line_h

    img.convert("RGB").save(out_path, "PNG")


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


# Each pair of adjacent beats crossfades over this much time — long enough
# to feel deliberate, short enough not to compete with the on-screen text.
CROSSFADE_SECONDS = 0.40


def _planned_beat_durations(beats: List[Beat]) -> List[float]:
    """Pad each beat so xfade overlaps don't shrink the total video length.

    A chain of N clips joined by N-1 crossfades of duration T loses
    (N-1)*T seconds of total playback. We add that back proportionally
    so the rendered MP4 still spans the full audio track.
    """
    if len(beats) < 2:
        return [max(b.seconds, 0.4) for b in beats]
    extra = (len(beats) - 1) * CROSSFADE_SECONDS / len(beats)
    return [max(b.seconds + extra, CROSSFADE_SECONDS + 0.2) for b in beats]


def _build_filtergraph(beats: List[Beat], durations: List[float]) -> tuple[str, str]:
    """Build the filtergraph: per-beat fps normalization + an xfade chain.

    Source PNGs are already WIDTH×HEIGHT and looped at the input level, so
    each clip just needs an `fps` filter to lock its timebase. xfade
    (below) smooths the transitions; we deliberately skip zoompan because
    its `d` knob is "output frames per input frame", which combined with a
    looped image source multiplies the duration unexpectedly.
    """
    parts: List[str] = []
    for i, _ in enumerate(durations):
        parts.append(
            f"[{i}:v]"
            f"fps={FPS},"
            f"setpts=PTS-STARTPTS,format=yuv420p"
            f"[v{i}]"
        )

    if len(beats) == 1:
        return ";".join(parts), "[v0]"

    # xfade chain: each step crossfades the running output with the next
    # clip. `offset` is when, within the running output's timeline, the
    # transition starts. The combined length of [running][vk] after the
    # xfade is `cumulative + duration[k] - CROSSFADE_SECONDS`.
    running = "[v0]"
    cumulative = durations[0]
    for k in range(1, len(beats)):
        offset = cumulative - CROSSFADE_SECONDS
        out_label = f"[xf{k}]"
        parts.append(
            f"{running}[v{k}]xfade=transition=fade:"
            f"duration={CROSSFADE_SECONDS}:offset={offset:.3f}{out_label}"
        )
        running = out_label
        cumulative += durations[k] - CROSSFADE_SECONDS

    return ";".join(parts), running


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
        beat_pngs: List[Path] = []
        for i, beat in enumerate(beats):
            png = tmp / f"beat_{i:02d}.png"
            _render_beat(beat, i, png)
            beat_pngs.append(png)

        if thumbnail_path is not None:
            # Save the hook PNG as the Reels cover image preview. IG
            # accepts JPEG/PNG covers; we re-encode with ffmpeg for size.
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-i", str(beat_pngs[0]),
                 "-q:v", "3", str(thumbnail_path)],
                check=True, capture_output=True, text=True,
            )

        durations = _planned_beat_durations(beats)
        filtergraph, vlabel = _build_filtergraph(beats, durations)

        # Each PNG becomes a looped image input timed to its (padded) beat
        # duration; the filtergraph adds ken-burns + xfade transitions, and
        # the audio is muxed on top from the prerendered track.
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for png, dur in zip(beat_pngs, durations):
            cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(png)]
        cmd += ["-i", str(audio_path)]
        cmd += [
            "-filter_complex", filtergraph,
            "-map", vlabel, "-map", f"{len(beat_pngs)}:a",
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
