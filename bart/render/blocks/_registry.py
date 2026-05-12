"""The bart-block fence-name → renderer registry.

Maps kebab-case fence names (``bart-formula-card``) to the snake-case renderer
functions in the family modules. ``expand_blocks`` looks renderers up here;
``blocks_validate`` checks fence names against ``_BLOCK_REGISTRY`` /
``BLOCK_NAMES``; ``block_schemas`` carries one schema per name in this dict.

Internal to ``bart.render.blocks``. (Its own module so ``expand`` / ``catalog``
can import the registry without an ``__init__`` import cycle.)
"""
from __future__ import annotations

from typing import Callable

from .primitives import *  # noqa: F401,F403  — the renderer fns referenced below
from .visuals import *  # noqa: F401,F403
from .interactives import *  # noqa: F401,F403
from .chem import *  # noqa: F401,F403
from .packs import *  # noqa: F401,F403


# Map of fence-name -> renderer function.
# Kebab-case fence names map to snake_case Python functions.
_BLOCK_REGISTRY: dict[str, Callable[..., str]] = {
    # Primitives
    "stamp":             stamp,
    "tag":               tag,
    "paper-rule":        paper_rule,
    "marginalia":        marginalia,
    "fig-caption":       fig_caption,
    # Visuals (originals)
    "formula-card":      formula_card,
    "worked-example":    worked_example,
    "mnemonic-card":     mnemonic_card,
    "trap-callout":      trap_callout,
    "why-it-matters":    why_it_matters,
    "flowchart":         flowchart,
    "concept-build":     concept_build,
    # Visuals (extras)
    "timeline":          timeline,
    "concept-map":       concept_map,
    "comparison-matrix": comparison_matrix,
    "process-ribbon":    process_ribbon,
    "anatomy-diagram":   anatomy_diagram,
    "number-line":       number_line,
    "proof-ladder":      proof_ladder,
    "annotated-quote":   annotated_quote,
    # Interactives (originals)
    "quick-check":       quick_check,
    "multi-step":        multi_step_problem,
    "multiple-choice":   multiple_choice,
    "hotspots":          hotspots,
    "checkpoint":        checkpoint,
    # Interactives (extras)
    "drag-order":        drag_order,
    "fill-in-blank":     fill_in_blank,
    "match-pairs":       match_pairs,
    "parameter-slider":  parameter_slider,
    "build-equation":    build_equation,
    "estimate-range":    estimate_range,
    "confidence-poll":   confidence_poll,
    # Chem visuals
    "molecule-diagram":  molecule_diagram,
    "reaction-equation": reaction_equation,
    "arrow-pushing":     arrow_pushing,
    "energy-diagram":    energy_diagram,
    "orbital-diagram":   orbital_diagram,
    "ph-scale":          ph_scale,
    "periodic-snippet":  periodic_snippet,
    # Chem interactives
    "balance-equation":  balance_equation,
    "isomer-spotter":    isomer_spotter,
    "titration-curve":   titration_curve,
    "electron-config":   electron_config,
    # ML pack
    "confusion-matrix":  confusion_matrix,
    "loss-curve":        loss_curve,
    "neural-net-diagram": neural_net_diagram,
    "attention-matrix":  attention_matrix,
    "embedding-scatter": embedding_scatter,
    # CS pack
    "code-block":        code_block,
    "call-stack":        call_stack,
    "memory-layout":     memory_layout,
    "binary-tree":       binary_tree,
    "process-timeline":  process_timeline,
    # Phil pack
    "argument-map":      argument_map,
    "truth-table":       truth_table,
    "venn-logic":        venn_logic,
    "dialectic-tree":    dialectic_tree,
    "quote-pull":        quote_pull,
    # Math pack
    "proof-block":       proof_block,
    "matrix-view":       matrix_view,
    "graph-plot":        graph_plot,
    "integral-area":     integral_area,
    # Lit pack
    "passage-annotated": passage_annotated,
    "character-graph":   character_graph,
    "theme-weave":       theme_weave,
    "style-spectrum":    style_spectrum,
}


# Public alias-set; agents reference these names in fences.
BLOCK_NAMES = sorted(_BLOCK_REGISTRY.keys())
