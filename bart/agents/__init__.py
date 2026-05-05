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
from .distiller import DistillerAgent
from .exam_pattern import ExamPatternAgent
from .notation_extractor import NotationExtractorAgent
from .planner import PlannerAgent
from .problem_indexer import ProblemIndexerAgent
from .researcher import ResearcherAgent
from .reviewer import ReviewerAgent, ReviewResult
from .reviser import ReviserAgent
from .solver import SolverAgent
from .topic_distiller import TopicDistillerAgent
from .whimsy_indexer import WhimsyIndexerAgent

__all__ = [
    "DistillerAgent", "TopicDistillerAgent", "PlannerAgent", "AuthorAgent",
    "CriticAgent", "ReviserAgent", "ResearcherAgent",
    "ReviewerAgent", "ReviewResult",
    "NotationExtractorAgent", "ProblemIndexerAgent", "ExamPatternAgent",
    "SolverAgent", "WhimsyIndexerAgent",
]
