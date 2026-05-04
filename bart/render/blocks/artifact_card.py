"""Artifact card — links from the landing page to top-level artifacts."""
from __future__ import annotations

from typing import ClassVar

from .base import Block, he


_ICONS = {
    "00_master_plan.html":     "M",  # Master
    "01_schematics.html":      "S",  # Schematics
    "02_whimsical_notes.html": "W",  # Whimsy
    "03_short_guide.html":     "Q",  # Quick guide
    "04_practice_exam.html":   "X",  # eXam
}


class ArtifactCardBlock(Block):
    name = "artifact_card"
    css_classes: ClassVar[list[str]] = ["block-artifact-card"]

    def __init__(self, title: str, blurb: str, href: str):
        self.title = title
        self.blurb = blurb
        self.href = href

    def render(self) -> str:
        icon = _ICONS.get(self.href, "·")
        return (
            f'<a class="block-artifact-card" href="{he(self.href)}">'
            f'<div class="block-artifact-icon">{he(icon)}</div>'
            f'<div class="block-artifact-body">'
            f'<h3 class="block-artifact-title">{he(self.title)}</h3>'
            f'<p class="block-artifact-blurb">{he(self.blurb)}</p>'
            f'</div></a>'
        )


class ArtifactGridBlock(Block):
    name = "artifact_grid"
    css_classes: ClassVar[list[str]] = ["block-artifact-grid"]

    def __init__(self, cards: list[ArtifactCardBlock], heading: str = "Top-level artifacts"):
        self.cards = cards
        self.heading = heading

    def render(self) -> str:
        if not self.cards:
            return ""
        inner = "\n".join(c.render() for c in self.cards)
        return (
            f'<section class="block-artifact-grid-section">'
            f'<h2 class="block-section-heading">{he(self.heading)}</h2>'
            f'<div class="block-artifact-grid">{inner}</div>'
            '</section>'
        )
