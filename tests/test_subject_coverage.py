"""Subject-coverage tests — reference tools + domain block packs.

Confirms the broadened subject support: physics / biology / economics /
statistics reference tools fire on their keywords and render valid HTML,
and the daily-lesson catalog routes those subjects to a domain pack
instead of the generic fallback.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bart.tools import TOOLS
from bart.tools.biology import BiologyReferenceTool
from bart.tools.economics import EconomicsReferenceTool
from bart.tools.physics import PhysicsReferenceTool
from bart.tools.statistics import StatisticsReferenceTool
from bart.render.blocks.catalog import _domain_kind, catalog_for


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


# ── Reference tools registered ─────────────────────────────────────────

def test_new_reference_tools_registered():
    names = {t.name for t in TOOLS}
    for n in ("physics-reference", "biology-reference",
              "economics-reference", "statistics-reference"):
        assert n in names


# ── Reference tools fire on subject keywords ───────────────────────────

@pytest.mark.parametrize("tool,subject,domain", [
    (PhysicsReferenceTool(),   "Classical Mechanics",        "physics"),
    (BiologyReferenceTool(),   "Molecular Biology",          "biology"),
    (EconomicsReferenceTool(), "Intro to Macroeconomics",    "economics"),
    (StatisticsReferenceTool(),"Probability and Statistics", "statistics"),
])
def test_reference_tool_fires_and_renders(tool, subject, domain):
    d = _tmp()
    res = tool.run(d, {"config": {"subject": subject}})
    assert res.success, res.detail
    assert len(res.output_paths) == 1
    out = res.output_paths[0]
    assert out.exists()
    html = out.read_text()
    # Self-contained, styled, math-capable page.
    assert "<!doctype html>" in html.lower()
    assert "packet.css" in html
    assert "katex" in html.lower()
    assert out.parent.name == "domain"
    assert out.name == f"{domain}_reference.html"


@pytest.mark.parametrize("tool,subject", [
    (PhysicsReferenceTool(),   "Renaissance Art History"),
    (BiologyReferenceTool(),   "Linear Algebra"),
    (EconomicsReferenceTool(), "Organic Chemistry"),
    (StatisticsReferenceTool(),"Shakespeare's Tragedies"),
])
def test_reference_tool_skips_unrelated_subject(tool, subject):
    res = tool.run(_tmp(), {"config": {"subject": subject}})
    assert not res.success
    assert "did not match" in res.detail


# ── Catalog routes new subjects to a domain pack ───────────────────────

@pytest.mark.parametrize("subject,domain", [
    ("Intro Physics",          "physics"),
    ("AP Physics C",           "physics"),
    ("Cell Biology",           "bio"),
    ("Genetics",               "bio"),
    ("Microeconomics",         "econ"),
    ("Corporate Finance",      "econ"),
    ("Organic Chemistry",      "chem"),
    ("Real Analysis",          "math"),
    ("Underwater Basketweaving", None),
])
def test_domain_kind_routing(subject, domain):
    assert _domain_kind(subject) == domain


def test_domain_catalogs_are_non_empty_and_distinct():
    physics = catalog_for("daily_lesson", "Physics 101")
    bio = catalog_for("daily_lesson", "Genetics")
    econ = catalog_for("daily_lesson", "Finance")
    generic = catalog_for("daily_lesson", "Underwater Basketweaving")
    for c in (physics, bio, econ, generic):
        assert len(c) > 100
    # Domain packs differ from the generic fallback.
    assert physics != generic
    assert bio != generic
    assert econ != generic
