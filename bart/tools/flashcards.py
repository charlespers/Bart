"""flashcards — Anki .apkg exporter.

Walks every daily lesson + the short study guide, extracts Quick Check
question/answer pairs and key flashcard items, builds an Anki deck.

The student double-clicks the resulting .apkg and gets a real spaced-repetition
deck in their Anki app. Anki is free, cross-platform, the gold standard.

Optional dependency: `genanki` (pip install genanki). If absent, we skip
gracefully — no crash.
"""
from __future__ import annotations

import re
from pathlib import Path


class FlashcardsTool:
    name = "flashcards"
    description = "Anki .apkg deck from Quick Checks + flashcard sections"

    def run(self, packet_dir: Path, manifest: dict):
        try:
            import genanki  # type: ignore
        except ImportError:
            from . import ToolResult
            return ToolResult(
                name=self.name,
                success=False,
                output_paths=[],
                detail="genanki not installed; run `.venv/bin/pip install genanki` to enable.",
            )

        from . import ToolResult

        cards = self._collect_cards(packet_dir)
        if not cards:
            return ToolResult(
                name=self.name, success=False, output_paths=[],
                detail="no flashcardable content found in the packet",
            )

        cfg = manifest.get("config", {}) or {}
        subject = cfg.get("subject", "Study Packet")
        deck_id = abs(hash(subject)) % (10 ** 10)
        model_id = abs(hash(subject + "-model")) % (10 ** 10)

        model = genanki.Model(
            model_id,
            "bart Q&A",
            fields=[{"name": "Question"}, {"name": "Answer"}, {"name": "Source"}],
            templates=[{
                "name": "Card 1",
                "qfmt": "{{Question}}<br><br><small>{{Source}}</small>",
                "afmt": "{{FrontSide}}<hr id=\"answer\">{{Answer}}",
            }],
            css=(
                ".card { font-family: Inter, sans-serif; font-size: 18px; "
                "background: #f4f1ea; color: #221f1b; padding: 24px; }"
                "small { color: #6d655a; font-family: monospace; font-size: 12px; }"
            ),
        )

        deck = genanki.Deck(deck_id, f"bart · {subject}")
        for card in cards:
            note = genanki.Note(
                model=model,
                fields=[card["question"], card["answer"], card["source"]],
            )
            deck.add_note(note)

        out_dir = packet_dir / "anki"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"{self._safe_filename(subject)}.apkg"
        genanki.Package(deck).write_to_file(str(out_path))

        return ToolResult(
            name=self.name,
            success=True,
            output_paths=[out_path],
            detail=f"{len(cards)} cards generated",
        )

    # ─── Card extraction ───────────────────────────────────────────────

    _BART_FENCE_RE = re.compile(
        r"(?ms)^```bart-([a-z0-9-]+)[^\n]*\n(.*?)```\s*$"
    )

    def _collect_cards(self, packet_dir: Path) -> list[dict]:
        cards: list[dict] = []

        # Walk markdown originals (preserved by the renderer)
        md_dir = packet_dir / "markdown"
        if not md_dir.exists():
            return cards

        import json as _json
        for md_path in md_dir.rglob("*.md"):
            text = md_path.read_text(encoding="utf-8", errors="replace")
            source_label = md_path.relative_to(md_dir).as_posix()

            # ── Pattern Z (preferred): bart-* structured blocks ──────
            # bart-checkpoint emits N front/back cards directly.
            # bart-quick-check is one Q/A pair.
            for m in self._BART_FENCE_RE.finditer(text):
                name, body = m.group(1), m.group(2).strip()
                if not body:
                    continue
                try:
                    payload = _json.loads(body)
                except _json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if name == "checkpoint":
                    for c in payload.get("cards", []) or []:
                        front = str(c.get("front", "")).strip()
                        back = str(c.get("back", "")).strip()
                        if front and back:
                            cards.append({
                                "question": self._html_safe(front),
                                "answer": self._html_safe(back),
                                "source": self._html_safe(source_label),
                            })
                elif name == "quick-check":
                    q = str(payload.get("question", "")).strip()
                    a = str(payload.get("answer", "")).strip()
                    if q and a:
                        cards.append({
                            "question": self._html_safe(q),
                            "answer": self._html_safe(a),
                            "source": self._html_safe(source_label),
                        })
                elif name == "multiple-choice":
                    q = str(payload.get("question", "")).strip()
                    correct = next(
                        (c for c in payload.get("choices", []) or []
                         if c.get("correct")),
                        None,
                    )
                    if q and correct:
                        ans = str(correct.get("text", "")).strip()
                        explain = str(correct.get("explanation", "")).strip()
                        full_answer = f"{ans}\n\n{explain}" if explain else ans
                        cards.append({
                            "question": self._html_safe(q),
                            "answer": self._html_safe(full_answer),
                            "source": self._html_safe(source_label),
                        })

            # ── Pattern A: <details><summary>Q</summary>A</details> (legacy) ──
            for m in re.finditer(
                r"<details[^>]*>\s*<summary>(.*?)</summary>\s*(.*?)</details>",
                text, re.DOTALL,
            ):
                summary = self._md_to_text(m.group(1))
                body = self._md_to_text(m.group(2))
                # Heuristic: if the surrounding context looks like a Quick Check
                # block, the question is the lines just before this details.
                start_idx = m.start()
                pre_window = text[max(0, start_idx - 600):start_idx]
                question = self._extract_question_above(pre_window)
                if not question:
                    question = summary  # fall back to summary text
                if len(body.strip()) < 3:
                    continue
                cards.append({
                    "question": self._html_safe(question),
                    "answer": self._html_safe(body),
                    "source": self._html_safe(source_label),
                })

            # Pattern B: bullet flashcards under "## flashcards" sections
            for fc_match in re.finditer(
                r"^#{2,4}\s*[^\n]*flashcard[^\n]*\n(.*?)(?=^#{1,4}\s|\Z)",
                text, re.MULTILINE | re.IGNORECASE | re.DOTALL,
            ):
                section = fc_match.group(1)
                # Ordered or unordered list items, possibly Q: A: format
                for line in re.finditer(r"^\s*(?:\d+\.|[-*])\s+(.+?)(?=\n\s*(?:\d+\.|[-*])|\Z)",
                                         section, re.MULTILINE | re.DOTALL):
                    item = line.group(1).strip()
                    # Try Q? A patterns
                    qa = re.match(r"(?P<q>.+?\?)\s+(?P<a>.+)", item, re.DOTALL)
                    if qa:
                        cards.append({
                            "question": self._html_safe(self._md_to_text(qa.group("q"))),
                            "answer": self._html_safe(self._md_to_text(qa.group("a"))),
                            "source": self._html_safe(source_label),
                        })

        # De-dup
        seen = set()
        unique: list[dict] = []
        for c in cards:
            key = (c["question"][:200].lower(), c["answer"][:200].lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        return unique

    @staticmethod
    def _extract_question_above(window: str) -> str:
        """Find the 'Quick Check' question immediately preceding a details block."""
        # Look backward for something like "Quick Check N", "What is X?", etc.
        # Take the last non-empty line/paragraph before the details.
        lines = [l.strip() for l in window.splitlines() if l.strip()]
        if not lines:
            return ""
        # Take last 1-2 substantive lines; prefer one ending with ?
        for line in reversed(lines):
            if "?" in line and len(line) < 400:
                return line
        return lines[-1] if lines else ""

    @staticmethod
    def _md_to_text(s: str) -> str:
        # Strip simple markdown to plain text for Anki front/back fields.
        s = re.sub(r"`([^`]+)`", r"\1", s)        # inline code -> text
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)    # bold
        s = re.sub(r"\*(.+?)\*", r"\1", s)        # italic
        s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)
        s = re.sub(r"<[^>]+>", "", s)
        return s.strip()

    @staticmethod
    def _html_safe(s: str) -> str:
        # Anki cards render as HTML — escape and convert newlines.
        s = (s.replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;"))
        s = s.replace("\n\n", "<br><br>").replace("\n", "<br>")
        return s

    @staticmethod
    def _safe_filename(s: str) -> str:
        return re.sub(r"[^\w\-]+", "_", s).strip("_") or "deck"
