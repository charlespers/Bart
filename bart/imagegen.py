"""Open-source custom image generation for study packets.

A ``bart-figure`` block lets the Author *ask for an image* of something — "a
labeled diagram of a neuron", "a clean graph of exponential decay" — and get a
real, custom illustration embedded in the packet. This module turns that
prompt into image bytes.

Two open backends, picked automatically (``get_backend``):

  - **LocalSDBackend** — a Stable Diffusion server the operator runs
    themselves (AUTOMATIC1111 / Forge / ComfyUI with the A1111-compatible
    API). Fully offline and fully open. Selected when ``BART_IMAGE_SD_URL``
    is set.

  - **PollinationsBackend** — pollinations.ai, a free and open-source hosted
    generator. No API key, no signup. The zero-config default.

Image generation is **disabled by default** (``BART_IMAGE_GEN`` unset). When
disabled, the ``bart-figure`` renderer draws a tasteful placeholder instead,
so a packet never breaks and never blocks on the network. Enable per-run with
``BART_IMAGE_GEN=1`` (the orchestrator sets this from ``cfg.image_generation``).

Generated images are cached on disk by prompt hash, so re-runs and repeated
prompts are free, and embedded into the HTML as self-contained ``data:`` URIs
— a packet stays portable with zero external image dependencies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import ROOT


# ── Enable / limits ────────────────────────────────────────────────────

_TRUE = {"1", "true", "yes", "on"}


def image_generation_enabled() -> bool:
    """True iff this run should generate real images for ``bart-figure``.

    Off by default: ``bart-figure`` renders a placeholder instead, so packets
    build fast and offline-safe unless image generation is explicitly turned
    on with ``BART_IMAGE_GEN=1``.
    """
    return os.environ.get("BART_IMAGE_GEN", "").strip().lower() in _TRUE


# A per-process ceiling so a packet that asks for dozens of figures can't turn
# into a multi-minute wall of network calls. Tunable; counts successful gens.
_MAX_IMAGES = max(1, int(os.environ.get("BART_IMAGE_MAX", "24")))
_count_lock = threading.Lock()
_generated = 0


def _budget_left() -> bool:
    with _count_lock:
        return _generated < _MAX_IMAGES


def _spend_budget() -> None:
    global _generated
    with _count_lock:
        _generated += 1


# ── Disk cache ─────────────────────────────────────────────────────────

def _cache_dir() -> Path:
    base = os.environ.get("BART_IMAGE_CACHE_DIR")
    d = Path(base).expanduser() if base else ROOT / ".assets_cache" / "figures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(backend: str, prompt: str, width: int, height: int) -> str:
    h = hashlib.sha1(f"{backend}|{width}x{height}|{prompt}".encode("utf-8"))
    return h.hexdigest()[:20]


def _seed_for(prompt: str) -> int:
    """A deterministic seed from the prompt — same prompt → same image, so
    caching is meaningful and re-runs are reproducible."""
    return int(hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8], 16)


# ── Prompt shaping ─────────────────────────────────────────────────────

# Appended to every prompt so generated images suit a study guide: legible,
# diagrammatic, uncluttered — not glossy stock-photo noise.
_STYLE_SUFFIX = (
    ", clean educational illustration, clear and legible, well labeled, "
    "high detail, neutral light background, no watermark"
)

# Steered away on backends that accept a negative prompt (local SD).
_NEGATIVE = "blurry, low quality, distorted text, cluttered, watermark, signature"


def _shape_prompt(prompt: str) -> str:
    p = (prompt or "").strip()
    return f"{p}{_STYLE_SUFFIX}" if p else p


# ── Backends ───────────────────────────────────────────────────────────

class PollinationsBackend:
    """pollinations.ai — free, open-source, no API key. The default backend.

    Fetched via ``curl`` when available (sidesteps macOS Python's missing CA
    bundle, the same reason the local-model installer prefers curl), with a
    ``urllib`` fallback.
    """

    name = "pollinations"
    _BASE = "https://image.pollinations.ai/prompt/"

    def available(self) -> bool:
        return True  # no precondition — it's a public HTTP endpoint

    def generate(self, prompt: str, width: int, height: int) -> bytes | None:
        shaped = _shape_prompt(prompt)
        query = urllib.parse.urlencode({
            "width": width, "height": height,
            "seed": _seed_for(prompt), "nologo": "true",
        })
        url = f"{self._BASE}{urllib.parse.quote(shaped)}?{query}"
        if shutil.which("curl"):
            try:
                out = subprocess.run(
                    ["curl", "-sL", "--max-time", "90", "-o", "-", url],
                    capture_output=True, timeout=100,
                )
                if out.returncode == 0 and out.stdout[:3] in (b"\xff\xd8\xff", b"\x89PN"):
                    return out.stdout
            except (subprocess.SubprocessError, OSError):
                pass
        # Fallback: urllib (may fail SSL on some macOS Pythons — best effort).
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bart/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if data[:3] in (b"\xff\xd8\xff", b"\x89PN"):
                return data
        except Exception:  # noqa: BLE001 — any network/SSL failure → no image
            pass
        return None


class LocalSDBackend:
    """A self-hosted Stable Diffusion server with the AUTOMATIC1111-compatible
    ``/sdapi/v1/txt2img`` API. Fully offline and open. Enabled by setting
    ``BART_IMAGE_SD_URL`` (e.g. ``http://127.0.0.1:7860``)."""

    name = "local-sd"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.base_url)

    def generate(self, prompt: str, width: int, height: int) -> bytes | None:
        payload = json.dumps({
            "prompt": _shape_prompt(prompt),
            "negative_prompt": _NEGATIVE,
            "width": width, "height": height,
            "steps": int(os.environ.get("BART_IMAGE_SD_STEPS", "22")),
            "seed": _seed_for(prompt),
            "sampler_name": "DPM++ 2M",
        }).encode("utf-8")
        url = f"{self.base_url}/sdapi/v1/txt2img"
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                body = json.loads(r.read().decode("utf-8"))
            images = body.get("images") or []
            if images:
                return base64.b64decode(images[0])
        except Exception:  # noqa: BLE001 — server down / bad response → no image
            pass
        return None


