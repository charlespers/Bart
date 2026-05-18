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
generate → studywithbart.com → Claude script → TTS voice + music → HyperFrames Reel
         → queue/<date>/  (state: draft)
   ── you review the folder ──
approve  → state: approved
publish  → Instagram Graph API → state: published
insights → reach / plays / saves / shares
```

A human review gate sits between `generate` and `publish`; `publish` refuses
anything not `approved`.

## Setup

```bash
pip install -r outreach/requirements.txt   # or just use ./run-outreach
brew install ffmpeg                        # required for audio + video
```

HyperFrames is fetched on demand via `npx hyperframes` (needs Node.js 22+).

Then fill in `.outreach_config.json` (created at the repo root on first run):

| field | needed for | how to get it |
|-------|-----------|---------------|
| `meta_access_token` | publish, insights | Meta Developer app → Instagram Graph API, long-lived token |
| `ig_user_id` | publish, insights | the Instagram **Professional** account's user id |
| `public_video_base_url` | publish | a public HTTPS folder where rendered MP4s are served |
| `anthropic_api_key` | generate | falls back to `$ANTHROPIC_API_KEY` or bart's config |
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
```

### Publishing note

The Graph API ingests Reels from a **public HTTPS URL** — it does not accept
a local file here. Before `publish`, get `queue/<date>/reel.mp4` to
`{public_video_base_url}/<date>.mp4` (e.g. copy it into the Bart website's
static assets on Fly.io), or pass `--video-url` explicitly.

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

`launchctl load ~/Library/LaunchAgents/com.bart.outreach.plist`. Each morning
a fresh draft is waiting; you review and `publish` it.

## Layout

```
outreach/
  config.py    OutreachConfig — tokens, ids, paths
  source.py    fetch studywithbart.com → grounding facts
  script.py    Claude → validated ReelScript
  audio.py     TTS voiceover + music bed (ffmpeg)
  render.py    fill templates/reel.html → MP4 (HyperFrames)
  generate.py  the daily generate step
  review.py    queue state machine + the review gate
  publish.py   Instagram Graph API REELS publish flow
  insights.py  read-only performance metrics
  doctor.py    preflight checks
  cli.py       command-line interface
  templates/reel.html   9:16 HyperFrames composition
  music/       your royalty-free tracks (gitignored)
  queue/       generated items (gitignored)
```

See `docs/superpowers/specs/2026-05-18-instagram-outreach-pipeline-design.md`
for the full design.
