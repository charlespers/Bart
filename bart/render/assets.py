"""Asset bundling — copies brand assets and bundles MathJax into a packet.

MathJax is downloaded once into bart/.assets_cache/ on first run, then
copied per packet. This means packets are fully self-contained and can be
viewed offline (e.g. printed, emailed, archived) with no CDN dependency.
"""
from __future__ import annotations

import shutil
import urllib.request
from pathlib import Path

from ..paths import ROOT


# Files to copy from the project's assets/ folder into each packet.
_BRAND_ASSETS = ["bart-loaf.svg", "bart-wordmark.svg", "bart-loaf.txt"]


# We bundle MathJax by downloading its core file once and caching locally.
# The stable CDN-style filename is well-known.
_MATHJAX_VERSION = "3.2.2"
_MATHJAX_BASE_URL = f"https://cdn.jsdelivr.net/npm/mathjax@{_MATHJAX_VERSION}/es5"
_MATHJAX_FILES = [
    # Just the entry-point bundle — it's self-contained ~2 MB.
    "tex-chtml.js",
]


def project_assets_dir() -> Path:
    return ROOT / "assets"


def cache_dir() -> Path:
    return ROOT / ".assets_cache"


def ensure_mathjax_cached() -> Path:
    """Ensure MathJax is in the local cache. Returns the cache subdir path."""
    target = cache_dir() / f"mathjax-{_MATHJAX_VERSION}"
    target.mkdir(parents=True, exist_ok=True)
    for fname in _MATHJAX_FILES:
        f = target / fname
        if f.exists() and f.stat().st_size > 0:
            continue
        url = f"{_MATHJAX_BASE_URL}/{fname}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                f.write_bytes(resp.read())
        except Exception as e:  # noqa: BLE001
            # If we can't fetch, write a tiny shim that emits a warning so
            # the page still loads with rendered HTML (just no math).
            f.write_text(
                f"// MathJax fetch failed at packet build time: {e}\n"
                "console.warn('bart: MathJax not available offline; equations will display as raw LaTeX.');\n"
            )
    return target


def copy_brand_assets(packet_dir: Path) -> None:
    """Copy bart-loaf.svg + wordmark + loaf ASCII into <packet>/assets/."""
    src = project_assets_dir()
    dst = packet_dir / "assets"
    dst.mkdir(parents=True, exist_ok=True)
    for fname in _BRAND_ASSETS:
        s = src / fname
        if s.exists():
            shutil.copy2(s, dst / fname)


def copy_mathjax(packet_dir: Path) -> None:
    """Copy the cached MathJax bundle into <packet>/lib/mathjax/."""
    cached = ensure_mathjax_cached()
    dst = packet_dir / "lib" / "mathjax"
    dst.mkdir(parents=True, exist_ok=True)
    for fname in _MATHJAX_FILES:
        s = cached / fname
        if s.exists():
            shutil.copy2(s, dst / fname)


def copy_template_assets(packet_dir: Path) -> None:
    """Copy packet.css and packet.js from the templates folder into the packet root."""
    tpl_dir = Path(__file__).parent / "templates"
    for fname in ("packet.css", "packet.js"):
        s = tpl_dir / fname
        if s.exists():
            shutil.copy2(s, packet_dir / fname)


def install_all(packet_dir: Path) -> None:
    """Convenience: ensure all bundled assets land in the packet."""
    copy_brand_assets(packet_dir)
    copy_template_assets(packet_dir)
    copy_mathjax(packet_dir)
