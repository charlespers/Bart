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
            run_id = _resolve_resume_id(resume)
            target = OUTPUT / run_id
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


def _resolve_resume_id(token: str) -> str:
    """Resolve a `--resume` token to an existing run directory name.

    Accepts the full `run_YYYY-MM-DD_HHMMSS` form, but also any unique
    substring of it — typing just the HHMMSS time component is the
    common shorthand. Raises FileNotFoundError if zero or multiple runs
    match. The error message lists the available runs so the user can
    disambiguate.
    """
    target = OUTPUT / token
    if target.exists():
        return token
    candidates = sorted(
        d.name for d in OUTPUT.iterdir()
        if d.is_dir() and d.name.startswith("run_") and token in d.name
    )
    if len(candidates) == 1:
        return candidates[0]
    available = sorted(
        d.name for d in OUTPUT.iterdir()
        if d.is_dir() and d.name.startswith("run_")
    )
    if not candidates:
        msg = f"Cannot resume — run '{token}' does not exist in output/."
    else:
        msg = (
            f"Cannot resume — '{token}' is ambiguous, matches: "
            f"{', '.join(candidates)}."
        )
    if available:
        msg += f" Available runs: {', '.join(available)}."
    raise FileNotFoundError(msg)
