"""Generate one day's Reel script with Claude.

A single API call returns JSON, validated into a `ReelScript`. The prompt
hard-codes the GROWTH-KIT voice rules so the output stays honest and
on-brand: say what Bart physically gives a student, cite their own notes,
never reach for "AI-powered" / "revolutionary" / "game-changer".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import List

import anthropic
from pydantic import BaseModel, Field, ValidationError, field_validator

from .source import ProductFacts

# Target Reel length. Three beats, hook-led, ~7 seconds each.
TARGET_SECONDS = (20, 28)
SCENE_COUNT = 3

_SYSTEM = """You write short-form Instagram Reel scripts for Bart — a study tool \
at studywithbart.com. Your job is to make a student STOP scrolling, not to \
sound like a brand.

WHAT BART IS (the literal product, never lie about it):
Bart turns a student's actual course materials — lecture PDFs, slides, past \
exams — into a personalized 7-day study packet in about 10 minutes. The output \
is a plan, not a chatbot: day-by-day lessons, schematics, mnemonics, a full \
practice exam, a 60-minute review guide. Every claim cites the student's own \
notes. It is $10/mo, cancel anytime.

TONE (this is a vibe shift — do NOT be polite-corporate):
- Sound like a Princeton senior dunking on bad study habits at 2 a.m. in \
the dining hall.
- Hook MUST be a pattern interrupt: call out a behavior, drop a \
counter-intuitive claim, name the cope. "POV:", "Reading my notes for the \
8th time like that's gonna help", "Your highlighter is a coping mechanism", \
"Tell me you procrastinated without telling me you procrastinated."
- Trolling is welcome when it's directed at the *student's bad habit*, \
not at students themselves. Bart is the friend who roasts you a little so \
you actually study.
- Use sentence fragments. Slang OK. Emoji sparingly (max 1-2 per Reel, \
only if they LAND — never as decoration).
- ALL CAPS for a single emphasis word is fine. Never a whole line.
- It is fine to be a little chaotic. It is not fine to be dishonest.

BANNED words (still): "AI-powered", "revolutionary", "game-changer", \
"unleash", "supercharge", "transform your studying", "unlock your \
potential". If you find yourself typing these, you're sounding like an \
ad. Stop, restart.

EVERY REEL NEEDS:
1. A hook (≤ 9 words) that could be its own tweet.
2. A re-hook at scene 2: a twist, an admission, a "but here's the thing".
3. A payoff at scene 3: the concrete thing Bart gives them, named \
plainly. Not "AI-powered solution" — say "a 60-minute review guide" or \
"a one-page schematic of week 4".

PUNCH WORD: each scene's `on_screen_text` MUST end with a 1–2 word punch \
(the noun or verb you actually want them to remember). The renderer \
draws the LAST WORD of each on-screen line in the brand accent color, so \
make sure ending on it lands. Example: "Bart writes the *review*." → the \
word "review" gets the highlight.

OUTPUT: a single JSON object, no prose around it, matching exactly:
{
  "title": "<= 8 words, internal label",
  "hook": "<= 9 words, the first on-screen line, must stop the scroll",
  "scenes": [
    {"order": 1, "on_screen_text": "<= 12 words ending on the punch \
word", "narration": "1 sentence spoken aloud, may be a fragment", \
"seconds": 7},
    {"order": 2, ...},
    {"order": 3, ...}
  ],
  "caption": "2-4 sentences for the post caption — playful is good, \
ends with studywithbart.com",
  "hashtags": ["studytips", "..."]  // 5-8, no leading '#', lowercase
}
The three narration sentences read aloud back-to-back must form one \
smooth voiceover (a Kokoro neural TTS will speak them — write for the \
ear, not the eye, in the narration). Keep total spoken time within %d-%d \
seconds."""  % TARGET_SECONDS


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


def _build_user_prompt(facts: ProductFacts, angle: str | None) -> str:
    user = facts.as_prompt_block() + "\n\n"
    user += (
        f"Write today's Reel. Angle to take: {angle}.\n"
        if angle
        else "Write today's Reel — pick one honest, specific angle.\n"
    )
    user += "Return only the JSON object."
    return user


def _call_via_sdk(client: anthropic.Anthropic, model: str, user: str) -> str:
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AnthropicError as e:
        raise ScriptError(f"Anthropic API call failed: {e}") from e
    return "".join(b.text for b in resp.content if b.type == "text")


def _call_via_cli(cli_path: str, model: str, user: str) -> str:
    """Shell out to `claude --print` so the user's subscription is billed.

    `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are stripped from the
    subprocess env — leaving them set would force the CLI into API mode
    (and a billing path that may be empty), defeating the point of this
    fallback. The CLI's own keychain login provides the subscription auth.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    combined = f"{_SYSTEM}\n\n---\n\n{user}"
    try:
        proc = subprocess.run(
            [cli_path, "--print", "--model", model,
             "--output-format", "text"],
            input=combined, capture_output=True, text=True,
            env=env, timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        raise ScriptError("`claude --print` timed out after 5 minutes.") from e
    if proc.returncode != 0:
        raise ScriptError(
            f"`claude --print` failed (rc={proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:400]}"
        )
    return proc.stdout


def generate_script(
    facts: ProductFacts,
    *,
    auth: tuple[str, str],
    model: str,
    angle: str | None = None,
) -> ReelScript:
    """Generate and validate one ReelScript. Raises ScriptError on failure.

    `auth` is the tuple returned by `OutreachConfig.resolve_anthropic_auth`:
      ``("oauth",   token)``   — SDK with the subscription OAuth token
      ``("cli",     path)``    — shell out to the `claude` CLI (subscription)
      ``("api_key", key)``     — SDK with a direct API key (api credits)
    Anything else raises before any network call.
    """
    kind, value = auth
    user = _build_user_prompt(facts, angle)

    if kind == "cli":
        text = _call_via_cli(value, model, user)
    elif kind == "oauth":
        # Explicit auth_token sends `Authorization: Bearer …` and bypasses
        # any ANTHROPIC_API_KEY env var the SDK would otherwise prefer.
        client = anthropic.Anthropic(auth_token=value)
        text = _call_via_sdk(client, model, user)
    elif kind == "api_key" and value:
        client = anthropic.Anthropic(api_key=value)
        text = _call_via_sdk(client, model, user)
    else:
        raise ScriptError(
            "No Anthropic auth. Install the `claude` CLI for subscription "
            "billing, or set CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY / "
            "anthropic_api_key in .outreach_config.json."
        )

    try:
        data = _extract_json(text)
        return ReelScript(**data)
    except (ValueError, ValidationError) as e:
        raise ScriptError(f"Model returned an unusable script: {e}") from e
