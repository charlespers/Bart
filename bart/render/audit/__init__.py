"""Post-render formatting audit + auto-repair (split package).

Powers ``./run format``. Walks every HTML file under ``<run_dir>/`` and runs a
battery of structural / typographic / accessibility checks; with ``--fix``,
applies idempotent string repairs and — if it detects a streaming-corruption
fingerprint — re-renders the packet from the sibling markdown sources.

The audit is read-only by default. Autofixes are restricted to transformations
that cannot make a working page worse. A clean run yields zero non-info issues
across every page in the packet.

Public surface (also re-exported by the back-compat ``bart.render.format_audit``
shim):

  * ``audit(run_dir, *, apply_fixes=False) -> AuditResult`` — the driver.
  * ``render_report(console, result, run_dir)`` — print + write format_audit.json.
  * ``AuditIssue`` / ``AuditResult`` — the data model.
  * the ``_fix_*`` / ``_ensure_katex_loaded`` functions — ``render/packet.py``
    folds these into the render pipeline so rerendered HTML is already clean.

Implementation, in three focused modules:
  * ``_shared`` — the data model + the "protected region" helpers + the
    regexes shared between a check and its paired fix.
  * ``checks`` — the read-only ``_check_*`` detections + the ``_CHECKS`` registry.
  * ``fixes``  — the idempotent ``_fix_*`` autofixes + the ordered ``_FIXES`` registry.
  * ``rebuild`` — ``audit()`` + the streaming-corruption detect-and-rebuild + ``render_report``.
"""
from __future__ import annotations

from ._shared import AuditIssue, AuditResult
from .rebuild import AUTOFIX_WARN_THRESHOLD, audit, render_report
from .fixes import (
    _fix_math_html_leak,
    _fix_double_escaped_entities,
    _fix_json_unicode_escape,
    _fix_double_escaped_math,
    _fix_unbalanced_block_math,
    _fix_inline_font_overrides,
    _fix_displaymath_inside_p,
    _fix_prose_math_wrap,
    _fix_double_escaped_latex_commands,
    _fix_raw_latex_leak,
    _fix_double_superscript,
    _fix_dollar_math,
    _fix_empty_math_span,
    _fix_html_entity_in_math,
    _fix_mismatched_math_delim,
    _fix_lazy_load_images,
    _ensure_katex_loaded,
)


__all__ = [
    "audit", "render_report", "AuditIssue", "AuditResult", "AUTOFIX_WARN_THRESHOLD",
    "_fix_math_html_leak",
    "_fix_double_escaped_entities",
    "_fix_json_unicode_escape",
    "_fix_double_escaped_math",
    "_fix_unbalanced_block_math",
    "_fix_inline_font_overrides",
    "_fix_displaymath_inside_p",
    "_fix_prose_math_wrap",
    "_fix_double_escaped_latex_commands",
    "_fix_raw_latex_leak",
    "_fix_double_superscript",
    "_fix_dollar_math",
    "_fix_empty_math_span",
    "_fix_html_entity_in_math",
    "_fix_mismatched_math_delim",
    "_fix_lazy_load_images",
    "_ensure_katex_loaded",
]
