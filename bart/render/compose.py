"""Page composition — assemble blocks into a final HTML body.

Usage:
    page = Page()
    page.add(HeroBlock(...))
    page.add(ArtifactGridBlock(...))
    page.add(DayGridBlock(...))
    body_html = page.render()

The Page is a thin list of blocks. The page-level template (page.py) wraps
the rendered body with sidebar / topbar / pager / etc.
"""
from __future__ import annotations

from typing import Iterable

from .blocks.base import Block


class Page:
    def __init__(self):
        self._blocks: list[Block] = []

    def add(self, block: Block | None) -> "Page":
        if block is not None:
            self._blocks.append(block)
        return self

    def extend(self, blocks: Iterable[Block]) -> "Page":
        for b in blocks:
            self.add(b)
        return self

    def render(self) -> str:
        return "\n".join(b.render() for b in self._blocks if b is not None)

    @property
    def blocks(self) -> list[Block]:
        return list(self._blocks)
