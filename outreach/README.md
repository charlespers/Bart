# Bart Instagram Outreach Pipeline

A daily, open-source pipeline that generates branded Instagram **Reels** for
[studywithbart.com](https://studywithbart.com) — vertical video with an AI
voiceover over a royalty-free music bed — and publishes the ones you approve
through the official **Instagram Graph API**.

It is built to match `GROWTH-KIT.md`: *honest posting under your own
accounts.* It only ever touches Bart's own account and Bart's own content.

### What it does NOT do

- No automated likes, comments, or follows on other accounts. There is no
  Graph API endpoint for that and it violates Instagram's Terms.
- No browser automation (Selenium / InstaPy).
- No account password — the Graph API uses OAuth access tokens only.

## How it works

```
generate → studywithbart.com → Claude script → TTS voice + music
         → Pillow PNG frames → ffmpeg MP4
         → queue/<date>/  (state: draft)  + thumbnail + upload.md
         → strategy.md refreshed from queue + insights
   ── you review the folder ──
approve  → state: approved
publish  → Instagram Graph API → state: published
insights → reach / plays / saves / shares
```

A human review gate sits between `generate` and `publish`; `publish` refuses
anything not `approved`. The renderer is self-contained ffmpeg + Pillow —
no headless browser, no Node, deterministic.

## Setup

```bash
pip install -r outreach/requirements.txt   # or just use ./run-outreach
brew install ffmpeg                        # required for audio + video
```

For script generation you need **one of**, in this order:

1. The `claude` CLI on `PATH` (Claude Code subscription billing — recommended
   when you're already a subscriber; the pipeline shells out to
   `claude --print` so no token plumbing is needed).
2. `CLAUDE_CODE_OAUTH_TOKEN` in the environment (subscription billing for
   headless / CI setups).
3. An Anthropic API key in `.outreach_config.json`, `$ANTHROPIC_API_KEY`,
   or bart's own `.bart_config.json` (uses API credits — set
   `prefer_api_key: true` in the outreach config if you want this to win).

`.outreach_config.json` is created at the repo root on first run:

| field | needed for | how to get it |
|-------|-----------|---------------|
| `meta_access_token` | publish, insights | Meta Developer app → Instagram Graph API, long-lived token |
| `ig_user_id` | publish, insights | the Instagram **Professional** account's user id |
| `public_video_base_url` | publish | a public HTTPS folder where rendered MP4s are served |
| `anthropic_api_key` | generate (only if no `claude` CLI / OAuth) | falls back to `$ANTHROPIC_API_KEY` or bart's config |
| `prefer_api_key` | generate | flip auth priority so API key beats `claude` CLI |
| `tts_provider` | generate | `say` (default, macOS, free), `elevenlabs`, or `openai` |
| `tts_api_key` | generate | only for `elevenlabs` / `openai` |

Drop royalty-free music tracks into `outreach/music/`. Posts published via
the Graph API cannot use Instagram's licensed music catalog — the audio is
baked into the MP4, so it must be license-free or original.

`outreach doctor` reports exactly what is still missing.

## Usage

```bash
./run-outreach doctor                       # preflight checks
./run-outreach doctor --check-token         # also validate the Graph token live
./run-outreach generate                     # build tomorrow's Reel (draft)
./run-outreach generate --date 2026-05-20 --angle "exam-week stress"
./run-outreach review                       # list the queue
./run-outreach review --date 2026-05-20     # inspect one item
./run-outreach approve --date 2026-05-20    # pass the review gate
./run-outreach reject  --date 2026-05-20 --reason "tone off"
./run-outreach publish --date 2026-05-20    # publish an approved Reel
./run-outreach insights                     # performance of published Reels
./run-outreach strategy                     # refresh outreach/strategy.md
```

### What `generate` writes

Every successful `generate` leaves a fully upload-ready folder under
`outreach/queue/<date>/`:

```
reel.mp4         the post itself, 1080×1920, H.264/AAC, MP4 +faststart
audio.m4a        voice + music bed (the audio baked into reel.mp4)
voice.m4a        voiceover only (kept for reuse / re-render)
thumbnail.jpg    first hook frame as a Reels cover preview
caption.txt      caption + hashtags, paste-ready
hashtags.txt     hashtags only
script.json      the Claude-generated script (audit trail)
status.json      review-gate state (draft → approved → published)
upload.md        one-page checklist with caption + on-screen beats
source_cache.json  the studywithbart.com facts grounding the script
```

It also rewrites `outreach/strategy.md` (see below) so the playbook always
reflects the freshest draft.

### Publishing note

The Graph API ingests Reels from a **public HTTPS URL** — it does not accept
a local file here. Before `publish`, get `queue/<date>/reel.mp4` to
`{public_video_base_url}/<date>.mp4` (e.g. copy it into the Bart website's
static assets on Fly.io), or pass `--video-url` explicitly.

## Engagement strategy (`outreach/strategy.md`)

`generate` (and `strategy`) rewrite a living playbook at
`outreach/strategy.md` with:

- **Stance** — non-negotiable voice rules lifted from `GROWTH-KIT.md`.
- **Queue state** — count of drafts / approved / published / rejected.
- **Last 7 drafts** — date + hook for each, so the angle bank stays fresh.
- **Performance table** — reach / plays / likes / saved / shares per
  published Reel (Graph API insights). Empty until the first publish.
- **Posting time** — honest read of the data; defaults to 7 PM weekday
  before there are enough Reels to commit to a slot.
- **Angle bank** — 7 upcoming angles that don't repeat last week's titles.
  Use these as `--angle` arguments.
- **Hashtag rotation** — top tags by reach, backfilled with the least-used
  brand-approved pool entries.

Anything below the `<!-- notes -->` marker is preserved across rewrites, so
you can jot down ideas and experiments without losing them on the next
`generate`.

## Daily automation

`generate` is the only step safe to schedule unattended (the review gate
keeps `publish` manual). A `launchd` agent on macOS:

```xml
<!-- ~/Library/LaunchAgents/com.bart.outreach.plist -->
<dict>
  <key>Label</key><string>com.bart.outreach</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/Desktop/Bart_OUTREACH/Bart/run-outreach</string>
    <string>generate</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
</dict>
```

`launchctl load ~/Library/LaunchAgents/com.bart.outreach.plist`. Each
morning a fresh draft is waiting; you review and `publish` it.

## Layout

```
outreach/
  config.py    OutreachConfig — tokens, ids, paths, auth resolution
  source.py    fetch studywithbart.com → grounding facts
  script.py    Claude (SDK or CLI subscription) → validated ReelScript
  audio.py     TTS voiceover + music bed (ffmpeg)
  render.py    Pillow PNG frames + ffmpeg concat → 1080×1920 MP4
  bundle.py    upload-ready files: caption / hashtags / thumbnail / upload.md
  generate.py  the daily generate step (with rendered-MP4 verification)
  review.py    queue state machine + the review gate
  publish.py   Instagram Graph API REELS publish flow
  insights.py  read-only performance metrics
  strategy.py  outreach/strategy.md generator (engagement playbook)
  doctor.py    preflight checks
  cli.py       command-line interface
  music/       your royalty-free tracks (gitignored)
  queue/       generated items (gitignored)
  strategy.md  the living engagement playbook (regenerated; gitignored)
```

See `docs/superpowers/specs/2026-05-18-instagram-outreach-pipeline-design.md`
for the original design.
