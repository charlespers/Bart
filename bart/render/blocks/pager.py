"""Pager — designed prev/next navigation with terracotta nub."""
from __future__ import annotations

from typing import ClassVar

from .base import Block, he


class PagerBlock(Block):
    name = "pager"
    css_classes: ClassVar[list[str]] = ["block-pager"]

    def __init__(
        self,
        prev_url: str | None = None, prev_label: str | None = None,
        next_url: str | None = None, next_label: str | None = None,
    ):
        self.prev_url = prev_url
        self.prev_label = prev_label
        self.next_url = next_url
        self.next_label = next_label

    def render(self) -> str:
        if not self.prev_url and not self.next_url:
            return ""
        prev_html = (
            f'<a class="block-pager-link block-pager-prev" href="{he(self.prev_url)}">'
            f'<span class="block-pager-direction">← previous</span>'
            f'<span class="block-pager-title">{he(self.prev_label or "")}</span></a>'
            if self.prev_url else '<span class="block-pager-spacer"></span>'
        )
        next_html = (
            f'<a class="block-pager-link block-pager-next" href="{he(self.next_url)}">'
            f'<span class="block-pager-direction">next →</span>'
            f'<span class="block-pager-title">{he(self.next_label or "")}</span></a>'
            if self.next_url else '<span class="block-pager-spacer"></span>'
        )
        return f'<nav class="block-pager">{prev_html}<span class="block-pager-nub">·</span>{next_html}</nav>'
