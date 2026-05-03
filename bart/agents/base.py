"""Common base for agents."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Config
from ..llm import LLMClient
from ..prompts import load_prompt


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
        return load_prompt(self.prompt_file)
