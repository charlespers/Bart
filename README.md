![bart. — production-grade study-packet harness](./assets/readme.png)

> Drop your course materials in. Run once.

---

bart reads your course materials and writes a personalized exam-prep packet — master plan, day-by-day lessons, schematics, mnemonics, a full practice exam, and a 60-minute review guide. Five Claude agents working in tandem.

```
              .
             (")
        .-----------.
       /             \
      |   o       o   |
       \      ‿      /
        '-----------'
        (___)   (___)

      hi — i'm bart.
```

```
   you   ──▶   drop pdfs in ./materials   ──▶   ./run   ──▶   open ./output
```

That's the whole thing.

---

## Try it locally first (no API tokens spent)

To verify bart is wired up before committing to a real run, drop the included sample fixture and dry-run:

```bash
cd bart
cp examples/sample_notes.md materials/
./run run --dry-run
```

This bootstraps the venv, runs the wizard once (any dummy values are fine), extracts the sample, builds the corpus, and stops *before* hitting the API. If you see the bart splash and a corpus summary with no errors, you're ready.

`./run doctor` verifies Python, deps, and your API key.

---

## 60-second start

**Step 1.** Put your course materials inside `bart/materials/`. The easiest way is to drag-and-drop them in Finder (macOS) or Explorer (Windows). Flat files or subfolders — both work.

