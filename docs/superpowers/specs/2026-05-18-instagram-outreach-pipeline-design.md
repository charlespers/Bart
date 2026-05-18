# Design — Bart Instagram Outreach Pipeline

Date: 2026-05-18
Status: Approved (Approach A)

## Goal

A daily, open-source automation pipeline that generates post-ready Instagram
Reels for **studywithbart.com** — branded vertical video with voiceover and a
music bed — and publishes approved ones to Bart's *own* Instagram account via
the official Instagram Graph API. Plus a performance-insights command.

This serves the existing `GROWTH-KIT.md` strategy: *"honest posting you do
under your own accounts. No bots, no fake signups, no spam."* The pipeline
only ever touches Bart's own account and Bart's own content.

## Non-goals (explicitly out of scope)

- No automated likes, comments, or follows on other accounts. There is no
  Graph API endpoint for it and it violates Instagram's Terms — permanently
  out of scope.
- No browser automation (Selenium / InstaPy). Graph API only.
- No use of account passwords. The Graph API authenticates with OAuth access
  tokens exclusively.

## Approach

Approach A: a standalone `outreach/` Python package inside the Bart repo,
alongside `bart/`. Local-first CLI, mirroring how `bart/` already works
(`./run`-style launcher, pydantic config, `rich` output). Reuses
`bart.backends.AnthropicAPIBackend` for LLM calls and `bart.branding` color
tokens for on-brand video.

Two-phase with a **human review gate**:

1. `outreach generate` — builds tomorrow's Reel into a dated queue folder.
2. A person reviews the folder and runs `outreach approve <date>`.
3. `outreach publish` — pushes approved items live via the Graph API.

A local `launchd`/`cron` entry runs `generate` daily. `publish` stays manual
(or can be a second scheduled job once output quality is trusted).

## Architecture

```
outreach/
  config.py      OutreachConfig (pydantic) — tokens, IDs, paths. .outreach_config.json, chmod 600
  paths.py       queue/published dir layout, dated item dirs
  source.py      fetch + parse studywithbart.com for grounding facts
  script.py      LLM → ReelScript (hook, scenes, narration, on-screen text, caption, hashtags)
  render.py      fill templates/reel.html, run `npx hyperframes render`
  audio.py       TTS narration + royalty-free music bed → mixed track (ffmpeg)
  templates/
    reel.html    HyperFrames 9:16 (1080x1920) branded composition
  music/         user-supplied royalty-free tracks (.gitignored)
  review.py      list / show / approve / reject queue items
  publish.py     Graph API: create REELS container → poll → media_publish
  insights.py    Graph API: per-media + account insights report
  cli.py         argparse: doctor | generate | review | approve | reject | publish | insights
  __main__.py
run-outreach     venv-bootstrapping launcher (mirrors ./run)
```

### Data flow (one day)

```
source.py  → product facts from studywithbart.com
   ↓
script.py  → ReelScript (Claude, grounded in those facts + GROWTH-KIT positioning)
   ↓
audio.py   → voice.<ext> (TTS) + music bed → audio.m4a (ffmpeg mix)
render.py  → reel.html filled with script + audio → reel.mp4 (npx hyperframes render)
   ↓
queue/<YYYY-MM-DD>/  { script.json, voice.m4a, audio.m4a, reel.mp4, caption.txt, status.json }
   ↓  ── HUMAN REVIEW GATE ──
approve    → status.json: approved
publish.py → reel.mp4 served at a public URL → Graph API REELS container → publish
   ↓
status.json: published  + media_id / permalink recorded (item stays in queue/)
insights.py → reach / plays / saves / shares per post
```

Items never move between directories: a published Reel stays in `queue/`
with `status.state == "published"`, a single source of truth for both
`review.py` and `insights.py`.

### Queue item: `queue/<YYYY-MM-DD>/status.json`

`{ "date", "state": draft|approved|rejected|published, "media_id", "permalink", "history": [...] }`

## Key components

- **source.py** — `requests` + `beautifulsoup4` (already a Bart dependency)
  GET the homepage and `/sample`; extract positioning copy. Cached per day so
  the site is hit once. Falls back to `GROWTH-KIT.md` positioning if offline.
- **script.py** — one `AnthropicAPIBackend` call returning JSON validated into
  a `ReelScript` pydantic model. Prompt encodes GROWTH-KIT voice rules ("say
  what it physically gives them", avoid "AI-powered"/"revolutionary"). 3 scene
  beats, ~18–25s total.
- **audio.py** — TTS providers, pluggable: `say` (macOS built-in, zero-cost,
  the default + offline test path), `elevenlabs`, `openai`. Output mixed with
  a random track from `music/` at low volume via `ffmpeg`. No music present →
  warn, proceed voice-only (degraded, not fatal).
- **render.py** — substitutes script fields + audio path into `reel.html`
  (1080x1920, Bart cream/terracotta palette), runs HyperFrames in a per-item
  temp project, copies out `reel.mp4`.
- **publish.py** — REELS publish flow. The Graph API requires `video_url` to
  be a **public HTTPS URL**, so a configurable `public_video_base_url` plus an
  upload hook (default: copy into the Bart website's static dir) makes the MP4
  reachable. Create container → poll `status_code` until `FINISHED` → `media_publish`.
- **insights.py** — `GET /{media-id}/insights` for reach/plays/saves/shares,
  `GET /{ig-user-id}/insights` for account-level; prints a `rich` table.

## Error handling

- `outreach doctor` checks: Python deps, Node 22+, `npx hyperframes`, `ffmpeg`,
  config completeness, token validity (a cheap `GET /me`), music presence.
  Every command runs the relevant subset of these checks first and fails
  with an actionable message rather than a stack trace.
- Each step writes to the item's `status.json` so a failed run is resumable;
  `generate` is idempotent per date.
- `publish` refuses any item not in `approved` state — the review gate cannot
  be bypassed by a flag.
- Graph API errors surface the Meta error `code`/`message` verbatim.

## External prerequisites (user must supply)

These are real blockers the code cannot create; `doctor` reports each:

1. Instagram **Professional** account linked to a Facebook Page.
2. A **Meta Developer app** with Instagram Graph API access.
3. A long-lived **access token** + the **IG user ID**.
4. A TTS API key *if* using `elevenlabs`/`openai` (the `say` default needs none).
5. `brew install ffmpeg`.
6. Royalty-free tracks dropped into `outreach/music/`.
7. A public HTTPS location to serve rendered MP4s (`public_video_base_url`).

## Testing

- Unit tests under `tests/`: `ReelScript` JSON parsing/validation, queue state
  transitions, the publish-gate refusal, HTML template substitution.
- `source.py` and Graph API calls tested against recorded fixtures, not live.
- End-to-end dry path: `say` TTS + a sample music file + `hyperframes render`
  produces a real `reel.mp4` locally with zero paid API keys. The publish step
  is verified only against real tokens — never mocked into a false "passing".
