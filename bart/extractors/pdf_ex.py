"""PDF extraction with fallbacks.

Three failure modes are common with academic PDFs and they all produce
useless input if not handled:

  1. Encrypted PDF                 → pypdf raises or returns blank.
  2. Image-only / scanned PDF      → text extraction returns whitespace.
  3. Custom font / broken ToUnicode → extracted text is mojibake (e.g.
                                       LaTeX-typeset PDFs whose font maps
                                       use private-use glyph indices).

This module attempts extractors in order of cost:

    pypdf  →  pdfplumber  →  pytesseract OCR

Each step's output is sanity-checked with a *gibberish detector* — if the
text is mostly non-letter characters, we throw it away and fall through.
OCR is opt-in (gated by `BART_PDF_OCR=1`) since it requires the system
binary `tesseract` and `pdf2image`/`Pillow`, plus it's slow.

Returns a string. On total failure, returns the empty string and the
caller's "empty" path triggers (so the user sees a clear skip reason).
"""
from __future__ import annotations

import logging as _logging
import os
import re
import warnings as _warnings
from pathlib import Path

# Silence pypdf's "Ignoring wrong pointing object" noise. These warnings
# come from PDFs with off-by-one xref tables — pypdf recovers and the
# extracted text is fine, but the messages flood stderr and look alarming.
# Library-level suppression: scoped to pypdf's logger only, no global side-
# effects on other warnings.
_logging.getLogger("pypdf").setLevel(_logging.ERROR)
_warnings.filterwarnings("ignore", module="pypdf")


# Unicode replacement chars + private-use-area glyphs are the dead giveaway
# of a broken ToUnicode map. Counting ASCII letters / digits / common
# punctuation gives a "real text" ratio.
_TEXTY_RE = re.compile(r"[A-Za-z0-9\s\.,;:!?'\"\(\)\[\]\-\—\–]")


def _is_gibberish(text: str, *, min_ratio: float = 0.55, min_chars: int = 80) -> bool:
    """Heuristic: True if the text is too non-letterish to be real prose.

    `min_ratio`: fraction of characters that must look like ASCII text.
    `min_chars`: short outputs are exempt (a 30-char abstract is fine).

    Tuned so that:
      - Real text (incl. some math notation) passes easily.
      - Mojibake from broken font maps fails (mostly U+F000-range glyphs).
      - Pure-image-only output (whitespace + page numbers) also flagged
        as gibberish so the next extractor gets a turn.
    """
    if not text or len(text.strip()) < min_chars:
        return True
    sample = text[:5000]
    texty = sum(1 for c in sample if _TEXTY_RE.match(c))
    return (texty / max(len(sample), 1)) < min_ratio


def _try_pypdf(path: Path) -> str:
    """First-line extractor. Fast, handles most well-typeset PDFs."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            try:
                # Many "encrypted" academic PDFs are encrypted with empty
                # password (read-only protection); pypdf can decrypt those.
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return ""
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                parts.append("")
        return "\n\n".join(parts).strip()
    except Exception:  # noqa: BLE001
        return ""


def _try_pdfplumber(path: Path) -> str:
    """Second-line extractor. Handles broken font maps better than pypdf."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return ""
    try:
        parts: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                try:
                    txt = page.extract_text() or ""
                except Exception:  # noqa: BLE001
                    txt = ""
                if txt:
                    parts.append(txt)
        return "\n\n".join(parts).strip()
    except Exception:  # noqa: BLE001
        return ""


def _ocr_stack_available() -> bool:
    """True if pytesseract + pdf2image + the tesseract binary are all present."""
    try:
        import pytesseract  # type: ignore  # noqa: F401
        import pdf2image  # type: ignore  # noqa: F401
    except ImportError:
        return False
    import shutil as _sh
    if not _sh.which("tesseract"):
        return False
    if not _sh.which("pdftoppm"):  # poppler — pdf2image needs it
        return False
    return True


def _try_ocr(path: Path, *, max_pages: int | None = None) -> str:
    """OCR fallback for image-only / scanned PDFs.

    Auto-triggers when text extraction returns empty / mostly-image output,
    PROVIDED the OCR stack is installed (pytesseract + pdf2image + the
    `tesseract` and `pdftoppm` binaries). Set `BART_PDF_OCR=0` to force
    disable. Slow — ~2-5s per page at 200 dpi.

    `max_pages` caps the OCR effort. Default reads from `BART_PDF_OCR_MAX_PAGES`
    (defaults to 40 — handles typical multi-page scans without runaway cost).
    """
    if os.environ.get("BART_PDF_OCR") == "0":
        return ""
    if not _ocr_stack_available():
        return ""
    try:
        from pdf2image import convert_from_path  # type: ignore
        import pytesseract  # type: ignore
    except ImportError:
        return ""
    if max_pages is None:
        try:
            max_pages = int(os.environ.get("BART_PDF_OCR_MAX_PAGES", "40"))
        except ValueError:
            max_pages = 40
    try:
        # 200 dpi is the sweet spot — high enough for tesseract accuracy,
        # low enough to keep memory + time reasonable for full textbooks.
        images = convert_from_path(str(path), dpi=200, last_page=max_pages)
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    for img in images:
        try:
            parts.append(pytesseract.image_to_string(img) or "")
        except Exception:  # noqa: BLE001
            parts.append("")
    return "\n\n".join(parts).strip()


