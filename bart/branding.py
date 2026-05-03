"""Brand assets — ASCII art, color tokens, splash printer.

Single source of truth for the bart visual identity in the terminal.
The design (Loaf mascot + 'bart.' wordmark + cream/terracotta palette) is
mirrored in:
  - assets/bart-loaf.svg   (vector mascot for README + favicons)
  - assets/bart-wordmark.svg
  - assets/bart-loaf.txt   (canonical ASCII art)
  - this module            (terminal renderings)
"""
from __future__ import annotations

from rich.console import Console
from rich.text import Text

# ───────── Color tokens (mirror :root vars from the design's final.html)
CREAM = "#f4f1ea"
CREAM_HI = "#fbf9f4"
CREAM_LO = "#d9d3c4"
INK = "#221f1b"
INK_SOFT = "#3a342d"
ACCENT = "#c96442"        # terracotta
ACCENT_HI = "#e88a6a"
ACCENT_LO = "#9a4628"
TERM_BG = "#1a1815"
TERM_FG = "#e6e1d6"

# Rich color names mapped from the brand palette (so we degrade gracefully on
# 8-color terminals).
RICH_ACCENT = ACCENT_HI         # terracotta highlight, used for status prefixes
RICH_INK = "white"
RICH_DIM = "grey50"
RICH_OK = "#7fb069"             # subdued sage green for ok markers


LOAF_ASCII = r"""              .
             (")
        .-----------.
       /             \
      |   o       o   |
       \      ‿      /
        '-----------'
        (___)   (___)"""


def wordmark_text(version: str | None = None) -> Text:
    """Renders 'bart.' with a terracotta period — Rich Text version."""
    t = Text()
    t.append("bart", style=f"bold {INK}")
    t.append(".", style=f"bold {ACCENT}")
    if version:
        t.append("  ")
        t.append("· study-packet harness", style=RICH_DIM)
        t.append(f"  {version}", style=RICH_DIM)
    return t


def render_splash(console: Console, version: str = "v1.0.0") -> None:
    """Prints the bart splash — ASCII loaf, wordmark, and intro line.

    Mirrors the design's `TerminalSplash` component. Used at the top of
    `./run` invocations and the setup wizard.
    """
    # ASCII art in terracotta accent
    ascii_text = Text(LOAF_ASCII, style=ACCENT_HI)
    console.print(ascii_text)

    # Wordmark + version line
    console.print(wordmark_text(version))

    # Intro
    console.print("hello — let's get you ready for the exam.", style=RICH_DIM)
    console.print()


def status_prefix() -> Text:
    """Terracotta arrow used for status lines: '→ doing thing …'"""
    t = Text("→ ", style=ACCENT_HI)
    return t
