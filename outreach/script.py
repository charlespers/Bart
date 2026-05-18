"""Generate one day's Reel script with Claude.

A single API call returns JSON, validated into a `ReelScript`. The prompt
hard-codes the GROWTH-KIT voice rules so the output stays honest and
on-brand: say what Bart physically gives a student, cite their own notes,
never reach for "AI-powered" / "revolutionary" / "game-changer".
"""
from __future__ import annotations

import json
import re
from typing import List

import anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator

from .source import ProductFacts

# Target Reel length. Three beats, hook-led, ~7 seconds each.
TARGET_SECONDS = (20, 28)
SCENE_COUNT = 3

_SYSTEM = """You write short-form Instagram Reel scripts for Bart — a study tool \
at studywithbart.com.

WHAT BART IS (say it this way):
Bart turns a student's actual course materials — lecture PDFs, slides, past \
exams — into a personalized 7-day study packet in about 10 minutes. The output \
is a plan, not a chatbot: day-by-day lessons, schematics, mnemonics, a full \
practice exam, a 60-minute review guide. Every claim cites the student's own \
notes. It is $10/mo, cancel anytime.

VOICE RULES (non-negotiable):
- Lead with what the student physically gets, not with technology.
- NEVER use the words: "AI-powered", "revolutionary", "game-changer", \
"unleash", "supercharge". Students smell marketing.
- Concrete and calm. Sound like a smart friend, not an ad.
- One honest idea per Reel (a study tip, a pain point, a before/after).

OUTPUT: a single JSON object, no prose around it, matching exactly:
{
  "title": "<= 8 words, internal label",
  "hook": "<= 9 words, the first on-screen line, must stop the scroll",
  "scenes": [
    {"order": 1, "on_screen_text": "<= 12 words", "narration": "1 sentence \
spoken aloud", "seconds": 7},
    {"order": 2, ...},
    {"order": 3, ...}
  ],
  "caption": "2-4 sentences for the post caption, ends with \
studywithbart.com",
  "hashtags": ["studytips", "..."]  // 5-8, no leading '#', lowercase
}
The three narration sentences read aloud back-to-back must form one smooth \
voiceover. Keep total spoken time within %d-%d seconds."""  % TARGET_SECONDS


class Scene(BaseModel):
    order: int
    on_screen_text: str = Field(min_length=1, max_length=140)
    narration: str = Field(min_length=1, max_length=320)
    seconds: float = Field(ge=2, le=12)


class ReelScript(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    hook: str = Field(min_length=1, max_length=90)
    scenes: List[Scene]
    caption: str = Field(min_length=1, max_length=2200)
    hashtags: List[str]

    @field_validator("scenes")
    @classmethod
    def _scene_count(cls, v: List[Scene]) -> List[Scene]:
        if len(v) != SCENE_COUNT:
            raise ValueError(f"expected {SCENE_COUNT} scenes, got {len(v)}")
        return v

    @field_validator("hashtags")
    @classmethod
    def _clean_tags(cls, v: List[str]) -> List[str]:
        cleaned = [t.lstrip("#").strip().lower() for t in v if t.strip()]
        if not 3 <= len(cleaned) <= 12:
            raise ValueError("expected 3-12 hashtags")
        return cleaned

    @property
    def voiceover(self) -> str:
        """The full narration, spoken back to back."""
        return " ".join(s.narration.strip() for s in sorted(self.scenes, key=lambda s: s.order))

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.scenes)

    @property
    def caption_with_tags(self) -> str:
        tags = " ".join(f"#{t}" for t in self.hashtags)
        return f"{self.caption.strip()}\n\n{tags}"


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("model reply contained no JSON object")
        return json.loads(m.group(0))


class ScriptError(RuntimeError):
    pass


def generate_script(
    facts: ProductFacts,
    *,
    api_key: str,
    model: str,
    angle: str | None = None,
) -> ReelScript:
    """Generate and validate one ReelScript. Raises ScriptError on failure."""
    if not api_key:
        raise ScriptError(
            "No Anthropic API key. Set it in .outreach_config.json, the "
            "ANTHROPIC_API_KEY env var, or bart's .bart_config.json."
        )

    user = facts.as_prompt_block() + "\n\n"
    user += (
        f"Write today's Reel. Angle to take: {angle}.\n"
        if angle
        else "Write today's Reel — pick one honest, specific angle.\n"
    )
    user += "Return only the JSON object."

    client = anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AnthropicError as e:
        raise ScriptError(f"Anthropic API call failed: {e}") from e

    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        data = _extract_json(text)
        return ReelScript(**data)
    except (ValueError, ValidationError) as e:
        raise ScriptError(f"Model returned an unusable script: {e}") from e
