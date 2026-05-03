"""Checkpointing — write artifacts atomically and resume cleanly."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), suffix=".tmp")
    try:
        tmp.write(text)
        tmp.flush()
        os.fsync(tmp.fileno())
    finally:
        tmp.close()
    os.replace(tmp.name, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, default=str))


def is_complete(path: Path, min_chars: int = 200) -> bool:
    """A checkpoint is 'complete' if the artifact exists and is non-trivial."""
    return path.exists() and path.stat().st_size >= min_chars
