# Practice Exam — {subject}

_Time limit: as the corpus suggests; otherwise plan for a full sitting._

Mirror the structure of any exam visible in the corpus. Cover topics in proportion to TOPIC_WEIGHTS.

## Section structure

A short markdown list describing the parts (e.g., "Part I: 20 short × 1 pt, Part II: ..."), inferred from the corpus shape.

## Problems

Emit each problem as a markdown subsection (`### P1 — <topic>`, `### P2 — ...`) with stable IDs.

For non-trivial problems, prefer rich blocks:
- A multi-part problem → `bart-multi-step` with hints (the hints are FOR THE STUDENT studying after — they'll be hidden during a real attempt).
- A formula-derivation problem → `bart-build-equation` (drag-and-drop tokens).
- A multiple-choice problem → `bart-multiple-choice`.
- An ordering problem → `bart-drag-order`.
- A "match these" problem → `bart-match-pairs`.
- An estimation problem → `bart-estimate-range`.

Plain-text problems are still valid for short-answer or essay-style questions — use markdown for those.

Every problem is NEW — inspired by, not copied from, corpus problems.

Do NOT include solutions in this file. The Solver agent emits the answer key as a separate stage.
