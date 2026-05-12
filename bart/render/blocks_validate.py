"""Validate every ``bart-<name>`` fence in a markdown artifact at source.

Today the architecture is "let the model emit broken block JSON, repair it
after render". This module is the source-side check: ``validate_blocks`` walks
every ``bart-<name>`` fence and reports — without mutating anything — what's
wrong with it: unparseable JSON, an unknown block name, a missing required
field, a present field with the wrong JSON type, HTML-unsafe math in a string
value, or (folding in the old block-*density* check) too few of a block-type
that the artifact's skeleton mandates.

Callers decide what to do with the findings:
  * ``agents/author.py`` runs it right after generation and does a targeted
    re-ask to fix what it can, surfacing the rest as run-record warnings.
  * ``render/packet.py`` runs it post-sanitize on the way to HTML and pushes
    any genuine residue into ``render_warnings.json`` + the run record.

It enumerates fences via ``block_expand.iter_block_fences`` and parses JSON
via ``block_expand._loads_block_json`` — the *same* helpers ``expand_blocks``
uses — so what gets validated is exactly what gets rendered.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import block_expand as _be
from .block_schemas import BLOCK_SCHEMAS
from .math_safety import check_math_html_safe


# Error kinds. Keep this set small and stable — callers branch on it.
ERROR_KINDS = (
    "json",          # JSON body wouldn't parse (even leniently)
    "not_object",    # JSON parsed but isn't an object
    "unknown_block", # ```bart-<name>``` with no registered renderer
    "missing_field", # a schema-required field is absent
    "wrong_type",    # a present field's top-level JSON type is wrong
    "unsafe_math",   # HTML-unsafe math ($...$ / bare <> / \uXXXX) in a string
    "too_few",       # under the per-artifact density floor for a block-type
)


@dataclass(frozen=True)
class BlockError:
    block_index: int   # 0-based fence position; -1 for `too_few` (not a fence)
    fence_name: str    # the ``<name>`` from ```bart-<name>``` (or the missing type)
    kind: str          # one of ERROR_KINDS
    detail: str        # human-readable specifics


# Coarse JSON-type → friendly name, for `wrong_type` detail strings.
def _typename(t) -> str:
    if isinstance(t, tuple):
        return " or ".join(_typename(x) for x in t)
    return {
        str: "string", list: "array", dict: "object", bool: "boolean",
        int: "integer", float: "number", object: "anything",
    }.get(t, getattr(t, "__name__", str(t)))


def _json_type_ok(value, expected) -> bool:
    if expected is object:
        return True
    # `bool` is a subclass of `int` in Python — JSON `true` must not satisfy
    # an `int` expectation, and a JSON number must not satisfy a `bool` one.
    if expected is bool:
        return isinstance(value, bool)
    if isinstance(expected, tuple):
        # When the tuple includes both bool and numerics, dispatch carefully.
        if bool in expected and isinstance(value, bool):
            return True
        non_bool = tuple(t for t in expected if t is not bool)
        if isinstance(value, bool):
            return bool in expected
        return any(_json_type_ok(value, t) for t in (non_bool or expected))
    if expected in (int, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def _iter_strings(value):
    """Yield every string anywhere inside a JSON value (recursively)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def validate_blocks(md: str, artifact_kind: str, subject: str) -> list[BlockError]:
    """Return the list of problems with the ``bart-<name>`` fences in ``md``.

    ``artifact_kind`` / ``subject`` drive the density (``too_few``) check;
    pass an artifact kind not in ``block_density.THRESHOLDS`` (or ``""``) to
    skip that part. Never raises; never mutates ``md``.
    """
    errors: list[BlockError] = []

    for index, name, body in _be.iter_block_fences(md):
        if name not in _be._BLOCK_REGISTRY:
            errors.append(BlockError(
                index, name, "unknown_block",
                f"no registered block named 'bart-{name}'",
            ))
            continue

        if not body:
            payload: object = {}
        else:
            try:
                payload = _be._loads_block_json(body)
            except json.JSONDecodeError as e:
                errors.append(BlockError(
                    index, name, "json",
                    f"JSON parse error: {e.msg} (line {e.lineno}, col {e.colno})",
                ))
                continue

        if not isinstance(payload, dict):
            errors.append(BlockError(
                index, name, "not_object",
                "block body must be a JSON object",
            ))
            continue

        schema = BLOCK_SCHEMAS.get(name)
        if schema is not None:
            for req in schema.required:
                if req not in payload:
                    errors.append(BlockError(
                        index, name, "missing_field",
                        f"missing required field '{req}'",
                    ))
            for field_name, expected in schema.field_types.items():
                if field_name in payload and not _json_type_ok(payload[field_name], expected):
                    errors.append(BlockError(
                        index, name, "wrong_type",
                        f"field '{field_name}' should be {_typename(expected)}, "
                        f"got {_typename(type(payload[field_name]))}",
                    ))

        # HTML-unsafe math in any string value of the block.
        for s in _iter_strings(payload):
            for w in check_math_html_safe(s):
                errors.append(BlockError(
                    index, name, "unsafe_math", w.detail,
                ))

    # Density floor — fold the old "too few blocks" check in here.
    if artifact_kind:
        from ..agents import block_density
        report = block_density.evaluate(artifact_kind, md)
        for missing_name, deficit in sorted(report.missing.items()):
            errors.append(BlockError(
                -1, missing_name, "too_few",
                f"need {deficit} more `bart-{missing_name}` "
                f"(have {report.counts.get(missing_name, 0)})",
            ))

    return errors
