"""Cover-page (HeroBlock) tests — the bespoke, high-end landing experience.

The hero is the first thing a student sees; it should read as a document
made for them and this exam, not a generic export. These tests pin the
personalization: exam countdown, the student's own guidance surfaced as a
"Tailored to" line, and graceful handling of missing/sentinel inputs.
"""
from __future__ import annotations

from datetime import date, timedelta

from bart.render.blocks.hero import HeroBlock


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def test_subject_and_eyebrow_render():
    html = HeroBlock("Organic Chemistry II", "2026-05-18", _in(20)).render()
    assert "Organic Chemistry II" in html
    assert "block-hero-eyebrow" in html


def test_countdown_reflects_days_until_exam():
    html = HeroBlock("Physics", "2026-05-18", _in(14)).render()
    assert "block-hero-countdown" in html
    assert "14" in html
    assert "days to go" in html


def test_countdown_singular_for_one_day():
    html = HeroBlock("Physics", "2026-05-18", _in(1)).render()
    assert "day to go" in html
    assert "days to go" not in html


def test_countdown_today():
    html = HeroBlock("Physics", "2026-05-18", _in(0)).render()
    assert "today" in html


def test_no_countdown_for_past_or_unparseable_exam():
    assert "block-hero-countdown" not in HeroBlock("X", "", "2000-01-01").render()
    assert "block-hero-countdown" not in HeroBlock("X", "", "not-a-date").render()


def test_guidance_surfaces_as_tailored_line():
    html = HeroBlock(
        "Linear Algebra", "2026-05-18", _in(10),
        guidance="I keep mixing up eigenvalues and eigenvectors",
    ).render()
    assert "Tailored to" in html
    assert "eigenvalues and eigenvectors" in html


def test_sentinel_guidance_is_not_shown():
    for sentinel in ("", "(no extra guidance)", "none", "N/A"):
        html = HeroBlock("Bio", "2026-05-18", _in(10), guidance=sentinel).render()
        assert "Tailored to" not in html


def test_long_guidance_is_trimmed():
    html = HeroBlock(
        "Bio", "2026-05-18", _in(10), guidance="focus area " * 60,
    ).render()
    assert "…" in html


def test_student_level_is_named_when_known():
    html = HeroBlock("Bio", "2026-05-18", _in(10), student_level="graduate").render()
    assert "graduate level" in html


def test_unknown_level_omitted_cleanly():
    html = HeroBlock("Bio", "2026-05-18", _in(10), student_level="").render()
    # No dangling "at the  level" fragment.
    assert "the  level" not in html
