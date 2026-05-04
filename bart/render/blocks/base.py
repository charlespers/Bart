"""Block base classes — every block subclasses one of these."""
from __future__ import annotations

from html import escape as html_escape
from typing import ClassVar


class Block:
    """A single composable content unit in a page.

    Subclasses implement `render() -> str` to emit HTML. They can declare
    additional CSS classes they need via `css_classes`, and request JS
    via `needs_js`.
    """
    name: str = "block"
    css_classes: ClassVar[list[str]] = []
    needs_js: ClassVar[bool] = False

    def render(self) -> str:
        raise NotImplementedError


class ProseBlock(Block):
    """A passthrough for already-rendered prose HTML.

    Used to wrap markdown-rendered prose paragraphs that don't fit a
    structured block. The HTML is trusted (it came from our own renderer).
    """
    name = "prose"

    def __init__(self, html: str):
        self.html = html

    def render(self) -> str:
        if not self.html.strip():
            return ""
        return f'<div class="block-prose">{self.html}</div>'


def he(s: str) -> str:
    """Shorthand for html.escape — used by every block."""
    return html_escape(str(s) if s is not None else "")
