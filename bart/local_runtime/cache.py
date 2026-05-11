"""On-disk cache for downloaded model weights.

Layout:

  ~/.cache/bart/models/
    qwen3-32b-mlx-4bit/         <- MLX models: full snapshot dir
      config.json
      tokenizer.json
      model-00001-of-00007.safetensors
      ...
    qwen3-32b-gguf-q4km/        <- GGUF models: single .gguf file
      Qwen3-32B-Q4_K_M.gguf

  ~/.cache/bart/cache.json      <- last-used timestamps for 24h TTL eviction

`cache.json` is a small JSON file mapping model.key → ISO timestamp. We
keep the model on disk for 24 h after last use to avoid re-downloading on
back-to-back runs, but evict older entries to free disk on quiet days.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Model

_TTL = timedelta(hours=24)


def cache_root() -> Path:
    """Root of the model cache. Respects XDG_CACHE_HOME on Linux."""
    base = os.environ.get("BART_CACHE_DIR")
    if base:
        return Path(base).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "bart"
    return Path.home() / ".cache" / "bart"


def models_dir() -> Path:
    d = cache_root() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir(m: Model) -> Path:
    """Where this model's files live on disk."""
    return models_dir() / m.key


def _index_path() -> Path:
    return cache_root() / "cache.json"


def _read_index() -> dict[str, str]:
    try:
        return json.loads(_index_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_index(idx: dict[str, str]) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, indent=2, sort_keys=True))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_downloaded(m: Model) -> bool:
    """True iff the expected files exist on disk and are non-trivial size.

    For MLX (full-repo snapshot) we just check that the directory exists and
    contains at least a `config.json`. For GGUF we check the specific file
    exists with size ≥ 80% of declared bytes_on_disk (catches partial
    downloads that resumed but didn't complete)."""
    d = model_dir(m)
    if not d.exists() or not d.is_dir():
        return False
    if m.format == "mlx":
        return (d / "config.json").exists()
    if m.format == "gguf":
        for fn in m.hf_filenames:
            f = d / fn
            if not f.exists():
                return False
            # Accept >=80% of declared size as "complete enough" — declared
            # size is approximate.
            if f.stat().st_size < (m.bytes_on_disk * 0.8) / max(1, len(m.hf_filenames)):
                return False
        return True
    return False


def touch(m: Model) -> None:
    idx = _read_index()
    idx[m.key] = _now_iso()
    _write_index(idx)


def is_fresh(m: Model) -> bool:
    idx = _read_index()
    ts = idx.get(m.key)
    if not ts:
        return False
    try:
        last = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last < _TTL


def evict_stale(on_evict=None) -> list[str]:
    idx = _read_index()
    now = datetime.now(timezone.utc)
    keep: dict[str, str] = {}
    evicted: list[str] = []
    for key, ts in idx.items():
        try:
            last = datetime.fromisoformat(ts)
        except ValueError:
            evicted.append(key)
            continue
        if now - last >= _TTL:
            evicted.append(key)
        else:
            keep[key] = ts
    for key in evicted:
        d = models_dir() / key
        if d.exists():
            try:
                shutil.rmtree(d)
            except OSError:
                pass
        if on_evict:
            on_evict(key)
    _write_index(keep)
    return evicted


def purge_all() -> list[str]:
    """Force-delete every cached model. Used by `./run cleanup`."""
    removed: list[str] = []
    if not models_dir().exists():
        return removed
    for d in models_dir().iterdir():
        if d.is_dir():
            try:
                shutil.rmtree(d)
                removed.append(d.name)
            except OSError:
                pass
    _write_index({})
    return removed
