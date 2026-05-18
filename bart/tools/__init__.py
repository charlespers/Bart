"""Custom study tools — pluggable post-pipeline artifact generators.

Each tool implements:
    name: str       — short identifier
    description: str — one-line description for CLI listing
    run(packet_dir, manifest) -> ToolResult — does the work, returns metadata

Tools run AFTER the main pipeline. They consume the produced markdown/HTML
and produce additional study artifacts. Each tool is independently
opt-in/opt-out via config or CLI flag.

Currently shipped:
    - flashcards          : Anki .apkg deck generator (uses genanki)
    - mermaid             : Auto-generated Mermaid concept maps (no deps)
    - chem-reference      : Static chemistry reference card (auto-gated on subject)
    - cs-reference        : Static computer-science reference card (auto-gated)
    - ece-reference       : Static ECE / signals reference card (auto-gated)
    - physics-reference   : Static physics reference card (auto-gated)
    - biology-reference   : Static biology reference card (auto-gated)
    - economics-reference : Static economics reference card (auto-gated)
    - statistics-reference: Static statistics reference card (auto-gated)

Domain reference tools cost zero LLM tokens — they ship hand-curated fact
tables and only emit when subject keywords match (or BART_TOOL_<NAME>=1).
Adding a new subject = one file with a KEYWORDS tuple, a SECTIONS list, and
a tool class registered in TOOLS below.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .biology import BiologyReferenceTool
from .chem import ChemReferenceTool
from .cs import CSReferenceTool
from .ece import ECEReferenceTool
from .economics import EconomicsReferenceTool
from .flashcards import FlashcardsTool
from .mermaid import MermaidTool
from .physics import PhysicsReferenceTool
from .statistics import StatisticsReferenceTool


@dataclass
class ToolResult:
    name: str
    success: bool
    output_paths: list[Path]
    detail: str = ""


# Public registry — orchestrator iterates over this. Reference tools are
# self-gating: each emits only when its subject keywords match, so listing
# every domain here is safe — at most one or two fire for a given subject.
TOOLS = [
    FlashcardsTool(),
    MermaidTool(),
    ChemReferenceTool(),
    CSReferenceTool(),
    ECEReferenceTool(),
    PhysicsReferenceTool(),
    BiologyReferenceTool(),
    EconomicsReferenceTool(),
    StatisticsReferenceTool(),
]


__all__ = [
    "ToolResult", "TOOLS",
    "FlashcardsTool", "MermaidTool",
    "ChemReferenceTool", "CSReferenceTool", "ECEReferenceTool",
    "PhysicsReferenceTool", "BiologyReferenceTool",
    "EconomicsReferenceTool", "StatisticsReferenceTool",
]
