"""The daily `generate` step: studywithbart.com → script → audio → Reel.

Produces one dated queue item in `draft` state. It never publishes — the
human review gate (`review.py`) sits between this and `publish.py`.
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio import build_track, probe_duration, synthesize_voice
from .config import OutreachConfig
from .paths import ItemPaths
from .render import render_reel
from .review import init_item, read_status, write_status
from .script import ReelScript, generate_script
from .source import fetch_product_facts


@dataclass
class GenerateResult:
    item: ItemPaths
    script: ReelScript
    had_music: bool
    duration_s: float


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
        api_key=cfg.resolve_anthropic_key(),
        model=cfg.anthropic_model,
        angle=angle,
    )
    item.script_path.write_text(script.model_dump_json(indent=2))
    item.caption_path.write_text(script.caption_with_tags)
    log(f"  “{script.hook}”")

    log(f"• synthesizing voiceover ({cfg.tts_provider})")
    synthesize_voice(script.voiceover, item.voice_path, cfg)

    log("• mixing audio track")
    had_music = build_track(item.voice_path, item.audio_path,
                            music_volume=cfg.music_volume)
    if not had_music:
        log("  ! no tracks in outreach/music/ — voice-only (add tracks for a bed)")
    duration = probe_duration(item.audio_path)

    log("• rendering the Reel with HyperFrames (this can take a few minutes)")
    render_reel(script, item.audio_path, duration, item.video_path)

    # Re-stamp status history so the run is traceable.
    status = read_status(item)
    status.history.append(f"generated ({duration:.1f}s, music={had_music})")
    write_status(item, status)

    log(f"✓ draft ready: {item.video_path}")
    return GenerateResult(item=item, script=script, had_music=had_music,
                          duration_s=duration)
