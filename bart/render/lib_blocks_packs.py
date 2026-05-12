"""Back-compat shim — the real code lives in ``bart.render.blocks/packs.py``.

The ML / CS / philosophy / math / literature "pack" renderers (``confusion-matrix``,
``code-block``, ``argument-map``, ``proof-block``, ``passage-annotated``, …) moved
into ``bart.render.blocks.packs``. This re-exports them so ``from .lib_blocks_packs
import …`` keeps working.

New code should import from ``bart.render.blocks.packs`` (or ``bart.render.blocks``) directly.
"""
from __future__ import annotations

from .blocks.packs import *  # noqa: F401,F403
