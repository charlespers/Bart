"""Block expansion — turn ```bart-<name> JSON fences into design-library HTML.

The bridge between agent output (markdown) and the design system. The Author
emits blocks like::

    ```bart-formula-card
    {"tex": "E = mc^2", "title": "Mass-energy", "legend": [...]}
    ```

``expand_blocks`` finds those fences, decodes the JSON payload (leniently —
trailing commas tolerated), calls the matching renderer from ``_registry``,
and substitutes the rendered HTML in place of the fence — *before* markdown
rendering (wrapped with blank lines + a sentinel comment so python-markdown
leaves it alone). A malformed block leaves a visible inline warning rather
than failing the build. ``iter_block_fences`` / ``_loads_block_json`` are
shared with ``blocks_validate`` so what gets validated is what gets rendered.

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ._registry import _BLOCK_REGISTRY, BLOCK_NAMES  # noqa: F401  — BLOCK_NAMES re-exported


@dataclass
class ExpansionWarning:
    kind: str           # 'unknown_block' | 'bad_json' | 'render_error'
    detail: str
    block_name: str = ""


@dataclass
class ExpansionResult:
    text: str                           # markdown with fences replaced by HTML
    counts: dict[str, int]              # how many times each block was used
    warnings: list[ExpansionWarning]


_FENCE_RE = re.compile(
    r"(?ms)^```bart-([a-z0-9-]+)[^\n]*\n(.*?)```\s*$"
)


def _strip_trailing_commas(s: str) -> str:
    out: list[str] = []
    in_str = False
    escaped = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            # Look ahead past whitespace: if the next non-space char closes
            # an object/array, this comma is trailing — drop it.
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                # Skip the comma (and keep the intervening whitespace so
                # line/col reporting on a *subsequent* error stays sane).
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_block_json(body: str) -> Any:
    """Parse a block body's JSON, tolerating trailing commas.

    Raises `json.JSONDecodeError` (from the *original* attempt — most
    informative) if even the lenient parse fails.
    """
    try:
        return json.loads(body)
    except json.JSONDecodeError as first_err:
        repaired = _strip_trailing_commas(body)
        if repaired != body:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise first_err


def iter_block_fences(markdown_text: str):
    """Yield ``(index, name, body)`` for every ``bart-<name>`` fence, in order.

    ``index`` is the 0-based positional index of the fence; ``body`` is the
    stripped JSON text (may be empty for zero-arg blocks). Shared by
    ``expand_blocks`` (implicitly, via ``_FENCE_RE``) and
    ``blocks_validate.validate_blocks`` so they enumerate identically.
    """
    for i, m in enumerate(_FENCE_RE.finditer(markdown_text)):
        yield i, m.group(1), m.group(2).strip()


def expand_blocks(markdown_text: str) -> ExpansionResult:
    """Scan `markdown_text` for `bart-*` fences and replace them with HTML.

    The replacement HTML is wrapped with blank lines and a sentinel comment
    so python-markdown doesn't try to "tidy" it. Inline math inside the
    rendered HTML still goes through KaTeX at view time.
    """
    counts: dict[str, int] = {}
    warnings: list[ExpansionWarning] = []

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        body = m.group(2).strip()
        fn = _BLOCK_REGISTRY.get(name)
        if fn is None:
            warnings.append(ExpansionWarning(
                "unknown_block", f"no registered block named 'bart-{name}'", name,
            ))
            return _inline_warning(name, "unknown block")

        # Allow zero-arg blocks (e.g. paper-rule with empty body).
        if not body:
            payload: dict[str, Any] = {}
        else:
            try:
                payload = _loads_block_json(body)
            except json.JSONDecodeError as e:
                warnings.append(ExpansionWarning(
                    "bad_json", f"bart-{name}: {e.msg} at line {e.lineno}", name,
                ))
                return _inline_warning(name, f"JSON parse error: {e.msg}")

        if not isinstance(payload, dict):
            warnings.append(ExpansionWarning(
                "bad_json", f"bart-{name}: payload must be a JSON object", name,
            ))
            return _inline_warning(name, "payload must be a JSON object")

        try:
            html = fn(**payload)
        except TypeError as e:
            # The author's JSON either has unknown kwargs OR is missing
            # required ones. We recover from both:
            #   1. Drop unknown kwargs and retry with only accepted keys.
            #   2. Synthesize placeholder defaults for missing required
            #      keyword-only args (empty string for str, [] for list,
            #      {} for dict, 0 for numeric). The block renders with a
            #      "missing field" stamp instead of a blocking yellow stub
            #      that hides the entire question.
            import inspect as _inspect
            try:
                sig = _inspect.signature(fn)
                params = sig.parameters
                accepts_var_kw = any(
                    p.kind is _inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
                err_str = str(e)
                if not accepts_var_kw and "unexpected keyword argument" in err_str:
                    accepted = {k: v for k, v in payload.items() if k in params}
                    dropped = sorted(set(payload) - set(accepted))
                    # After dropping unknowns, the call may still fail because
                    # required kwargs are missing. Fill those with placeholders
                    # so the block renders rather than collapsing to a stub.
                    missing: list[str] = []
                    for pname, p in params.items():
                        if pname in accepted:
                            continue
                        if p.kind is _inspect.Parameter.KEYWORD_ONLY \
                                and p.default is _inspect.Parameter.empty:
                            missing.append(pname)
                            accepted[pname] = _placeholder_for(pname, p.annotation)
                    html = fn(**accepted)
                    if dropped:
                        warnings.append(ExpansionWarning(
                            "unknown_kwargs",
                            f"bart-{name}: dropped unknown kwargs {dropped} "
                            f"(rendered with the rest)",
                            name,
                        ))
                    if missing:
                        html = (
                            html
                            + f'<div class="b-block-stub-note" style="margin:'
                              f'8px 0 0;padding:6px 10px;font-size:11px;'
                              f'color:#b48a3c;background:#fff3cd;'
                              f'border-radius:4px;font-family:monospace">'
                              f'⚠ bart-{name}: filled missing field(s) '
                              f'{missing} with placeholder defaults — edit '
                              f'the source markdown to provide real values.'
                              f'</div>'
                        )
                        warnings.append(ExpansionWarning(
                            "missing_required_kwargs",
                            f"bart-{name}: filled missing required kwargs "
                            f"{missing} with defaults",
                            name,
                        ))
                elif "missing" in err_str and "required keyword-only argument" in err_str:
                    # Determine which required kwargs the author skipped, then
                    # supply a typed default so `fn()` succeeds. Render the
                    # block with a small caveat note appended.
                    missing: list[str] = []
                    for pname, p in params.items():
                        if pname in payload:
                            continue
                        if p.kind is _inspect.Parameter.KEYWORD_ONLY and p.default is _inspect.Parameter.empty:
                            missing.append(pname)
                            payload[pname] = _placeholder_for(pname, p.annotation)
                    html = fn(**payload)
                    if missing:
                        html = (
                            html
                            + f'<div class="b-block-stub-note" style="margin:'
                              f'8px 0 0;padding:6px 10px;font-size:11px;'
                              f'color:#b48a3c;background:#fff3cd;'
                              f'border-radius:4px;font-family:monospace">'
                              f'⚠ bart-{name}: filled missing field(s) '
                              f'{missing} with placeholder defaults — edit '
                              f'the source markdown to provide real values.'
                              f'</div>'
                        )
                        warnings.append(ExpansionWarning(
                            "missing_required_kwargs",
                            f"bart-{name}: filled missing required kwargs "
                            f"{missing} with defaults",
                            name,
                        ))
                else:
                    raise
            except Exception as e2:  # noqa: BLE001
                # Recovery itself failed — report the SECONDARY error so the
                # author sees the real shape problem (e.g. items being a list
                # of strings when the renderer expected list-of-dicts), not
                # the misleading original 'unexpected keyword argument' echo.
                warnings.append(ExpansionWarning(
                    "render_error",
                    f"bart-{name}: recovery failed: {type(e2).__name__}: {e2}",
                    name,
                ))
                return _inline_warning(name, f"{type(e2).__name__}: {e2}")
        except Exception as e:  # noqa: BLE001
            warnings.append(ExpansionWarning(
                "render_error", f"bart-{name}: {type(e).__name__}: {e}", name,
            ))
            return _inline_warning(name, f"{type(e).__name__}: {e}")

        counts[name] = counts.get(name, 0) + 1
        # Wrap so python-markdown treats it as a raw HTML block. The trailing
        # blank lines are critical — without them surrounding paragraphs can
        # absorb the HTML.
        return f"\n\n<!-- bart-{name} -->\n{html}\n<!-- /bart-{name} -->\n\n"

    new_text = _FENCE_RE.sub(_sub, markdown_text)
    return ExpansionResult(text=new_text, counts=counts, warnings=warnings)


def _inline_warning(name: str, detail: str) -> str:
    """Render a visible warning in place of a failed block. Avoids silent loss."""
    from html import escape
    return (
        f'\n\n<div class="bart-block-error" style="background:#fff3cd;'
        f'border:1px dashed #b48a3c;color:#6e4f10;padding:8px 12px;'
        f'border-radius:6px;font-family:monospace;font-size:13px;margin:12px 0">'
        f'<strong>bart-{escape(name)}</strong> failed: {escape(detail)}</div>\n\n'
    )


def _placeholder_for(field_name: str, annotation: Any) -> Any:
    """Synthesize a typed placeholder for a missing required kwarg.

    Inspects the parameter's type annotation when available; falls back to
    name heuristics (anything ending in `s` or with `list` in the name
    becomes `[]`; otherwise empty string). The placeholder lets the block
    render so the rest of the page is intact; an inline `⚠` note tells the
    reader (and the author) that a real value is needed.
    """
    import typing as _typing
    name_lc = field_name.lower()
    if annotation is not None and annotation is not type(None):
        origin = getattr(annotation, "__origin__", None)
        if origin in (list, _typing.List):
            return []
        if origin in (dict, _typing.Dict):
            return {}
        if annotation is int or annotation is float:
            return 0
        if annotation is bool:
            return False
    if name_lc.endswith("s") or "list" in name_lc or "items" in name_lc \
            or "choices" in name_lc or "steps" in name_lc \
            or "hints" in name_lc or "rows" in name_lc:
        return []
    if "count" in name_lc or "num" in name_lc or "size" in name_lc:
        return 0
    fallback = {
        "solution": "(solution to be provided)",
        "answer": "(answer to be provided)",
        "question": "(question text)",
        "problem": "(problem statement)",
        "tex": "?",
        "title": "",
        "body": "",
    }
    return fallback.get(name_lc, "(missing)")