def _is_thin(text: str, path: Path, *, chars_per_page: int = 100) -> bool:
    """A successful pypdf parse can still be 'thin' — most pages were
    images and only a small header extracted. We auto-OCR these when
    chars/page is below `chars_per_page`."""
    try:
        from pypdf import PdfReader
        n = len(PdfReader(str(path)).pages) or 1
    except Exception:  # noqa: BLE001
        return False
    return len(text) / n < chars_per_page


def extract_pdf(path: Path) -> str:
    """Public extractor. Returns text on success, empty on total failure.

    Pipeline:
      1. pypdf — fast, handles most well-typeset PDFs.
      2. pdfplumber — fallback for broken font maps.
      3. tesseract OCR — auto-triggers when (a) the prior stages returned
         nothing or gibberish, OR (b) the prior stage returned 'thin' text
         (chars/page below threshold), e.g. a scan with one cover page of
         embedded text.

    The caller should treat empty-string as a skip-with-explanation; this
    module's `diagnose_pdf()` companion provides the explanation.
    """
    text = _try_pypdf(path)
    pypdf_ok = bool(text) and not _is_gibberish(text)

    fallback = ""
    if not pypdf_ok:
        fallback = _try_pdfplumber(path)
        if fallback and not _is_gibberish(fallback):
            text = fallback
            pypdf_ok = True

    # Auto-OCR when (a) extraction failed entirely OR (b) it succeeded but
    # the result is too thin for chars-per-page to plausibly be real prose.
    needs_ocr = (not pypdf_ok) or (text and _is_thin(text, path))
    if needs_ocr:
        ocr = _try_ocr(path)
        # OCR is the last-resort path; accept short outputs (a single image
        # PDF may legitimately produce 20-30 chars). Only reject OCR when
        # the *ratio* of texty chars is too low — never the length floor.
        ocr_ok = bool(ocr.strip()) and not _is_gibberish(ocr, min_chars=0)
        if ocr_ok:
            return ocr

    if pypdf_ok:
        return text

    # Best of a bad lot — return whichever attempt had the most letters.
    candidates = [c for c in (text, fallback) if c]
    if not candidates:
        return ""
    return max(candidates, key=lambda c: sum(1 for ch in c if ch.isalpha()))


def diagnose_pdf(path: Path) -> str:
    """Inspect a PDF and report why extraction failed (if it did).

    Returns a one-line human-readable explanation, intended for the
    skip-reason field on `ExtractedFile`. Cheap — no OCR.

    Distinguishes:
      - encrypted PDFs (password protected)
      - image-only / scanned PDFs (no embedded text — OCR is the fix)
      - broken-font-map PDFs (text exists but renders as PUA/replacement glyphs)
      - sparse PDFs (a tiny header extracts but most pages are images)
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return "pypdf not installed"
    try:
        reader = PdfReader(str(path))
    except Exception as e:  # noqa: BLE001
        return f"unreadable PDF ({type(e).__name__})"
    if getattr(reader, "is_encrypted", False):
        return "encrypted PDF (password protected)"
    n_pages = len(reader.pages)
    if n_pages == 0:
        return "empty PDF (zero pages)"

    # Sample every page (or up to 12) so a multi-page scan with one
    # text-bearing cover page doesn't masquerade as "extraction worked."
    sample_pages = reader.pages[: min(n_pages, 12)]
    per_page: list[str] = []
    for page in sample_pages:
        try:
            per_page.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            per_page.append("")

    sample_text = "\n".join(per_page)
    nonempty_pages = sum(1 for t in per_page if t.strip())
    image_ratio = (len(sample_pages) - nonempty_pages) / max(len(sample_pages), 1)

    if _ocr_stack_available():
        ocr_hint = "OCR auto-runs (tesseract installed)"
    else:
        ocr_hint = (
            "install OCR for image PDFs: "
            "`pip install pytesseract pdf2image` + `brew install tesseract poppler`"
        )

    if not sample_text.strip():
        return f"image-only / scanned PDF — {n_pages} pages, no extractable text · {ocr_hint}"

    # If the sample has only a few non-empty pages out of many, it's
    # *mostly* image-only with a text cover. Common for handwritten
    # answer keys with a typed header.
    if image_ratio >= 0.7 and n_pages > 3:
        return (
            f"mostly image-only — only {nonempty_pages}/{len(sample_pages)} "
            f"sampled pages had extractable text · {ocr_hint}"
        )

    # If the text we DO have is gibberish, it's a font-map problem and
    # OCR can rescue it.
    if _is_gibberish(sample_text):
        return (
            f"broken font map — text extracts as non-Unicode glyphs · "
            f"{ocr_hint}"
        )
    return "extraction succeeded but downstream filtered the result"
