"""Smoke tests for corpus assembly. Run with: ./run -m pytest tests/."""
from __future__ import annotations

from pathlib import Path

from bart.io.corpus import build_corpus, ExtractedFile


def test_build_corpus_under_budget():
    files = [
        ExtractedFile("a.md", "x" * 1000, 1000),
        ExtractedFile("b.md", "y" * 1000, 1000),
    ]
    c = build_corpus(files, [], char_budget=10_000)
    assert c.total_chars > 0
    assert "FILE: a.md" in c.body
    assert "FILE: b.md" in c.body


def test_build_corpus_truncates_large_file():
    files = [ExtractedFile("big.md", "x" * 1_000_000, 1_000_000)]
    c = build_corpus(files, [], char_budget=50_000)
    assert "truncated" in c.body
    assert c.total_chars <= 60_000


def test_build_corpus_exhausts_budget():
    files = [ExtractedFile(f"f{i}.md", "z" * 50_000, 50_000) for i in range(10)]
    c = build_corpus(files, [], char_budget=100_000)
    assert c.total_chars <= 110_000  # plus minor headers
