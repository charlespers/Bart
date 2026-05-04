"""Custom study tools — pluggable post-pipeline artifact generators.

Each tool implements:
    name: str       — short identifier
    description: str — one-line description for CLI listing
    run(packet_dir, manifest) -> ToolResult — does the work, returns metadata

Tools run AFTER the main pipeline. They consume the produced markdown/HTML
and produce additional study artifacts. Each tool is independently
opt-in/opt-out via config or CLI flag.

Currently shipped:
    - flashcards     : Anki .apkg deck generator (uses genanki)
    - mermaid        : Auto-generated Mermaid concept maps (no deps)
    - chem-reference : Static chemistry reference card (auto-gated on subject)
    - cs-reference   : Static computer-science reference card (auto-gated)
    - ece-reference  : Static ECE / signals reference card (auto-gated)

Domain reference tools cost zero LLM tokens — they ship hand-curated fact
tables and only emit when subject keywords match (or BART_TOOL_<NAME>=1).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .chem import ChemReferenceTool
from .cs import CSReferenceTool
from .ece import ECEReferenceTool
from .flashcards import FlashcardsTool
from .mermaid import MermaidTool


@dataclass
class ToolResult:
    name: str
    success: bool
    output_paths: list[Path]
    detail: str = ""


# Public registry — orchestrator iterates over this.
TOOLS = [
    FlashcardsTool(),
    MermaidTool(),
    ChemReferenceTool(),
    CSReferenceTool(),
    ECEReferenceTool(),
]


__all__ = [
    "ToolResult", "TOOLS",
    "FlashcardsTool", "MermaidTool",
    "ChemReferenceTool", "CSReferenceTool", "ECEReferenceTool",
]
