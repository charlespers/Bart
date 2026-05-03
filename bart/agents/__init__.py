"""Agentic generation pipeline.

Each agent is a focused unit with a single responsibility:

- PlannerAgent      → produces a syllabus / day-by-day plan from the corpus
- AuthorAgent       → produces long-form artifacts (lessons, schematics, etc.)
- CriticAgent       → grades an artifact against quality rubrics
- ReviserAgent      → applies critic notes to revise an artifact
- ResearcherAgent   → answers per-day "what's in the materials about X?" queries

Orchestration logic lives in `bart.orchestrator`; agents themselves are stateless.
"""

from .author import AuthorAgent
from .critic import CriticAgent
from .planner import PlannerAgent
from .researcher import ResearcherAgent
from .reviewer import ReviewerAgent, ReviewResult
from .reviser import ReviserAgent

__all__ = [
    "PlannerAgent", "AuthorAgent", "CriticAgent", "ReviserAgent",
    "ResearcherAgent", "ReviewerAgent", "ReviewResult",
]
