"""Pull grounding facts from studywithbart.com.

The script generator must talk about what Bart actually is, in Bart's
actual voice — not hallucinate features. This module fetches the live site
(homepage + the public `/sample` packet) and extracts plain-text positioning
copy. If the site is unreachable it falls back to the repo's GROWTH-KIT.md,
so `generate` never hard-fails on a network blip.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .paths import REPO_ROOT

_TIMEOUT = 15
_HEADERS = {"User-Agent": "bart-outreach/0.1 (+studywithbart.com)"}


@dataclass
class ProductFacts:
    """Everything the script generator is allowed to ground claims in."""

    source: str                       # "studywithbart.com" or "GROWTH-KIT.md"
    homepage_text: str = ""
    sample_text: str = ""
    extra: dict = field(default_factory=dict)

    def as_prompt_block(self) -> str:
        parts = [f"SOURCE: {self.source}"]
        if self.homepage_text:
            parts.append("--- studywithbart.com homepage ---\n" + self.homepage_text)
        if self.sample_text:
            parts.append("--- public sample packet ---\n" + self.sample_text)
        return "\n\n".join(parts)


def _visible_text(html: str, limit: int = 6000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:limit]


def _fetch(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def _growth_kit_fallback() -> ProductFacts:
    kit = REPO_ROOT / "GROWTH-KIT.md"
    text = kit.read_text() if kit.exists() else ""
    # Section 1 of GROWTH-KIT.md is the canonical positioning block.
    positioning = text.split("## 2.")[0] if "## 2." in text else text
    return ProductFacts(source="GROWTH-KIT.md", homepage_text=positioning[:6000])


def fetch_product_facts(base_url: str, *, cache_path: Path | None = None) -> ProductFacts:
    """Fetch product facts, using a per-day cache file if provided."""
    if cache_path and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            return ProductFacts(**data)
        except (json.JSONDecodeError, TypeError, OSError):
            pass

    base = base_url.rstrip("/")
    home = _fetch(base + "/")
    sample = _fetch(base + "/sample")

    if home is None and sample is None:
        facts = _growth_kit_fallback()
    else:
        facts = ProductFacts(
            source="studywithbart.com",
            homepage_text=_visible_text(home) if home else "",
            sample_text=_visible_text(sample) if sample else "",
        )

    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(facts.__dict__, indent=2))
        except OSError:
            pass
    return facts
