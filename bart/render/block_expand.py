"""Back-compat shim — the real code lives in ``bart.render.blocks/``.

Block expansion (``expand_blocks``), the lenient JSON parse + fence iteration
(``_loads_block_json`` / ``iter_block_fences``), the fence-name → renderer
registry (``_BLOCK_REGISTRY`` / ``BLOCK_NAMES``), and the agent-facing block
catalog (``render_block_catalog`` / ``catalog_for`` / ``is_chem_subject``) all
moved into the ``bart.render.blocks`` package (``_registry`` / ``expand`` /
``catalog`` + the per-family renderer modules). This re-exports the surface
everything historically imported from ``block_expand`` so nothing else changes.

New code should import from ``bart.render.blocks`` directly.
"""
from __future__ import annotations

from .blocks import *  # noqa: F401,F403  — public surface (see blocks.__all__)
# `import *` skips leading-underscore names — re-export those explicitly.
from .blocks import (  # noqa: F401
    _BLOCK_REGISTRY,
    _loads_block_json,
    _strip_trailing_commas,
    _inline_warning,
    _placeholder_for,
    _FENCE_RE,
)
