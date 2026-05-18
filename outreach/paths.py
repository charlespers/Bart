"""Filesystem layout for the outreach pipeline.

The package lives at `<repo>/outreach/`. Runtime data sits inside it:

    outreach/queue/<YYYY-MM-DD>/      one Reel item — state lives in status.json
    outreach/music/                   user-supplied royalty-free tracks
    outreach/templates/               HyperFrames HTML templates

Items are never moved between directories. A published Reel stays in
`queue/` with `status.json.state == "published"` — a single source of
truth that `insights.py` and `review.py` both read.

Config is a single JSON file at the repo root, mirroring bart's
`.bart_config.json` convention.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent

QUEUE = PKG_DIR / "queue"
MUSIC = PKG_DIR / "music"
TEMPLATES = PKG_DIR / "templates"

# Mirror bart's BART_CONFIG override so tests / parallel runs don't collide.
CONFIG_PATH = (
    Path(os.environ["OUTREACH_CONFIG"]).resolve()
    if os.environ.get("OUTREACH_CONFIG")
    else REPO_ROOT / ".outreach_config.json"
)


@dataclass(frozen=True)
class ItemPaths:
    """Layout of one dated queue item."""

    date: str
    root: Path

    @property
    def script_path(self) -> Path:
        return self.root / "script.json"

    @property
    def voice_path(self) -> Path:
        return self.root / "voice.m4a"

    @property
    def audio_path(self) -> Path:
        """Voice mixed with the music bed — the track baked into the Reel."""
        return self.root / "audio.m4a"

    @property
    def video_path(self) -> Path:
        return self.root / "reel.mp4"

    @property
    def caption_path(self) -> Path:
        return self.root / "caption.txt"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @classmethod
    def for_date(cls, date: str, *, base: Path | None = None) -> "ItemPaths":
        base = base or QUEUE
        return cls(date=date, root=base / date)

    def ensure(self) -> "ItemPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        return self
