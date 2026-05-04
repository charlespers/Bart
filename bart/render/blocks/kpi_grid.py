"""KPI grid — small stat-card row at the top of the index page.

Shows: total artifacts, total days, est. cost, generation time.
"""
from __future__ import annotations

from typing import ClassVar

from .base import Block, he


class KpiGridBlock(Block):
    name = "kpi_grid"
    css_classes: ClassVar[list[str]] = ["block-kpi-grid"]

    def __init__(self, kpis: list[dict]):
        """Each kpi: {label: str, value: str, hint?: str}"""
        self.kpis = kpis

    def render(self) -> str:
        if not self.kpis:
            return ""
        cards: list[str] = []
        for k in self.kpis:
            hint = (
                f'<div class="block-kpi-hint">{he(k["hint"])}</div>'
                if k.get("hint") else ""
            )
            cards.append(
                '<div class="block-kpi-card">'
                f'<div class="block-kpi-label">{he(k["label"])}</div>'
                f'<div class="block-kpi-value">{he(k["value"])}</div>'
                f'{hint}'
                '</div>'
            )
        return f'<div class="block-kpi-grid">{"".join(cards)}</div>'
