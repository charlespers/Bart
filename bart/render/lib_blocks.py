"""Back-compat shim — the real code lives in ``bart.render.blocks/``.

``lib_blocks.py`` used to be a single ~90 KB module of bart-* renderer
functions; it's now split by family across the ``bart.render.blocks`` package
(``primitives`` / ``visuals`` / ``interactives`` / ``chem``, with ``_shared``
for the common ``_attr`` / ``_inline_md`` / ``_esc_math_inner`` / … helpers).
This re-exports every renderer fn (and the shared helpers other modules reach
into) so ``from .lib_blocks import …`` keeps working.

New code should import from ``bart.render.blocks`` (or the family modules) directly.
"""
from __future__ import annotations

from .blocks import *  # noqa: F401,F403  — every renderer fn (see blocks.__all__)
# Shared helpers historically importable from `lib_blocks` (used by
# `lib_blocks_packs` and a couple of tests); `import *` skips underscore names.
from .blocks._shared import (  # noqa: F401
    _attr, _esc_math_inner, _inline_math, _block_math, _inline_md, _block_md,
)