def get_backend():
    """Pick an image backend. Local SD wins when configured (offline, the
    operator's own hardware); otherwise the free Pollinations default."""
    sd_url = os.environ.get("BART_IMAGE_SD_URL", "").strip()
    if sd_url:
        return LocalSDBackend(sd_url)
    return PollinationsBackend()


# ── Public API ─────────────────────────────────────────────────────────

def _detect_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def generate_image(prompt: str, *, width: int = 768, height: int = 512) -> bytes | None:
    """Generate (or fetch from cache) an image for ``prompt``.

    Returns the raw image bytes, or ``None`` if generation is disabled, the
    per-run budget is spent, or every backend failed. Callers must treat
    ``None`` as "draw a placeholder" — image generation never raises.
    """
    if not image_generation_enabled():
        return None
    prompt = (prompt or "").strip()
    if not prompt:
        return None

    backend = get_backend()
    cache = _cache_dir()
    key = _cache_key(backend.name, prompt, width, height)
    cached = cache / f"{key}.img"
    if cached.exists():
        try:
            return cached.read_bytes()
        except OSError:
            pass

    if not _budget_left():
        return None
    try:
        data = backend.generate(prompt, width, height)
    except Exception:  # noqa: BLE001 — defensive; backends already swallow
        data = None
    if not data:
        return None

    _spend_budget()
    try:
        cached.write_bytes(data)
    except OSError:
        pass
    return data


def figure_data_uri(prompt: str, *, width: int = 768, height: int = 512) -> str | None:
    """Generate an image for ``prompt`` and return it as a ``data:`` URI ready
    to drop into an ``<img src>``. ``None`` when no image was produced."""
    data = generate_image(prompt, width=width, height=height)
    if not data:
        return None
    mime = _detect_mime(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
