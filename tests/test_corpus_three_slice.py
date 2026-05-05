"""Three-slice truncation tests for build_corpus.

The middle of long files used to be dropped entirely (head/tail only).
This file verifies the new head + middle-sample + tail strategy keeps
sentinel content from each region.
"""
from __future__ import annotations

from bart.io.corpus import build_corpus, ExtractedFile


def _make(text: str) -> ExtractedFile:
    return ExtractedFile("big.md", text, len(text))


def test_truncation_keeps_head_middle_tail_sentinels():
    head = "HEAD_SENTINEL" + ("a" * 30_000)
    mid = ("b" * 30_000) + "MIDDLE_SENTINEL" + ("b" * 30_000)
    tail = ("c" * 30_000) + "TAIL_SENTINEL"
    body = head + mid + tail

    c = build_corpus([_make(body)], [], char_budget=60_000)

    assert "HEAD_SENTINEL" in c.body, "head slice was dropped"
    assert "MIDDLE_SENTINEL" in c.body, "middle slice was dropped"
    assert "TAIL_SENTINEL" in c.body, "tail slice was dropped"


def test_truncation_marker_format():
    body = "x" * 200_000
    c = build_corpus([_make(body)], [], char_budget=60_000)
    assert c.body.count("…truncated") == 2 or c.body.count("…sample…") >= 1, (
        "expected three-slice markers, got: " + c.body[:500]
    )


def test_truncation_total_within_budget():
    body = "x" * 200_000
    c = build_corpus([_make(body)], [], char_budget=60_000)
    assert c.total_chars <= 62_000


def test_short_file_is_not_truncated():
    body = "ok " * 100
    c = build_corpus([_make(body)], [], char_budget=60_000)
    assert "truncated" not in c.body
    assert "sample" not in c.body
