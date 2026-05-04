"""Corpus assembly — gather extracted text into a single, budget-respecting block.

Strategy:
- Each file gets a header + body.
- Files larger than the per-file cap get a head + tail slice (preserves intro & conclusion).
- Total budget is enforced; once exhausted, remaining files are noted but their content is dropped.
- A separate manifest of all files is preserved for the planner to reason about.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ..extractors import REGISTRY, extract, supported
from ..paths import MATERIALS


@dataclass
class ExtractedFile:
    rel_path: str
    text: str
    char_count: int
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class Corpus:
    files: list[ExtractedFile]
    body: str
    total_chars: int
    skipped_files: list[ExtractedFile]


def gather_files() -> list[Path]:
    if not MATERIALS.exists():
        MATERIALS.mkdir(parents=True, exist_ok=True)
    return sorted(
        p for p in MATERIALS.rglob("*")
        if p.is_file() and not p.name.startswith(".") and p.name != ".gitkeep"
    )


def extract_all(console: Console | None = None) -> tuple[list[ExtractedFile], list[ExtractedFile]]:
    """Extract every file in materials/. Returns (kept, skipped)."""
    paths = gather_files()
    if not paths:
        msg = (
            f"\n  [yellow]No files in[/yellow] [cyan]{MATERIALS}/[/cyan]\n\n"
            f"  [bold]To get started:[/bold]\n"
            f"    1. Drop your course materials (PDFs, slides, notes) into [cyan]materials/[/cyan]\n"
            f"    2. Run [white]./run[/white] again\n\n"
            f"  [dim]Supported: .pdf .docx .pptx .md .txt[/dim]\n"
        )
        if console:
            console.print(msg)
        raise FileNotFoundError(f"No files in {MATERIALS}/. Drop materials there first.")

    kept: list[ExtractedFile] = []
    skipped: list[ExtractedFile] = []
    for path in paths:
        rel = str(path.relative_to(MATERIALS))
        if not supported(path):
            sf = ExtractedFile(
                rel_path=rel, text="", char_count=0, skipped=True,
                skip_reason=f"unsupported extension {path.suffix.lower()}",
            )
            skipped.append(sf)
            if console:
                console.print(f"  [yellow]⊘[/yellow] skip [dim]{rel}[/dim] ({sf.skip_reason})")
            continue
        text, err = extract(path)
        # Treat empty AND mojibake outputs as failures — gibberish poisons
        # downstream agents (the model wastes tokens trying to make sense
        # of OCR-of-handwriting / broken-font-map output).
        is_pdf = path.suffix.lower() == ".pdf"
        looks_bad = False
        bad_reason = ""
        if is_pdf and text.strip():
            try:
                from ..extractors.pdf_ex import _is_gibberish
                if _is_gibberish(text):
                    looks_bad = True
                    bad_reason = "extracted text is mojibake (broken font map or OCR-of-handwriting)"
            except Exception:  # noqa: BLE001
                pass
        if err or not text.strip() or looks_bad:
            reason = err or bad_reason or "empty"
            if is_pdf and not err:
                try:
                    from ..extractors.pdf_ex import diagnose_pdf
                    reason = diagnose_pdf(path)
                except Exception:  # noqa: BLE001
                    pass
            sf = ExtractedFile(
                rel_path=rel, text="", char_count=0, skipped=True,
                skip_reason=reason,
            )
            skipped.append(sf)
            if console:
                console.print(f"  [yellow]⊘[/yellow] skip [dim]{rel}[/dim] ({sf.skip_reason})")
            continue
        ef = ExtractedFile(rel_path=rel, text=text, char_count=len(text))
        kept.append(ef)
        if console:
            # If a PDF still came back thin AFTER OCR auto-trigger, the
            # user is missing the OCR stack. Surface the install hint.
            tail = ""
            if is_pdf and ef.char_count < 500:
                from ..extractors.pdf_ex import _ocr_stack_available
                if not _ocr_stack_available():
                    tail = (
                        " [yellow](mostly image — install tesseract+pdf2image "
                        "for auto-OCR)[/yellow]"
                    )
            console.print(f"  [green]✓[/green] [dim]{rel}[/dim] ([cyan]{ef.char_count:,}[/cyan] chars){tail}")
    return kept, skipped


def build_corpus(
    kept: list[ExtractedFile],
    skipped: list[ExtractedFile],
    char_budget: int = 400_000,
) -> Corpus:
    if not kept:
        raise RuntimeError("No usable files after extraction.")

    parts: list[str] = []
    used = 0
    per_file_cap = max(20_000, char_budget // max(len(kept), 1))

    for ef in kept:
        header = f"\n\n=== FILE: {ef.rel_path} ===\n\n"
        body = ef.text
        if len(body) > per_file_cap:
            half = per_file_cap // 2
            body = (
                body[:half]
                + f"\n\n[…truncated {len(ef.text) - per_file_cap:,} mid-document chars…]\n\n"
                + body[-half:]
            )
        if used + len(header) + len(body) > char_budget:
            remaining = char_budget - used - len(header)
            if remaining > 1000:
                body = body[:remaining] + "\n\n[…corpus budget exhausted…]"
                parts.append(header + body)
                used += len(header) + len(body)
            break
        parts.append(header + body)
        used += len(header) + len(body)

    body = "".join(parts)
    return Corpus(files=kept, body=body, total_chars=used, skipped_files=skipped)
