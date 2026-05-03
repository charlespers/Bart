"""bart.render — markdown -> sanitized -> HTML packet pipeline.

Public surface:
    build_packet(run_dir: Path, manifest: dict) -> list[Warning]
        Reads every .md artifact in the run directory, sanitizes it,
        renders to HTML wrapped in the bart template, copies bundled
        assets, builds a search index, and writes index.html.

The pipeline is strict: warnings are accumulated to render_warnings.json,
and a final validation pass refuses to ship pages with invalid HTML or
broken anchors.
"""
from __future__ import annotations

from .packet import Warning, build_packet  # noqa: F401

__all__ = ["build_packet", "Warning"]
