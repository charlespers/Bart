"""JSON Schema → GBNF grammar compiler.

llama.cpp lets you constrain sampling to a GBNF grammar so the model
*cannot* emit invalid JSON. This converts the JSON Schema subset that
Bart's agents actually use (object with named string/integer/array
properties, optional enum, optional required list) into GBNF.

We don't try to handle every corner of JSON Schema — just enough to lock
down the structures the orchestrator parses downstream. Anything we can't
translate falls back to the permissive `json` rule, which still
guarantees parseable JSON but not schema conformance.

For mlx-lm, GBNF isn't supported. The client falls back to llguidance's
`response_format={"type":"json_schema",...}` if the server build supports
it; otherwise it relies on the model's native JSON-mode + retry.
"""
from __future__ import annotations

import json
from typing import Any


_PRIMITIVE_RULES = """\
ws ::= [ \\t\\n\\r]*
string ::= "\\"" ( [^"\\\\] | "\\\\" ["\\\\/bfnrt] | "\\\\u" [0-9a-fA-F]{4} )* "\\""
integer ::= "-"? ([0-9] | [1-9] [0-9]+)
number ::= "-"? ([0-9] | [1-9] [0-9]+) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?
boolean ::= "true" | "false"
null ::= "null"
json ::= object | array | string | number | boolean | null
array ::= "[" ws ( json (ws "," ws json)* )? ws "]"
object ::= "{" ws ( string ws ":" ws json (ws "," ws string ws ":" ws json)* )? ws "}"
"""


def schema_to_gbnf(schema: dict[str, Any], *, root_name: str = "root") -> str:
    """Compile a JSON Schema dict to a GBNF grammar string.

    Supported features:
    - type: object / array / string / integer / number / boolean / null
    - properties (object), required (object)
    - items (array)
    - enum (string)

    Anything else degrades to the permissive `json` rule.
    """
    rules: dict[str, str] = {}
    counter = [0]

    def fresh(name: str) -> str:
        counter[0] += 1
        return f"{name}-{counter[0]}"

    def compile_node(node: Any) -> str:
        if not isinstance(node, dict):
            return "json"
        t = node.get("type")
        if isinstance(t, list):
            # Multi-type — fall back to `json` to keep things simple.
            return "json"
        if t == "string":
            if "enum" in node and isinstance(node["enum"], list):
                # The model emits a JSON string, so each enum option must
                # itself be quoted: '"easy"' | '"medium"' | '"hard"'.
                literal_opts = []
                for v in node["enum"]:
                    if not isinstance(v, str):
                        continue
                    # Escape inner quotes / backslashes for GBNF.
                    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
                    literal_opts.append(f'"\\"{escaped}\\""')
                if literal_opts:
                    name = fresh("enum")
                    rules[name] = " | ".join(literal_opts)
                    return name
            return "string"
        if t == "integer":
            return "integer"
        if t == "number":
            return "number"
        if t == "boolean":
            return "boolean"
        if t == "null":
            return "null"
        if t == "array":
            items = node.get("items")
            inner = compile_node(items) if items else "json"
            name = fresh("arr")
            rules[name] = (
                f'"[" ws ( {inner} (ws "," ws {inner})* )? ws "]"'
            )
            return name
        if t == "object":
            props = node.get("properties") or {}
            required = node.get("required") or list(props.keys())
            if not isinstance(props, dict) or not props:
                return "object"
            # Build pairs in the required order. Optional properties are
            # not yet emitted by this compiler — the model is constrained
            # to emit exactly the required keys, in the order given. The
            # downstream parser tolerates either ordering.
            pairs: list[tuple[str, str]] = []
            for prop_name in required:
                if prop_name not in props:
                    continue
                value_rule = compile_node(props[prop_name])
                pairs.append((prop_name, value_rule))
            if not pairs:
                return "object"
            sep = ' ws "," ws '
            chunks: list[str] = []
            for key, val in pairs:
                # Emit the JSON-quoted key as a single GBNF literal:  "\"key\""
                key_literal = '"\\"' + key + '\\""'
                chunks.append(f'{key_literal} ws ":" ws {val}')
            body = sep.join(chunks)
            name = fresh("obj")
            rules[name] = f'"{{" ws {body} ws "}}"'
            return name
        return "json"

    root_rule_body = compile_node(schema)
    out_lines: list[str] = []
    out_lines.append(f"{root_name} ::= {root_rule_body}")
    for name, body in rules.items():
        out_lines.append(f"{name} ::= {body}")
    out_lines.append(_PRIMITIVE_RULES.rstrip())
    return "\n".join(out_lines) + "\n"
