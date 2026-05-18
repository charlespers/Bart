"""Render a ReelScript into an MP4 with HyperFrames.

HyperFrames (https://github.com/heygen-com/hyperframes) turns an animated
HTML composition into a deterministic MP4. The flow:

  1. `npx hyperframes init <proj> --non-interactive --example blank`
  2. drop our filled composition (templates/reel.html) into the project
  3. `npx hyperframes render --output reel.mp4`  (run inside <proj>)

Node 22+ and ffmpeg are required — `outreach doctor` checks both.
"""
from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from .paths import TEMPLATES
from .script import ReelScript

WIDTH, HEIGHT = 1080, 1920          # 9:16 vertical
HOOK_MAX_SECONDS = 2.4
TAIL_SECONDS = 0.5                  # breathing room after the voiceover ends


class RenderError(RuntimeError):
    pass


def _scene_div(cls: str, body_html: str, start_s: float, dur_s: float) -> str:
    return (
        f'<div class="{cls}" style="--start: {start_s:.2f}s; --dur: {dur_s:.2f}s;">'
        f"{body_html}</div>"
    )


def fill_template(script: ReelScript, audio_src: str, audio_seconds: float) -> str:
    """Return the composition HTML for `script`, timed to the audio length."""
    template = (TEMPLATES / "reel.html").read_text()

    total = audio_seconds + TAIL_SECONDS
    hook_dur = min(HOOK_MAX_SECONDS, total * 0.16)
    body_total = total - hook_dur

    weights = [max(s.seconds, 0.1) for s in sorted(script.scenes, key=lambda s: s.order)]
    weight_sum = sum(weights)

    blocks = [_scene_div("hook", html.escape(script.hook), 0.0, hook_dur)]
    cursor = hook_dur
    for scene, w in zip(sorted(script.scenes, key=lambda s: s.order), weights):
        dur = body_total * (w / weight_sum)
        blocks.append(_scene_div("scene", html.escape(scene.on_screen_text), cursor, dur))
        cursor += dur

    return (
        template
        .replace("{{WIDTH}}", str(WIDTH))
        .replace("{{HEIGHT}}", str(HEIGHT))
        .replace("{{TOTAL_MS}}", str(int(total * 1000)))
        .replace("{{AUDIO_SRC}}", html.escape(audio_src, quote=True))
        .replace("{{SCENES}}", "\n      ".join(blocks))
    )


def _find_composition_html(project: Path) -> Path:
    """Locate the HTML file a HyperFrames project renders.

    Different HyperFrames versions name this differently, so match on
    content (the composition root) rather than a fixed filename.
    """
    candidates = sorted(project.rglob("*.html"))
    for name in ("composition.html", "index.html", "main.html"):
        for c in candidates:
            if c.name == name:
                return c
    for c in candidates:
        if "data-composition-id" in c.read_text(errors="ignore"):
            return c
    if candidates:
        return candidates[0]
    raise RenderError(
        f"Could not find a composition HTML in the HyperFrames project at "
        f"{project}. Run `npx hyperframes preview` once to inspect the "
        f"layout, then adjust outreach/render.py:_find_composition_html."
    )


def render_reel(
    script: ReelScript,
    audio_path: Path,
    audio_seconds: float,
    out_path: Path,
) -> Path:
    """Render `script` + `audio_path` into an MP4 at `out_path`."""
    if not shutil.which("npx"):
        raise RenderError("`npx` not found — install Node.js 22+.")
    if not shutil.which("ffmpeg"):
        raise RenderError("`ffmpeg` not found — run `brew install ffmpeg`.")

    with tempfile.TemporaryDirectory(prefix="hyperframes-") as tmp:
        project = Path(tmp) / "reel"
        try:
            subprocess.run(
                ["npx", "--yes", "hyperframes", "init", str(project),
                 "--non-interactive", "--example", "blank"],
                check=True, capture_output=True, text=True, timeout=300,
            )
        except subprocess.CalledProcessError as e:
            raise RenderError(f"`hyperframes init` failed:\n{e.stderr or e.stdout}") from e
        except subprocess.TimeoutExpired as e:
            raise RenderError("`hyperframes init` timed out (npm install slow?).") from e

        # Audio referenced by a relative name the renderer can resolve.
        audio_in_proj = project / "audio.m4a"
        shutil.copyfile(audio_path, audio_in_proj)

        composition = _find_composition_html(project)
        composition.write_text(fill_template(script, "audio.m4a", audio_seconds))

        render_out = project / "reel.mp4"
        try:
            subprocess.run(
                ["npx", "--yes", "hyperframes", "render", "--output", str(render_out)],
                cwd=project, check=True, capture_output=True, text=True, timeout=900,
            )
        except subprocess.CalledProcessError as e:
            raise RenderError(f"`hyperframes render` failed:\n{e.stderr or e.stdout}") from e
        except subprocess.TimeoutExpired as e:
            raise RenderError("`hyperframes render` timed out after 15 minutes.") from e

        if not render_out.exists():
            raise RenderError(
                "HyperFrames reported success but produced no MP4. Check the "
                "render output and the --output flag for your HyperFrames version."
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(render_out, out_path)

    return out_path
