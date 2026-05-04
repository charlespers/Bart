"""Modular structural blocks for bart packet pages.

Scope is deliberately tight: page-level chrome (hero, grids, pager). The
*body* of each page remains markdown-rendered prose — adding ornamental
in-content boxes (DEFINITION/INTUITION/TRAP badges) competes with the
actual explanation. Clean prose is the goal; blocks just compose the
navigation around it.

Adding a new block: subclass `Block` in a new file, implement `render()`,
import it here.
"""
from __future__ import annotations

from .artifact_card import ArtifactCardBlock, ArtifactGridBlock
from .base import Block, ProseBlock
from .day_card import DayCardBlock, DayGridBlock
from .hero import HeroBlock
from .kpi_grid import KpiGridBlock
from .pager import PagerBlock


__all__ = [
    "Block", "ProseBlock",
    "HeroBlock", "KpiGridBlock",
    "DayCardBlock", "DayGridBlock",
    "ArtifactCardBlock", "ArtifactGridBlock",
    "PagerBlock",
]
