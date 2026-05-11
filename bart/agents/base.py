"""Common base for agents."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..config import Config
from ..llm import LLMClient
from ..prompts import load_prompt


def extract_json(text: str, expect: str = "any") -> Any:
    """Lenient JSON extraction that tolerates Gemma / small-model output:
      - bare JSON (Ollama json-mode produces this)
      - JSON wrapped in ```json fences (the original Claude-targeted shape)
      - JSON wrapped in ``` (no language hint)
      - JSON preceded or followed by prose / chatter
      - JSON with C-style trailing commas (small models occasionally emit)
      - JSON wrapped inside <json>...</json> tags

    `expect`: "object" → must be `{...}`, "array" → must be `[...]`, "any"
    accepts either.

    Returns the parsed value, or `None` if no valid JSON could be recovered.
    """
    if not text:
        return None
    s = text.strip()

    # 1. Try the bare value first — fastest path, what json-mode produces.
    candidates: list[str] = []
    if s and s[0] in "{[":
        candidates.append(s)

    # 2. Fenced JSON (with or without language hint).
    for m in re.finditer(r"```(?:json|JSON)?\s*\n?([\s\S]*?)\n?```", s):
        candidates.append(m.group(1).strip())

    # 3. <json>...</json> tag-wrapped output.
    for m in re.finditer(r"<json>\s*([\s\S]*?)\s*</json>", s, re.IGNORECASE):
        candidates.append(m.group(1).strip())

    # 4. First balanced `{...}` or `[...]` substring anywhere in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        if expect == "object" and opener != "{":
            continue
        if expect == "array" and opener != "[":
            continue
        idx = s.find(opener)
        while idx >= 0:
            depth, in_str, esc, end = 0, False, False, -1
            for i, ch in enumerate(s[idx:], idx):
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                candidates.append(s[idx:end])
            idx = s.find(opener, idx + 1)

    # 5. Try each candidate, with one attempt at trailing-comma repair.
    for raw in candidates:
        for attempt in (raw, re.sub(r",(\s*[}\]])", r"\1", raw)):
            try:
                value = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if expect == "object" and not isinstance(value, dict):
                continue
            if expect == "array" and not isinstance(value, list):
                continue
            return value
    return None


@dataclass
class AgentContext:
    cfg: Config
    llm: LLMClient
    corpus_block: list[dict]  # cacheable user-message preamble containing the corpus
    # `corpus_block` is the canonical full-corpus preamble used by Planner,
    # Researcher, Author. Reviewer/Critic/Reviser pass corpus_block=[] when
    # they only need the artifact + brief — saves significant input tokens.


class Agent:
    name: str = "agent"
    prompt_file: str = ""

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    @property
    def system_prompt(self) -> str:
        # All supported backends (Anthropic API, Claude Code subscription,
        # local Qwen3) handle system role + structured output natively, so
        # no per-mode prompt prepend is needed.
        return load_prompt(self.prompt_file)
