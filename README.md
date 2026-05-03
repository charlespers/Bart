<p align="center">
  <img src="assets/readme.png" alt="bart. — a friendly cream-colored loaf next to the bart wordmark, with the tagline 'Drop your course materials in. Run once.' and chips reading: agentic pipeline · prompt-cached · resumable · rubric-graded · parallel" width="100%" />
</p>

> Drop your course materials in. Run once. Get a personalized exam-prep packet engineered to Ivy-undergraduate standards.

---

**TL;DR:** bart reads your course materials and writes you a personalized exam-prep packet — master plan, day-by-day lessons, schematics, mnemonics, a full practice exam, and a 60-minute panic guide. It's like having a TA who pulled an all-nighter just for you, except the TA is five Claude agents working in tandem.

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
   you ──▶  drop pdfs in ./materials   ──▶  ./run   ──▶  open ./output  ──▶  ace exam
```

That's it. That's the whole thing.

---

## Try it locally first (no API tokens spent)

Want to verify bart is wired up before you commit to a real run? Drop the included sample fixture and dry-run:

```bash
cd bart
cp examples/sample_notes.md materials/
./run run --dry-run
```

This bootstraps the venv, runs the wizard once (you can put any dummy values), extracts the sample, builds the corpus, and stops *before* hitting the API. If you see the bart splash and a corpus summary with no errors, you're good to go.

You can also run `./run doctor` to verify Python, deps, and your API key.

---

## 60-second start

```bash
cd bart
cp ~/your-class-notes/*.pdf materials/
./run
```

First run does the boring stuff for you:

- Builds a Python venv. (You don't need to think about it.)
- Installs deps. (You don't need to think about it.)
- Asks for your API key, exam date, and what you're worried about. (One time, takes 30 seconds.)
- Grinds for ~10 minutes while you make coffee.
- Drops a beautifully organized study packet in `output/`.

Re-runs skip the wizard and just go.

<p align="center">
  <img src="assets/readme2.png" alt="terminal screenshot of bart starting up — the loaf in ASCII, the bart. wordmark, and status lines for scanning materials, extracting corpus, and planning the schedule" width="100%" />
</p>

<sub align="center"><i>what you see when you run <code>./run</code> — bart says hi, scans your materials, and gets to work.</i></sub>

---

## What you get

After one run, `output/run_<timestamp>/` looks like this:

```
📂 output/run_2026-05-03_141503/
├── 📕 README.md                  ← you are here
├── 🗺️  00_MASTER_PLAN.md          ← "what to study, on what day, why"
├── 📐 01_SCHEMATICS.md           ← every diagram, formula table, and trap, in one place
├── 🎭 02_WHIMSICAL_NOTES.md      ← mnemonics, jingles, silly analogies that actually stick
├── 🚨 03_SHORT_STUDY_GUIDE.md    ← the "I have 60 minutes" version
├── 📝 04_PRACTICE_EXAM.md        ← full mock exam + answer key
└── 📂 daily_lessons/
    ├── Day_01_2026-05-04.md      ← 1000+ lines of dense, source-grounded study
    ├── Day_02_2026-05-05.md
    └── … one per day until exam day
```

Every file is markdown. Open in your editor of choice, your browser, or print and annotate.

---

## Wait, how is this different from just asking ChatGPT?

Glad you asked. Here's the secret sauce:

**Five agents, one packet.** Each agent has one job and does it well:

```
        ┌─────────────────┐
        │  📚 your stuff  │
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │   🧠 Planner    │  reads everything, writes the master plan
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │  🔍 Researcher  │  for each day, finds the relevant bits
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │   ✍️  Author     │  writes the lesson (long, dense, real)
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │  🔬 Critic      │  grades it 0–100, demands fixes
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │  🛠️  Reviser     │  fixes anything that scored < 80
        └────────┬────────┘
                 │
              📦 packet
```

Yes — your lessons are graded by a critic agent before you see them. If a lesson is mid, it gets rewritten. You never see the bad draft.

**Source-grounded.** Bart cites _your actual notes_ — your textbook's notation, your past exams' problem numbers, your homework's vocabulary. No generic "this concept is widely used in the field 🤖" filler.

**Practice-heavy.** Every lesson ends with embedded ✏️ Quick Checks (with collapsible answers), worked examples, and 8–12 drill problems. The practice exam is a serious 3-hour mock with a real answer key.

---

## Commands

You'll mostly just type `./run`. But here are the rest:

```bash
./run                       # generate a packet (this is the main thing)
./run setup                 # change your API key, exam date, etc.
./run list                  # show every packet you've ever generated, with cost
./run doctor                # "is everything okay?" — runs a quick health check
./run run --resume <id>     # picked back up from a crashed run
./run run --dry-run         # extract + plan, but don't burn tokens
./run run --no-critic       # skip grading (faster, cheaper, slightly worse)
./run run --max-parallel 8  # generate more lessons at once
./run run --days 14         # override how many daily lessons to write
```

---

## Supported file types

| Type    | Notes                                                            |
| ------- | ---------------------------------------------------------------- |
| `.pdf`  | Textbook chapters, past exams, homework. Bart extracts the text. |
| `.docx` | Word documents (paragraphs and tables).                          |
| `.pptx` | Lecture slides (text frames + tables).                           |
| `.md`   | Your own notes.                                                  |
| `.txt`  | Anything else plain text.                                        |

Anything not on this list gets skipped with a friendly message. (Sorry, no `.heic`.)

---

## How long does it take?

| Setup         | What's happening                         | Time      |
| ------------- | ---------------------------------------- | --------- |
| First `./run` | Build venv, install deps                 | ~60 sec   |
| The wizard    | Ask you 6 questions                      | ~30 sec   |
| The pipeline  | Plan → research → write → grade → revise | ~5–15 min |

Subsequent runs skip the venv build. Pipeline time scales with the number of days until your exam.

---

## How much does it cost?

A 7-day plan with the full critic-revise loop costs roughly **$5–$15** in API tokens. Knobs to spend less:

- `--no-critic` ≈ half cost
- Edit `.bart_config.json` and set `primary_model` to `claude-sonnet-4-6` ≈ another 5× cheaper

The disk cache means re-runs over the same materials are basically free. Bart will tell you the exact cost when it finishes.

---

## What if I don't have an Anthropic API key?

Get one at [console.anthropic.com](https://console.anthropic.com). Free credits when you sign up. Bart will ask once and remember it (saved to `.bart_config.json`, mode `0600`, never leaves your machine).

---

## What if it breaks?

**It's interrupted mid-run.** No problem. Run `./run run --resume <run_id>` (use `./run list` to see ids). Already-finished artifacts are skipped; only the unfinished bits are regenerated.

**API errors.** Bart retries with exponential backoff up to 4 times. After that, the run logs the error and continues with what it has. Look at `output/<run_id>/run.log` for details.

**A PDF extracts as garbage.** It's probably a scanned image. OCR it first with `ocrmypdf` (or anything similar) and re-drop.

**Bart told you a file was skipped.** It listed the reason. Usually it's an unsupported extension or an empty file.

**Something weirder.** Run `./run doctor` — it checks Python, deps, your API key, and your materials folder, and tells you what's wrong.

---

## Configuration

Stored in `.bart_config.json`. You can edit it directly or rerun `./run setup`:

```json
{
  "api_key": "sk-ant-…",
  "exam_date": "2026-05-10",
  "subject": "Organic Chemistry II",
  "guidance": "I'm weak on reaction mechanisms. I have 3 hrs/day.",
  "student_level": "undergraduate",
  "style": "academic-rigorous",
  "daily_hours": 3.0,
  "primary_model": "claude-opus-4-7",
  "fast_model": "claude-haiku-4-5-20251001"
}
```

Want a different vibe? Swap `style`. Want it cheaper? Swap `primary_model`. Want lower-stakes language? Swap `student_level` to `aplevel`. Anything you change here gets used on the next run.

---

## Customizing what bart writes

Every agent's system prompt is a markdown file in `prompts/`. Open one. Edit it. Save it. The next run uses it. No code changes needed.

```
prompts/
├── planner.md       ← how to build the day-by-day plan
├── researcher.md    ← how to surface relevant corpus bits
├── author.md        ← how to write the lessons
├── critic.md        ← how to grade lessons
└── reviser.md       ← how to apply critic feedback
```

If you want all your lessons to read like a stand-up routine, edit `author.md` accordingly. (We don't recommend it the night before an exam.)

---

## Project layout (for the curious)

```
bart/
├── run                  ← the bash launcher (you only ever run this)
├── requirements.txt
├── README.md
├── assets/              ← the loaf — SVG + ASCII brand assets
│   ├── bart-loaf.svg
│   ├── bart-wordmark.svg
│   └── bart-loaf.txt
├── bart/                ← the python package
│   ├── __main__.py      ← CLI dispatcher
│   ├── branding.py      ← color tokens + ASCII splash printer
│   ├── config.py        ← pydantic config + setup wizard
│   ├── orchestrator.py  ← the brain (well-commented)
│   ├── llm.py           ← API wrapper: caching, retries, telemetry
│   ├── telemetry.py     ← token + cost accounting
│   ├── agents/          ← planner / researcher / author / critic / reviser
│   ├── extractors/      ← pdf / docx / pptx / text
│   └── io/              ← corpus assembly + atomic writes
├── prompts/             ← editable agent prompts
├── tests/               ← pytest smoke tests
├── materials/           ← ⬅ drop your stuff here
└── output/              ← ⬅ packets land here (each with its own loaf)
```

Roughly 1800 lines of Python. Every module has one job.

---

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/
```

Smoke tests cover corpus assembly and planner JSON parsing. PRs welcome.

---

## Philosophy

Three rules:

1. **One command should do everything.** If you have to read a doc, the UX failed.
2. **Show the cost.** Every run prints exactly what it spent. No surprises.
3. **Don't make stuff up.** If the corpus doesn't say it, bart doesn't say it. (And if a lesson does, the critic catches it.)

---

## Credits

Built with [Anthropic Claude](https://anthropic.com), [pypdf](https://pypdf.readthedocs.io), [python-docx](https://python-docx.readthedocs.io), [python-pptx](https://python-pptx.readthedocs.io), [Rich](https://github.com/Textualize/rich), [Pydantic](https://pydantic.dev), and several cups of coffee.

Made for students who would rather _learn_ the material than spend three hours organizing their notes into a study schedule.

**Now go drop those PDFs.** ⬇️

---

<p align="center">
  <img src="assets/bart-loaf.svg" alt="bart" width="80" /><br/>
  <sub><img src="assets/bart-wordmark.svg" alt="bart." height="20" /></sub>
</p>
