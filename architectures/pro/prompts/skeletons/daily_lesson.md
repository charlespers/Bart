# Day {day_num} — {topic}

_{day_date} · {focus} · {hours}h_

## Why today matters

Open with **exactly one** `bart-why-it-matters` block. Don't summarize the day; pose the question the day will answer. ("By the end of this lesson you'll be able to ___, which lets you ___.") Corpus-grounded, ≤4 sentences.

```bart-why-it-matters
{"body": "<the question this day answers + what it unlocks>"}
```

## Pre-quiz (cold start)

ONE `bart-quick-check` (label `"Cold start"`) asking a high-leverage question a naive student probably can't answer yet. Roediger's testing-effect: the *struggle* before exposure is what primes encoding. Don't be afraid of a hard question — even wrong answers help.

If INTERLEAVED_REVIEW is non-empty, emit ONE additional `bart-quick-check` (label `"Recall from yesterday"`) BEFORE introducing today's new content.

## Threshold concepts for today

A short markdown list of the 1–3 ideas where understanding *qualitatively shifts* — the moments where, before you got it, nothing made sense, and after you got it, everything does. Each item is one sentence. These are NOT all the topics — they're the load-bearing pivots.

## Core content

For each load-bearing concept (typically 2–4 per day), use ONE `bart-concept-build` block — **this is the core teaching arc**. Fill these rungs:

```bart-concept-build
{"name": "<concept name>",
 "threshold": true,
 "motivate": "<why this idea has to exist — open with the question, not the answer>",
 "define": "<formal statement, in the corpus's notation>",
 "define_tex": "<latex if applicable>",
 "example": "<one concrete numerical example you can compute by hand>",
 "example_tex": "<latex>",
 "connect": "<tie to a prior topic the reader already understands>",
 "contrast": "<vs the closest neighbor concept students confuse it with>",
 "apply": "<use it on a corpus problem; show reasoning, not just answer>"}
```

Set `threshold: true` for the 1–3 threshold concepts identified above. Plain concepts can omit it.

For each formula referenced inside a concept-build, ALSO emit ONE `bart-formula-card` next to it (the concept-build conveys the *intuition*; the formula-card is the *cheat-sheet entry*). Include `legend`, a one-line `note`, and `cite` to the corpus location.

## Misconceptions

For every threshold concept, ONE `bart-trap-callout` (kind=trap) titled "What students usually think". Surface the wrong-but-natural model FIRST, then the right framing. Naming the misconception is what dismantles it; teaching the right thing alone doesn't.

Add other `bart-trap-callout` blocks (kind=warn or note) for sign errors, units, and edge cases as they come up.

## Worked examples

3–6 `bart-worked-example` blocks. Steps include `action`, `math` (LaTeX), and `reasoning` (the *why* of each step — this is what makes a worked example a *teaching tool* vs. an answer key). The `cite` field links to the corpus problem the example is based on.

Active-recall cues sprinkled in the surrounding prose: "pause — what would you predict the next step is?" before revealing it.

## Past-exam problems

For every entry in TODAYS_PROBLEMS (corpus-indexed), emit one `bart-multi-step` block: `problem` is the corpus statement (verbatim id + text), `hints` is 2–3 progressive hints (NOT giveaways — each hint should pose a sub-question), `solution` is the full worked answer with reasoning.

## Whimsical hook

If WHIMSY_HOOK is present, use it as the basis for ONE `bart-mnemonic-card`. The mnemonic must aid the math, not replace it.

## Practice problems

8–10 drills. Mix the formats:
- 3–4 `bart-multi-step` (multi-step problems with hints).
- 2–3 `bart-multiple-choice` (with `correct: true` on the right answer AND `explanation` on EVERY choice — wrong-answer explanations are where the teaching happens).
- 1–2 `bart-fill-in-blank` (`{{0}}`-style placeholders, case-insensitive `accept`).
- 1–2 of: `bart-build-equation`, `bart-match-pairs`, or `bart-drag-order` (when ordering matters).

## Cheat-sheet candidates

A short markdown list (3–6 entries) — formulas + facts + intuitions to transcribe today.

## End-of-day flashcards

ONE `bart-checkpoint` block with 10–15 cards. Each `front` is a *prompt* (a question or partial scenario), each `back` is the answer + a one-line *why*. The "why" is what turns rote drilling into understanding.

```bart-checkpoint
{"title": "Day {day_num} review",
 "cards": [{"front":"<prompt>","back":"<answer + one-line why>"}, ...]}
```

## Tomorrow preview

One paragraph orienting toward day {next_day}. Tie it back to today's "why this matters" — the lesson is a journey, not a list. No block — just prose.
