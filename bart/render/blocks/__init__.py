"""bart render blocks — both the page-chrome blocks and the bart-* renderers.

Two distinct things share this package:

  * **Structural page blocks** — `HeroBlock`, `DayCardBlock`, `PagerBlock`, … —
    the page-level chrome (`packet.py` composes these around the markdown body).
  * **The 66 ``bart-<name>`` content renderers** — `formula_card`, `quick_check`,
    `molecule_diagram`, … — split by family across `primitives` / `visuals` /
    `interactives` / `chem` / `packs`, with `_shared` for the common helpers.
    `_registry` builds the fence-name → renderer table; `expand` is the
    fence-substitution engine; `catalog` generates the agent-facing block
    catalog from `block_schemas`.

Public surface for the renderers (also re-exported by the back-compat
`bart.render.lib_blocks` / `bart.render.lib_blocks_packs` / `bart.render.block_expand`
shims): every renderer fn, `_BLOCK_REGISTRY`, `BLOCK_NAMES`, `expand_blocks`,
`iter_block_fences`, `_loads_block_json`, `render_block_catalog`, `catalog_for`,
`is_chem_subject`, `ExpansionWarning`, `ExpansionResult`.

Adding a new bart-* renderer: write it in the right family module, register it
in `_registry._BLOCK_REGISTRY`, add a `block_schemas.BLOCK_SCHEMAS` entry.
Adding a new structural block: subclass `Block` in a new file, import it here.
"""
from __future__ import annotations

# ── Structural page blocks ───────────────────────────────────────
from .artifact_card import ArtifactCardBlock, ArtifactGridBlock
from .base import Block, ProseBlock
from .day_card import DayCardBlock, DayGridBlock
from .hero import HeroBlock
from .kpi_grid import KpiGridBlock
from .pager import PagerBlock

# ── bart-* content renderers (split by family) ───────────────────
from .primitives import *  # noqa: F401,F403
from .visuals import *  # noqa: F401,F403
from .interactives import *  # noqa: F401,F403
from .chem import *  # noqa: F401,F403
from .packs import *  # noqa: F401,F403
from ._registry import _BLOCK_REGISTRY, BLOCK_NAMES  # noqa: F401
from .expand import (  # noqa: F401
    ExpansionWarning, ExpansionResult,
    expand_blocks, iter_block_fences,
    _loads_block_json, _strip_trailing_commas,
    _inline_warning, _placeholder_for, _FENCE_RE,
)
from .catalog import (  # noqa: F401
    render_block_catalog, catalog_for, is_chem_subject,
)


# Renderer-function names, for `from .blocks import *` consumers (the shims).
def _renderer_names():
    import inspect
    from . import primitives as _p, visuals as _v, interactives as _i, chem as _c, packs as _k
    names = set()
    for mod in (_p, _v, _i, _c, _k):
        for n, obj in vars(mod).items():
            if not n.startswith("_") and inspect.isfunction(obj) and obj.__module__ == mod.__name__:
                names.add(n)
    return sorted(names)


__all__ = [
    # structural
    "Block", "ProseBlock",
    "HeroBlock", "KpiGridBlock",
    "DayCardBlock", "DayGridBlock",
    "ArtifactCardBlock", "ArtifactGridBlock",
    "PagerBlock",
    # renderer registry + engine + catalog
    "_BLOCK_REGISTRY", "BLOCK_NAMES",
    "expand_blocks", "iter_block_fences",
    "_loads_block_json", "_strip_trailing_commas",
    "_inline_warning", "_placeholder_for", "_FENCE_RE",
    "ExpansionWarning", "ExpansionResult",
    "render_block_catalog", "catalog_for", "is_chem_subject",
] + _renderer_names()
