"""PDF extraction via pypdf."""
from __future__ import annotations

from pathlib import Path


def extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            parts.append(f"[page {i + 1} extraction failed: {e}]")
    return "\n\n".join(parts).strip()
