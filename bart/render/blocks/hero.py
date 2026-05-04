"""Hero block — top of the landing page (index.html).

Loaf logo + subject title with the terracotta dot + tagline + meta line.
This is the first thing a student sees when they open the packet.
"""
from __future__ import annotations

from typing import ClassVar

from .base import Block, he


class HeroBlock(Block):
    name = "hero"
    css_classes: ClassVar[list[str]] = ["block-hero"]

    def __init__(
        self,
        subject: str,
        generated_at: str,
        exam_date: str,
        days_until: int | None = None,
        rel_root: str = ".",
    ):
        self.subject = subject
        self.generated_at = generated_at
        self.exam_date = exam_date
        self.days_until = days_until
        self.rel_root = rel_root

    def render(self) -> str:
        days_str = f" · {self.days_until} day(s) left" if self.days_until is not None else ""
        return (
            '<section class="block-hero">'
            '<div class="block-hero-loaf-wrap">'
            f'<img class="block-hero-loaf" src="{self.rel_root}/assets/bart-loaf.svg" '
            'alt="bart" width="120" height="109" />'
            '</div>'
            '<div class="block-hero-content">'
            f'<h1 class="block-hero-title">{he(self.subject)}'
            '<span class="block-hero-dot">.</span></h1>'
            f'<div class="block-hero-meta">generated {he(self.generated_at)} · '
            f'exam {he(self.exam_date)}{he(days_str)}</div>'
            '<p class="block-hero-tagline">A study packet built from your own course materials. '
            'Open the master plan first; come back here to navigate between days.</p>'
            '</div>'
            '</section>'
        )
