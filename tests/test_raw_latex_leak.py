"""Tests for the raw-LaTeX-leak fix.

Reproduces the symptoms from the system-property-checklist screenshot:
- a `<td>` containing `y(t) = \\int_{-\\infty}^{t} x(\\tau)d\\tau`
- callout text containing `a\\,x_1+b\\,x_2`
- callout text containing standalone `\\tau`
"""
from __future__ import annotations

from bart.render.format_audit import _fix_raw_latex_leak, _check_raw_latex_leak


def test_table_cell_integral_gets_wrapped():
    html = (
        "<html><body><table><tr>"
        "<td>y(t) = \\int_{-\\infty}^{t} x(\\tau)d\\tau</td>"
        "</tr></table></body></html>"
    )
    fixed, n = _fix_raw_latex_leak(html)
    assert n >= 1
    assert "\\(" in fixed and "\\)" in fixed
    assert "\\int" in fixed and "\\tau" in fixed
    # The wrapped run should at minimum contain the integral.
    import re
    spans = re.findall(r"\\\((.+?)\\\)", fixed)
    assert any("\\int" in s and "\\tau" in s for s in spans), (
        f"expected one math span containing the full integral; got spans: {spans}"
    )


def test_table_cell_with_tau_gets_wrapped():
    html = "<td>y(t) = x(\\tau)</td>"
    fixed, n = _fix_raw_latex_leak(html)
    assert n == 1
    assert "\\(y(t) = x(\\tau)\\)" in fixed


def test_inline_run_in_callout_gets_wrapped():
    html = (
        "<p>Linearity is about how the system responds to a\\,x_1+b\\,x_2, "
        "not about whether it preserves shape.</p>"
    )
    fixed, n = _fix_raw_latex_leak(html)
    assert n >= 1
    # The inline run should be wrapped, prose should remain unwrapped.
    assert "\\(a" in fixed or "\\(\\,x" in fixed or "x_1" in fixed
    assert "Linearity is about" in fixed  # prose untouched
    assert "preserves shape." in fixed     # prose untouched


def test_standalone_tau_in_prose_gets_wrapped():
    html = (
        "<p>differentiating with respect to \\tau when you meant t is the "
        "most common Part III arithmetic loss.</p>"
    )
    fixed, n = _fix_raw_latex_leak(html)
    assert n >= 1
    assert "\\(\\tau\\)" in fixed
    assert "most common Part III" in fixed


def test_already_wrapped_math_is_not_double_wrapped():
    html = "<p>The integral \\(\\int_0^1 x \\, dx\\) computes the area.</p>"
    fixed, n = _fix_raw_latex_leak(html)
    assert n == 0
    assert "\\(\\int_0^1 x \\, dx\\)" in fixed
    assert "\\(\\(" not in fixed


def test_attribute_value_with_tex_command_is_skipped():
    html = '<p data-x="\\tau">no tex commands here</p>'
    fixed, n = _fix_raw_latex_leak(html)
    assert n == 0
    assert 'data-x="\\tau"' in fixed


def test_check_flags_raw_leak():
    html = "<td>\\int x(\\tau)d\\tau</td>"
    issues = _check_raw_latex_leak("test.html", html)
    assert len(issues) == 1
    assert issues[0].kind == "raw_latex_leak"


def test_check_silent_when_only_inside_math_spans():
    html = "<p>The math is \\(\\int x(\\tau)d\\tau\\) right here.</p>"
    issues = _check_raw_latex_leak("test.html", html)
    assert issues == []


def test_legitimate_prose_with_no_tex_commands_untouched():
    html = "<p>This is just normal prose with backslash \\n in code.</p>"
    fixed, n = _fix_raw_latex_leak(html)
    assert n == 0
    assert fixed == html


def test_tex_spacing_command_comma_is_not_stripped():
    """Regression: trim used to drop the `,` from `\\,`, leaving content `a\\`
    which closes the math span prematurely as `\\(a\\\\)`."""
    # Simulates input AFTER prose_math_wrap already partially wrapped x_1/x_2:
    # `a\,\(x_1\)+b\,\(x_2\)` — the loose `\,` runs need clean wrapping.
    html = "<p>responds to a\\,\\(x_1\\)+b\\,\\(x_2\\), not about</p>"
    fixed, n = _fix_raw_latex_leak(html)
    # Must NOT produce the malformed `\(a\\)` shape.
    assert "\\(a\\\\)" not in fixed, f"trim broke spacing command: {fixed!r}"
    # `\,` must still be inside a math span.
    assert "\\(a\\,\\)" in fixed or "\\(a\\,x" in fixed
