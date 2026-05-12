"""Back-compat shim — the real code lives in ``bart.render.audit/``.

``format_audit.py`` used to be a single ~94 KB module; it's now the
``bart.render.audit`` package (``_shared`` / ``checks`` / ``fixes`` /
``rebuild``). Everything that previously did ``from .format_audit import …``
keeps working: this re-exports the public surface (``audit``, ``render_report``,
``AuditIssue``, ``AuditResult``) **and** the internal ``_check_*`` / ``_fix_*`` /
``_ensure_katex_loaded`` helpers + the ``_CHECKS`` / ``_FIXES`` registries that
``render/packet.py`` and the test suite reach into.

New code should import from ``bart.render.audit`` (public surface) or
``bart.render.audit.{checks,fixes,rebuild}`` (internals) directly.
"""
from __future__ import annotations

# Public surface.
from .audit import AuditIssue, AuditResult, audit, render_report  # noqa: F401

# Internals that external modules / tests historically imported by name.
# (`import *` skips leading-underscore names, so these are listed explicitly.)
from .audit._shared import _line_of, _strip_protected, _apply_outside_protected  # noqa: F401
from .audit.checks import (  # noqa: F401
    _CHECKS,
    _check_anchors, _check_block_error, _check_broken_attrs,
    _check_deprecated_tags, _check_displaymath_inside_p, _check_doctype_and_lang,
    _check_dollar_math, _check_double_escaped_entities, _check_double_escaped_math,
    _check_empty_math_span, _check_html_entity_in_math, _check_images,
    _check_inline_font_overrides, _check_json_unicode_escape,
    _check_katex_delimiter_config, _check_katex_wired_when_math_present,
    _check_library_block_density, _check_llm_wrapper_fence, _check_markdown_leak,
    _check_math_balance, _check_math_html_leak, _check_mismatched_math_delim,
    _check_mojibake, _check_nbsp_soup, _check_pre_holds_latex,
    _check_raw_fence_leak, _check_raw_latex_leak, _check_raw_md_heading_leak,
    _check_raw_md_hr_leak, _check_render_fallback, _check_stray_dollar,
    _check_title_present, _check_unclosed_code_fence,
)
from .audit.fixes import (  # noqa: F401
    _FIXES,
    _ensure_katex_loaded,
    _fix_displaymath_inside_p, _fix_dollar_math, _fix_double_escaped_entities,
    _fix_double_escaped_latex_commands, _fix_double_escaped_math,
    _fix_double_superscript, _fix_empty_math_span, _fix_html_entity_in_math,
    _fix_inline_font_overrides, _fix_json_unicode_escape, _fix_lazy_load_images,
    _fix_math_html_leak, _fix_mismatched_math_delim, _fix_prose_math_wrap,
    _fix_raw_latex_leak, _fix_unbalanced_block_math,
)
