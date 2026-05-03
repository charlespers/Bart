"""Plain-text extraction (.txt, .md)."""
from __future__ import annotations

from pathlib import Path


def extract_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()
