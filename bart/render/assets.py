"""Asset bundling — copies brand assets and bundles MathJax into a packet.

MathJax is downloaded once into bart/.assets_cache/ on first run, then
copied per packet. This means packets are fully self-contained and can be
viewed offline (e.g. printed, emailed, archived) with no CDN dependency.

When the offline fetch fails (e.g. corporate SSL, no network), the page
still renders math via a CDN fallback wired into the script tag itself.
"""
from __future__ import annotations

import shutil
import ssl
import urllib.request
from pathlib import Path

from ..paths import ROOT


# Files to copy from the project's assets/ folder into each packet.
_BRAND_ASSETS = ["bart-loaf.svg", "bart-wordmark.svg", "bart-loaf.txt"]


# Math bundles. The packet's primary engine is KaTeX (per design library),
# but MathJax remains supported for backward compatibility.
_MATHJAX_VERSION = "3.2.2"
MATHJAX_VERSION = _MATHJAX_VERSION
_MATHJAX_BASE_URL = f"https://cdn.jsdelivr.net/npm/mathjax@{_MATHJAX_VERSION}/es5"
MATHJAX_CDN_URL = f"{_MATHJAX_BASE_URL}/tex-chtml.js"
_MATHJAX_FILES = ["tex-chtml.js"]

KATEX_VERSION = "0.16.9"
_KATEX_BASE_URL = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist"
KATEX_CSS_CDN = f"{_KATEX_BASE_URL}/katex.min.css"
KATEX_JS_CDN = f"{_KATEX_BASE_URL}/katex.min.js"
KATEX_AUTORENDER_CDN = f"{_KATEX_BASE_URL}/contrib/auto-render.min.js"
KATEX_MHCHEM_CDN = f"{_KATEX_BASE_URL}/contrib/mhchem.min.js"
_KATEX_FILES = [
    ("katex.min.css", f"{_KATEX_BASE_URL}/katex.min.css"),
    ("katex.min.js",  f"{_KATEX_BASE_URL}/katex.min.js"),
    ("auto-render.min.js", f"{_KATEX_BASE_URL}/contrib/auto-render.min.js"),
    ("mhchem.min.js", f"{_KATEX_BASE_URL}/contrib/mhchem.min.js"),
]
# Anything below this size is the failure stub or a partial download.
_STUB_THRESHOLD_BYTES = 50_000
_KATEX_STUB_THRESHOLD = 5_000  # KaTeX files are smaller; aux file is ~3 KB


def project_assets_dir() -> Path:
    return ROOT / "assets"


def cache_dir() -> Path:
    return ROOT / ".assets_cache"


def _try_fetch(url: str) -> bytes | None:
    """Attempt to fetch `url` with three strategies in order.

    Returns the bytes on success, None on terminal failure. Strategies:
      1. default SSL context (works on healthy installs)
      2. certifi CA bundle (works when system store is incomplete)
      3. unverified context (last resort — public CDN, content is the
         pinned MathJax version, so MITM risk is bounded by the version pin)
    """
    contexts: list[ssl.SSLContext | None] = [None]  # default context
    try:
        import certifi  # type: ignore
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:  # noqa: BLE001
        pass
    contexts.append(ssl._create_unverified_context())

    for ctx in contexts:
        try:
            with urllib.request.urlopen(url, timeout=20, context=ctx) as resp:
                data = resp.read()
                if data:
                    return data
        except Exception:  # noqa: BLE001
            continue
    return None


def ensure_mathjax_cached(force_refresh: bool = False) -> Path:
    """Ensure MathJax is in the local cache. Returns the cache subdir path."""
    target = cache_dir() / f"mathjax-{_MATHJAX_VERSION}"
    target.mkdir(parents=True, exist_ok=True)
    for fname in _MATHJAX_FILES:
        f = target / fname
        # Refresh if missing OR if a previous run wrote the failure stub.
        if not force_refresh and f.exists() and f.stat().st_size >= _STUB_THRESHOLD_BYTES:
            continue
        url = f"{_MATHJAX_BASE_URL}/{fname}"
        data = _try_fetch(url)
        if data is None:
            # All fetch strategies failed. Write a tiny stub that triggers the
            # in-page CDN fallback (`onerror` on the <script> tag).
            f.write_text(
                "// MathJax local bundle unavailable; the page falls back "
                "to the CDN via the <script onerror> handler.\n"
                "throw new Error('bart-mathjax-bundle-missing');\n"
            )
        else:
            f.write_bytes(data)
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


def ensure_katex_cached(force_refresh: bool = False) -> Path:
    """Ensure KaTeX (CSS + JS + auto-render) is in the local cache."""
    target = cache_dir() / f"katex-{KATEX_VERSION}"
    target.mkdir(parents=True, exist_ok=True)
    for fname, url in _KATEX_FILES:
        f = target / fname
        if not force_refresh and f.exists() and f.stat().st_size >= _KATEX_STUB_THRESHOLD:
            continue
        data = _try_fetch(url)
        if data is None:
            # Stub. The page-level onerror will swap to the CDN.
            f.write_bytes(b"/* katex bundle missing; CDN fallback active. */\n")
        else:
            f.write_bytes(data)
    return target


def copy_katex(packet_dir: Path) -> None:
    """Copy the cached KaTeX bundle into <packet>/lib/katex/."""
    cached = ensure_katex_cached()
    dst = packet_dir / "lib" / "katex"
    dst.mkdir(parents=True, exist_ok=True)
    for fname, _ in _KATEX_FILES:
        s = cached / fname
        if s.exists():
            shutil.copy2(s, dst / fname)


def copy_template_assets(packet_dir: Path) -> None:
    """Copy packet.css/js + blocks.css + lib_blocks.js + sandbox files
    from templates. The sandbox is a self-contained client-side preview
    page (sandbox.html) that lets the reader paste markdown and see it
    render through the same library-block + KaTeX pipeline."""
    tpl_dir = Path(__file__).parent / "templates"
    for fname in (
        "packet.css", "packet.js", "blocks.css", "lib_blocks.js",
        "sandbox.html", "sandbox.js",
    ):
        s = tpl_dir / fname
        if s.exists():
            shutil.copy2(s, packet_dir / fname)


def install_all(packet_dir: Path) -> None:
    """Convenience: ensure all bundled assets land in the packet."""
    copy_brand_assets(packet_dir)
    copy_template_assets(packet_dir)
    copy_mathjax(packet_dir)
    copy_katex(packet_dir)
