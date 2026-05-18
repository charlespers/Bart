"""Hero block — the cover of the landing page (index.html).

This is the first thing a student sees. It should feel like a bespoke
document made for *them* and *this exam* — not a generic export. So it
carries a prominent exam countdown, the student's own focus/guidance when
they gave any, and a quietly premium presentation.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from .base import Block, he


# Sentinels the setup wizard writes when the student skips the guidance
# prompt — never surface these as if they were a real instruction.
_EMPTY_GUIDANCE = {"", "(no extra guidance)", "none", "n/a"}

_LEVEL_LABEL = {
    "undergraduate": "undergraduate",
    "graduate": "graduate",
    "aplevel": "AP / high-school",
    "professional": "professional",
}


def _days_until(exam_date: str) -> int | None:
    """Whole days from today to `exam_date` (YYYY-MM-DD). None if unparseable
    or already past."""
    try:
        exam = datetime.strptime(exam_date.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None
    delta = (exam - date.today()).days
    return delta if delta >= 0 else None


class HeroBlock(Block):
    name = "hero"
    css_classes: ClassVar[list[str]] = ["block-hero"]

    def __init__(
        self,
        subject: str,
        generated_at: str,
        exam_date: str,
        days_until: int | None = None,
        rel_root: str = ".",
        guidance: str = "",
        student_level: str = "",
    ):
        self.subject = subject
        self.generated_at = generated_at
        self.exam_date = exam_date
        # Prefer an explicitly-passed countdown; otherwise derive from the date.
        self.days_until = days_until if days_until is not None else _days_until(exam_date)
        self.rel_root = rel_root
        self.guidance = (guidance or "").strip()
        self.student_level = (student_level or "").strip().lower()

    def _countdown_html(self) -> str:
        if self.days_until is None:
            return ""
        if self.days_until == 0:
            big, unit = "today", ""
        elif self.days_until == 1:
            big, unit = "1", " day to go"
        else:
            big, unit = str(self.days_until), " days to go"
        return (
            '<div class="block-hero-countdown">'
            f'<span class="block-hero-countdown-num">{he(big)}</span>'
            f'<span class="block-hero-countdown-unit">{he(unit)}</span>'
            '</div>'
        )

    def _custom_line(self) -> str:
        """A 'tailored to' line — only when the student actually gave guidance.
        This is what makes the packet read as bespoke rather than generic."""
        g = self.guidance
        if not g or g.lower() in _EMPTY_GUIDANCE:
            return ""
        # Keep the cover calm — show a single trimmed line.
        excerpt = g.replace("\n", " · ").strip()
        if len(excerpt) > 160:
            excerpt = excerpt[:157].rstrip() + "…"
        return (
            '<div class="block-hero-custom">'
            '<span class="block-hero-custom-label">Tailored to</span>'
            f'<span class="block-hero-custom-text">{he(excerpt)}</span>'
            '</div>'
        )

    def render(self) -> str:
        level = _LEVEL_LABEL.get(self.student_level, "")
        level_clause = f" at the {level} level" if level else ""
        meta_bits = []
        if self.exam_date:
            meta_bits.append(f"exam {he(self.exam_date)}")
        if self.generated_at:
            meta_bits.append(f"prepared {he(self.generated_at)}")
        meta = '<span class="block-hero-sep">·</span>'.join(
            f'<span>{b}</span>' for b in meta_bits
        )
        return (
            '<section class="block-hero">'
            '<div class="block-hero-loaf-wrap">'
            f'<img class="block-hero-loaf" src="{self.rel_root}/assets/bart-loaf.svg" '
            'alt="bart" width="120" height="109" />'
            '</div>'
            '<div class="block-hero-content">'
            '<div class="block-hero-eyebrow">Your bespoke study packet</div>'
            f'<h1 class="block-hero-title">{he(self.subject)}'
            '<span class="block-hero-dot">.</span></h1>'
            '<p class="block-hero-tagline">Built end-to-end from your own course '
            f'materials{he(level_clause)}. Start with the master plan — then use '
            'this page to move between days.</p>'
            f'{self._custom_line()}'
            f'{self._countdown_html()}'
            f'<div class="block-hero-meta">{meta}</div>'
            '</div>'
            '</section>'
        )
