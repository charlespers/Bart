"""Tests for lenient bart-block input shapes.

drag_order and build_equation used to crash when authors passed `items` as
a list of strings (instead of dicts) or supplied unknown kwargs like
`explanation`. Both should now render without errors.
"""
from __future__ import annotations

from bart.render.lib_blocks import drag_order, build_equation


def test_drag_order_accepts_list_of_strings():
    html = drag_order(
        prompt="Order the steps",
        items=["First", "Second", "Third"],
    )
    assert "First" in html
    assert "Second" in html
    assert "Third" in html
    # data-correct should default to ["0","1","2"] (the supplied order)
    assert '"0"' in html and '"1"' in html and '"2"' in html


def test_drag_order_swallows_unknown_kwargs():
    html = drag_order(
        prompt="Order",
        items=["a", "b"],
        explanation="Some explanation here.",
        label="Drill",
    )
    assert "<div class=\"b-drag-order\"" in html
    # Author's `explanation`/`label` are silently ignored (not rendered).
    assert "Some explanation here." not in html


def test_drag_order_accepts_explicit_correct_order():
    html = drag_order(
        prompt="Order",
        items=[{"id": "x", "label": "X"}, {"id": "y", "label": "Y"}],
        correct_order=["y", "x"],
    )
    assert '"y"' in html and '"x"' in html


def test_build_equation_accepts_list_of_strings():
    html = build_equation(
        prompt="Build",
        tokens=["x", "+", "1"],
    )
    assert "<button class=\"b-chip\"" in html
    assert ">x<" in html and ">1<" in html


def test_build_equation_accepts_answer_alias():
    html = build_equation(
        prompt="Build",
        tokens=[{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        answer=["b", "a"],
    )
    # `answer` should be treated as `correct` and reflected in data-correct.
    assert '"b"' in html and '"a"' in html


def test_build_equation_swallows_unknown_kwargs():
    html = build_equation(
        prompt="Build",
        tokens=["a", "b"],
        explanation="...",
        label="Drill",
    )
    assert "<div class=\"b-build-eq\"" in html
