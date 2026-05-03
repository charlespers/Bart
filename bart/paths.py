"""Path resolution and run-directory layout."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATERIALS = ROOT / "materials"
OUTPUT = ROOT / "output"
PROMPTS = ROOT / "prompts"
LOGS = ROOT / "logs"


@dataclass
class RunPaths:
    """Layout of a single generation run."""
    run_id: str
    root: Path

    @property
    def daily_dir(self) -> Path:
        return self.root / "daily_lessons"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def log_path(self) -> Path:
        return self.root / "run.log"

    @property
    def checkpoints_dir(self) -> Path:
        return self.root / ".checkpoints"

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache"

    @classmethod
    def create(cls, resume: str | None = None) -> "RunPaths":
        OUTPUT.mkdir(parents=True, exist_ok=True)
        if resume:
            target = OUTPUT / resume
            if not target.exists():
                raise FileNotFoundError(f"Cannot resume — run '{resume}' does not exist in output/.")
            run_id = resume
        else:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            run_id = f"run_{stamp}"
            target = OUTPUT / run_id
        target.mkdir(parents=True, exist_ok=True)
        rp = cls(run_id=run_id, root=target)
        rp.daily_dir.mkdir(exist_ok=True)
        rp.checkpoints_dir.mkdir(exist_ok=True)
        rp.cache_dir.mkdir(exist_ok=True)
        return rp
