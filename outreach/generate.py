"""The daily `generate` step: studywithbart.com → script → audio → Reel.

Produces one dated queue item in `draft` state. It never publishes — the
human review gate (`review.py`) sits between this and `publish.py`. The
output folder is upload-ready: MP4, voice + mixed audio, thumbnail, caption,
hashtags, and a one-page `upload.md` checklist.
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio import build_track, probe_duration, synthesize_voice
from .bundle import (
    MediaProbe,
    probe_media,
    write_caption_files,
    write_upload_checklist,
)
from .config import OutreachConfig
from .paths import ItemPaths
from .render import render_reel
from .review import init_item, read_status, write_status
from .script import ReelScript, generate_script
from .source import fetch_product_facts
from .strategy import update_strategy


@dataclass
class GenerateResult:
    item: ItemPaths
    script: ReelScript
    had_music: bool
    duration_s: float
    probe: MediaProbe


def generate_item(
    cfg: OutreachConfig,
    date: str,
    *,
    angle: str | None = None,
    log=print,
) -> GenerateResult:
    """Generate the full Reel for `date`. Leaves the item in `draft`."""
    item = init_item(date)

    log("• fetching product facts from studywithbart.com")
    facts = fetch_product_facts(
        cfg.studywithbart_url, cache_path=item.root / "source_cache.json"
    )
    log(f"  source: {facts.source}")

    log("• writing the script with Claude")
    script = generate_script(
        facts,
        auth=cfg.resolve_anthropic_auth(),
        model=cfg.anthropic_model,
        angle=angle,
    )
    item.script_path.write_text(script.model_dump_json(indent=2))
    write_caption_files(item, script)
    log(f"  “{script.hook}”")

    log(f"• synthesizing voiceover ({cfg.tts_provider})")
    synthesize_voice(script.voiceover, item.voice_path, cfg)

    log("• mixing audio track")
    had_music = build_track(item.voice_path, item.audio_path,
                            music_volume=cfg.music_volume)
    if not had_music:
        log("  ! no tracks in outreach/music/ — voice-only (add tracks for a bed)")
    duration = probe_duration(item.audio_path)

    log("• rendering the Reel (Pillow + ffmpeg)")
    render_reel(
        script, item.audio_path, duration, item.video_path,
        thumbnail_path=item.thumbnail_path,
    )

    log("• verifying the rendered MP4")
    probe = probe_media(item.video_path)
    if probe.width != 1080 or probe.height != 1920:
        raise RuntimeError(
            f"rendered MP4 is {probe.width}x{probe.height}, expected 1080x1920"
        )
    if not probe.has_audio:
        raise RuntimeError("rendered MP4 has no audio stream")
    if probe.duration_s < duration - 1.0:
        raise RuntimeError(
            f"rendered MP4 is {probe.duration_s:.1f}s, audio was {duration:.1f}s"
        )

    log("• writing upload checklist")
    write_upload_checklist(item, script, probe, had_music=had_music)

    # Refresh the strategy file so it reflects the just-added draft.
    try:
        update_strategy(cfg, log=lambda m: None)
    except Exception as e:  # noqa: BLE001 - strategy is best-effort
        log(f"  ! strategy update skipped: {e}")

    status = read_status(item)
    status.history.append(
        f"generated ({duration:.1f}s, music={had_music}, "
        f"{probe.width}x{probe.height} {probe.video_codec}/{probe.audio_codec})"
    )
    write_status(item, status)

    log(f"✓ draft ready: {item.video_path}")
    log(f"  upload checklist: {item.upload_path}")
    return GenerateResult(item=item, script=script, had_music=had_music,
                          duration_s=duration, probe=probe)