If you prefer the terminal, replace `<PATH-TO-YOUR-NOTES>` below with the real path on your machine (run `pwd` inside the folder if you're not sure):

```bash
cd bart

# copy every file from your real notes folder into materials/, recursively
cp -R "<PATH-TO-YOUR-NOTES>"/. materials/

# example for a folder on the Desktop:
#   cp -R ~/Desktop/CHEM-201/. materials/
```

**Step 2.** Run bart:

```bash
./run
```

bart finds every supported file under `materials/` recursively. No globs, no filtering — drop it all in, bart sorts it out.

First run:

- Builds a Python venv.
- Installs deps.
- Asks for your API key, exam date, and what you want to focus on. (One time, takes 30 seconds.)
- Runs for ~10 minutes.
- Drops a study packet in `output/`.

Subsequent runs skip the wizard.

<p align="center">
  <img src="assets/readme2.png" alt="terminal screenshot of bart starting up — the loaf in ASCII, the bart. wordmark, and status lines for scanning materials, extracting corpus, and planning the schedule" width="100%" />
</p>

<sub align="center"><i>what you see when you run <code>./run</code> — bart says hi, scans your materials, and gets to work.</i></sub>

---

## What you get

After one run, `output/run_<timestamp>/` looks like this:

```
output/run_2026-05-03_141503/
├── README.md                  ← packet index
├── 00_MASTER_PLAN.md          ← what to study, on what day, why
├── 01_SCHEMATICS.md           ← every diagram, formula table, and trap
├── 02_WHIMSICAL_NOTES.md      ← mnemonics and analogies that stick
├── 03_SHORT_STUDY_GUIDE.md    ← the 60-minute version
├── 04_PRACTICE_EXAM.md        ← full mock exam + answer key
└── daily_lessons/
    ├── Day_01_2026-05-04.md   ← 1000+ lines of dense, source-grounded study
    ├── Day_02_2026-05-05.md
    └── … one per day until exam day
```

Every file is markdown.

---

## How it differs from one-shot prompting

**Five agents, one packet.** Each has one job:

```
        ┌─────────────────┐
        │   your stuff    │
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │     Planner     │  reads everything, writes the master plan
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │   Researcher    │  for each day, finds the relevant bits
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │     Author      │  writes the lesson (long, dense, real)
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │     Critic      │  grades it 0–100, demands fixes
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │     Reviser     │  fixes anything that scored < 80
        └────────┬────────┘
                 │
              packet
```

Lessons are graded by a critic agent before they reach you. If a lesson is weak, it gets revised. You never see the bad draft.

**Source-grounded.** bart cites *your actual notes* — your textbook's notation, your past exams' problem numbers, your homework's vocabulary. No generic filler.

**Practice-heavy.** Every lesson ends with embedded Quick Checks (collapsible answers), worked examples, and 8–12 drill problems. The practice exam is a serious 3-hour mock with a real answer key.

---

## Commands

```bash
./run                              # generate a packet
./run setup                        # change API key, exam date, etc.
./run list                         # show every packet you've ever generated
./run doctor                       # health check
./run run --resume <id>            # resume a crashed run
./run run --dry-run                # extract + plan, no API calls
./run run --fast                   # speed preset: sonnet + no critic + parallel 8 (~5x faster)
./run run --no-critic              # skip the grading loop (faster)
./run run --max-parallel 8         # generate more lessons at once
./run run --days 14                # override how many daily lessons to write
./run run --model claude-sonnet-4-6 # one-off model override
```

---

## Supported file types

| Type    | Notes                                                            |
| ------- | ---------------------------------------------------------------- |
| `.pdf`  | Textbook chapters, past exams, homework. bart extracts the text. |
| `.docx` | Word documents (paragraphs and tables).                          |
| `.pptx` | Lecture slides (text frames + tables).                           |
| `.md`   | Plain markdown notes.                                            |
| `.txt`  | Anything else plain text.                                        |

Unsupported file types are skipped with a clear message.

---

## How long does it take?

Total time scales with: number of days until exam × model speed × critic on/off × auth mode.

| Configuration                                   | 7-day plan | 30-day plan |
| ----------------------------------------------- | ---------- | ----------- |
| `--fast` (Sonnet, no critic, parallel 8)        | ~5 min     | ~15 min     |
| API mode, Opus, no critic                       | ~10 min    | ~30 min     |
| API mode, Opus, with critic+revise              | ~15 min    | ~50 min     |
| Subscription mode, Opus, with critic+revise     | ~25 min    | ~90 min     |

Subscription mode is slower than API mode because there's no prompt caching — the corpus is re-processed on every call. If runtime matters, use `--fast` or set `primary_model` to `claude-sonnet-4-6` in your config. Quality is still excellent and the runtime drops dramatically.

Subsequent runs skip the venv build, and the disk cache makes repeat runs over the same materials nearly instant.

---

## Cost

A 7-day plan with the full critic-revise loop costs roughly **$5–$15** in API tokens. To spend less:

- `--no-critic` ≈ half cost
- Edit `.bart_config.json` and set `primary_model` to `claude-sonnet-4-6` ≈ another 5× cheaper

The disk cache means re-runs over the same materials are basically free. bart prints the exact cost when it finishes.

---

## Auth: API key *or* Claude subscription

bart supports two ways to talk to Claude:

**1. Claude Code subscription** *(recommended if you have Pro/Max/Team)*. If the `claude` CLI from [claude.ai/code](https://claude.ai/code) is on your PATH and you've logged in once, bart can route every call through it. No API key needed; usage is covered by your existing subscription. The setup wizard auto-detects this and offers it as the first option.

**2. Anthropic API key**. Get one at [console.anthropic.com](https://console.anthropic.com) (free credits on signup). Pay-per-token, but enables prompt caching and exact cost tracking. The wizard saves the key to `.bart_config.json` (mode `0600`, stays on your machine).

| | subscription mode | API mode |
|---|---|---|
| Per-run cost | $0 (covered by subscription) | $5–$15 typical |
| Setup | already done if `claude` is logged in | paste an API key once |
| Prompt caching | no (subscription tier handles speed) | yes |
| Cost telemetry | not tracked | exact USD per call |
| Rate limits | subscription tier | API tier |

You can switch between modes anytime with `./run setup`.

---

## Troubleshooting

**Interrupted mid-run.** Run `./run run --resume <run_id>` (use `./run list` to see ids). Completed artifacts are skipped; only the unfinished bits are regenerated.

**API errors.** bart retries with exponential backoff up to four times. After that, the error is logged to `output/<run_id>/run.log` and the run continues with what it has.

**A PDF extracts as garbage.** It's probably a scanned image. OCR it first (e.g. with `ocrmypdf`) and re-drop.

**A file was skipped.** The reason is printed alongside the skip. Usually unsupported extension or empty file.

**Anything else.** `./run doctor` checks Python, deps, your API key, and the materials folder.

---

## Configuration

Stored in `.bart_config.json`. Edit directly or rerun `./run setup`:

```json
{
  "api_key": "sk-ant-…",
  "exam_date": "2026-05-10",
  "subject": "Organic Chemistry II",
  "guidance": "weak on reaction mechanisms. 3 hrs/day available.",
  "student_level": "undergraduate",
  "style": "academic-rigorous",
  "daily_hours": 3.0,
  "primary_model": "claude-opus-4-7",
  "fast_model": "claude-haiku-4-5-20251001"
}
```

Swap `style` for a different voice. Swap `primary_model` for cheaper output. Swap `student_level` for different difficulty calibration. Changes apply on the next run.

---

## Customizing what bart writes

Every agent's system prompt is a markdown file in `prompts/`. Edit, save, run again. No code changes.

```
prompts/
├── planner.md       — how to build the day-by-day plan
├── researcher.md    — how to surface relevant corpus bits
├── author.md        — how to write the lessons
├── critic.md        — how to grade lessons
└── reviser.md       — how to apply critic feedback
```

---

## Project layout

```
bart/
├── run                  — bash launcher
├── requirements.txt
├── README.md
├── assets/              — SVG + ASCII brand assets
│   ├── bart-loaf.svg
│   ├── bart-wordmark.svg
│   └── bart-loaf.txt
├── bart/                — python package
│   ├── __main__.py      — CLI dispatcher
│   ├── branding.py      — color tokens + ASCII splash printer
│   ├── config.py        — pydantic config + setup wizard
│   ├── orchestrator.py  — pipeline orchestration
│   ├── llm.py           — API wrapper: caching, retries, telemetry
│   ├── telemetry.py     — token + cost accounting
│   ├── agents/          — planner / researcher / author / critic / reviser
│   ├── extractors/      — pdf / docx / pptx / text
│   └── io/              — corpus assembly + atomic writes
├── prompts/             — editable agent prompts
├── tests/               — pytest smoke tests
├── materials/           — drop course materials here
└── output/              — generated packets land here
```

Roughly 1800 lines of Python. Every module has one job.

---

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/
```

Smoke tests cover corpus assembly and planner JSON parsing.

---

## Philosophy

1. **One command should do everything.** If you have to read a doc, the UX failed.
2. **Show the cost.** Every run prints exactly what it spent.
3. **Don't make things up.** If the corpus doesn't say it, bart doesn't say it. (And if a lesson does, the critic catches it.)

---

## Credits

Built with [Anthropic Claude](https://anthropic.com), [pypdf](https://pypdf.readthedocs.io), [python-docx](https://python-docx.readthedocs.io), [python-pptx](https://python-pptx.readthedocs.io), [Rich](https://github.com/Textualize/rich), and [Pydantic](https://pydantic.dev).

For students who would rather *learn* the material than spend three hours organizing their notes into a study schedule.

---

<p align="center">
  <img src="assets/bart-loaf.svg" alt="bart" width="80" /><br/>
  <sub><img src="assets/bart-wordmark.svg" alt="bart." height="20" /></sub>
</p>
