"""Material extraction — pluggable per file extension."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .docx_ex import extract_docx
from .pdf_ex import extract_pdf
from .pptx_ex import extract_pptx
from .text_ex import extract_text

REGISTRY: dict[str, Callable[[Path], str]] = {
    ".pdf": extract_pdf,
    ".txt": extract_text,
    ".md": extract_text,
    ".markdown": extract_text,
    ".docx": extract_docx,
    ".pptx": extract_pptx,
}


def supported(path: Path) -> bool:
    return path.suffix.lower() in REGISTRY


def extract(path: Path) -> tuple[str, str | None]:
    """Returns (text, error). Empty text + None error means file legitimately had no text."""
    fn = REGISTRY.get(path.suffix.lower())
    if not fn:
        return "", f"unsupported extension {path.suffix.lower()}"
    try:
        return fn(path), None
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"
