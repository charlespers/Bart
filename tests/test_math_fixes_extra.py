"""Tests for the extra TeX-leak fixes added in the comprehensive sweep.

Covers:
  - multi-escape collapse (triple/quadruple-escaped delimiters)
  - dollar-math conversion (`$x$`, `$$x$$`)
  - empty math span removal
  - HTML entities inside math spans
  - mismatched delimiter repair
  - expanded indicator-command list (\\quad, \\boxed, \\dfrac, etc.)
"""
from __future__ import annotations

from bart.render.format_audit import (
    _fix_double_escaped_math,
    _fix_dollar_math,
    _fix_empty_math_span,
    _fix_html_entity_in_math,
    _fix_mismatched_math_delim,
    _fix_raw_latex_leak,
    _check_dollar_math,
    _check_empty_math_span,
    _check_html_entity_in_math,
    _check_mismatched_math_delim,
)


# ── Multi-escape collapse ───────────────────────────────────────────


def test_double_escape_collapse():
    src = "<p>x \\\\(a\\\\) y</p>"  # `\\(a\\)` literal
    out, n = _fix_double_escaped_math(src)
    assert n >= 1
    assert "\\(a\\)" in out
    assert "\\\\(" not in out


def test_triple_escape_collapse():
    src = "<p>x \\\\\\\\(a\\\\\\\\) y</p>"  # `\\\\(a\\\\)` literal
    out, n = _fix_double_escaped_math(src)
    assert n >= 1
    assert "\\(a\\)" in out
    assert "\\\\(" not in out
    assert "\\\\\\\\" not in out


def test_quadruple_escape_collapse():
    src = "<p>" + "\\" * 8 + "(a" + "\\" * 8 + ") z</p>"
    out, n = _fix_double_escaped_math(src)
    assert n >= 1
    assert "\\(a\\)" in out


# ── Dollar-math conversion ──────────────────────────────────────────


def test_inline_dollar_math_with_tex_command():
    src = "<p>For $x \\in \\mathbb{R}$ we have...</p>"
    out, n = _fix_dollar_math(src)
    assert n == 1
    assert "\\(x \\in \\mathbb{R}\\)" in out
    assert "$" not in out


def test_inline_dollar_math_with_subscript():
    src = "<p>The variable $x_1$ is the first.</p>"
    out, n = _fix_dollar_math(src)
    assert n == 1
    assert "\\(x_1\\)" in out


def test_display_dollar_math():
    src = "<p>Equation: $$\\int_0^1 x \\, dx$$ here.</p>"
    out, n = _fix_dollar_math(src)
    assert n == 1
    assert "\\[\\int_0^1 x \\, dx\\]" in out


def test_dollar_currency_is_left_alone():
    src = "<p>Get $5 to $10 worth of value.</p>"
    out, n = _fix_dollar_math(src)
    assert n == 0
    assert out == src


def test_dollar_check_flags_when_present():
    issues = _check_dollar_math("test.html", "<p>$\\tau + 1$</p>")
    assert len(issues) == 1
    assert issues[0].kind == "dollar_math"


# ── Empty math span ─────────────────────────────────────────────────


def test_empty_inline_math_removed():
    src = "<p>x \\(\\) y</p>"
    out, n = _fix_empty_math_span(src)
    assert n == 1
    assert "\\(\\)" not in out


def test_empty_display_math_removed():
    src = "<p>x \\[\\] y</p>"
    out, n = _fix_empty_math_span(src)
    assert n == 1
    assert "\\[\\]" not in out


def test_whitespace_only_math_removed():
    src = "<p>x \\(   \\) y</p>"
    out, n = _fix_empty_math_span(src)
    assert n == 1


def test_empty_math_check_flags():
    issues = _check_empty_math_span("test.html", "<p>\\(\\)</p>")
    assert len(issues) == 1


# ── HTML entities in math ───────────────────────────────────────────


def test_html_escaped_backslash_in_math_decoded():
    src = "<p>The integral \\(&#x5C;tau\\) is the dummy variable.</p>"
    out, n = _fix_html_entity_in_math(src)
    assert n == 1
    assert "\\(\\tau\\)" in out
    assert "&#x5C;" not in out


def test_html_amp_in_math_decoded():
    src = "<p>\\(a &amp; b\\)</p>"
    out, n = _fix_html_entity_in_math(src)
    assert n == 1
    assert "\\(a & b\\)" in out


def test_html_entity_outside_math_untouched():
    src = "<p>This &amp; that.</p>"
    out, n = _fix_html_entity_in_math(src)
    assert n == 0


def test_html_entity_check_flags():
    issues = _check_html_entity_in_math("t.html", "<p>\\(&#x5C;tau\\)</p>")
    assert len(issues) == 1


# ── Mismatched delimiters ───────────────────────────────────────────


def test_mismatched_paren_to_bracket_repaired():
    src = "<p>x \\(a + b\\] y</p>"
    out, n = _fix_mismatched_math_delim(src)
    assert n == 1
    assert "\\(a + b\\)" in out


def test_mismatched_bracket_to_paren_repaired():
    src = "<p>x \\[\\int_0^1 dx\\) y</p>"
    out, n = _fix_mismatched_math_delim(src)
    assert n == 1
    assert "\\[\\int_0^1 dx\\]" in out


def test_mismatched_check_flags():
    issues = _check_mismatched_math_delim("t.html", "<p>\\(a\\]</p>")
    assert len(issues) == 1


# ── Expanded indicator commands ─────────────────────────────────────


def test_quad_command_is_recognized():
    src = "<p>between a \\quad b in math</p>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1
    assert "\\(" in out and "\\quad" in out


def test_boxed_command_is_recognized():
    src = "<td>\\boxed{x = 5}</td>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1
    assert "\\(" in out and "\\boxed" in out


def test_dfrac_command_is_recognized():
    src = "<td>y = \\dfrac{1}{x+1}</td>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1
    assert "\\(" in out and "\\dfrac" in out


def test_long_arrow_command_is_recognized():
    src = "<p>A \\longrightarrow B</p>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1
    assert "\\longrightarrow" in out


def test_chemistry_ce_command_is_recognized():
    src = "<td>\\ce{H2O}</td>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1


def test_text_command_is_recognized():
    src = "<td>\\text{when } x &gt; 0</td>"
    out, n = _fix_raw_latex_leak(src)
    assert n >= 1
    assert "\\text" in out
