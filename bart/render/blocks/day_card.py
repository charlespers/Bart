"""Day card — one entry in the landing page's day grid."""
from __future__ import annotations

from typing import ClassVar

from .base import Block, he


_FOCUS_LABEL = {
    "learn": "learn",
    "practice": "practice",
    "review": "review",
    "mock-exam": "mock exam",
    "mock_exam": "mock exam",
}


class DayCardBlock(Block):
    name = "day_card"
    css_classes: ClassVar[list[str]] = ["block-day-card"]

    def __init__(self, day_num: int, topic: str, date: str, focus: str, href: str):
        self.day_num = day_num
        self.topic = topic
        self.date = date
        self.focus = focus
        self.href = href

    def render(self) -> str:
        focus_class = self.focus.replace("-", "_")
        focus_label = _FOCUS_LABEL.get(self.focus, self.focus)
        return (
            f'<a class="block-day-card focus-{he(focus_class)}" href="{he(self.href)}">'
            f'<div class="block-day-card-num">Day {self.day_num:02d}</div>'
            f'<div class="block-day-card-topic">{he(self.topic) or "—"}</div>'
            f'<div class="block-day-card-meta">'
            f'<span class="block-day-card-date">{he(self.date)}</span>'
            f'<span class="block-day-card-focus">{he(focus_label)}</span>'
            f'</div></a>'
        )


class DayGridBlock(Block):
    """Wrapper that holds many DayCardBlocks in a CSS grid."""
    name = "day_grid"
    css_classes: ClassVar[list[str]] = ["block-day-grid"]

    def __init__(self, cards: list[DayCardBlock], heading: str = "Daily lessons"):
        self.cards = cards
        self.heading = heading

    def render(self) -> str:
        if not self.cards:
            return ""
        inner = "\n".join(c.render() for c in self.cards)
        return (
            f'<section class="block-day-grid-section">'
            f'<h2 class="block-section-heading">{he(self.heading)}</h2>'
            f'<div class="block-day-grid">{inner}</div>'
            '</section>'
        )
