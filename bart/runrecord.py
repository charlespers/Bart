"""Structured per-run outcome + warning record.

A tiny, dependency-free bookkeeper the orchestrator threads through a run so
that — at the end — the run knows what went sideways and the process can
exit with a meaningful code, and the summary can show the user the whole
picture instead of only the metrics table.

Two kinds of facts:

  * **Artifact outcomes** — for every thing the run is supposed to produce
    (top-level artifacts, daily lessons, sidecar indices), one of:
      - ``ok``        — produced cleanly
      - ``failed``    — not produced (a real hole in the packet)
      - ``recovered`` — produced, but only after a retry / re-ask
      - ``skipped``   — deliberately not produced (already present, opted out)
      - ``fallback``  — a sidecar that hit an error and used its empty fallback
  * **Warnings** — anything noteworthy that isn't an artifact outcome, with a
    severity:
      - ``info``  — a normalization happened (e.g. ``$x$`` → ``\\(x\\)``)
      - ``warn``  — a degradation (a sidecar fell back, an autofix did a lot)
      - ``error`` — a genuine problem (unsafe math survived the sanitizer)

`has_failures()` is true iff any artifact is ``failed``. `has_errors()` is
true iff any artifact is ``failed`` *or* any warning has ``severity ==
"error"`` — i.e. "the packet is incomplete or has an error-level finding".
`summary_lines()` returns the human list of everything that went sideways
(clean artifacts and a fully-clean run produce no lines).
"""
from __future__ import annotations

from dataclasses import dataclass, field

ARTIFACT_OUTCOMES = ("ok", "failed", "recovered", "skipped", "fallback")
WARNING_SEVERITIES = ("info", "warn", "error")


@dataclass(frozen=True)
class ArtifactOutcome:
    name: str
    outcome: str


@dataclass(frozen=True)
class Warning:
    severity: str
    source: str
    detail: str


@dataclass
class RunRecord:
    """Mutable collector of artifact outcomes + warnings for one run."""

    artifacts: list[ArtifactOutcome] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)

    # ── recording ────────────────────────────────────────────────────
    def record_artifact(self, name: str, outcome: str) -> None:
        if outcome not in ARTIFACT_OUTCOMES:
            raise ValueError(
                f"unknown artifact outcome {outcome!r}; "
                f"expected one of {ARTIFACT_OUTCOMES}"
            )
        self.artifacts.append(ArtifactOutcome(name=name, outcome=outcome))

    def record_warning(self, severity: str, source: str, detail: str) -> None:
        if severity not in WARNING_SEVERITIES:
            raise ValueError(
                f"unknown warning severity {severity!r}; "
                f"expected one of {WARNING_SEVERITIES}"
            )
        self.warnings.append(Warning(severity=severity, source=source, detail=detail))

    # ── queries ──────────────────────────────────────────────────────
    def failed_artifacts(self) -> list[str]:
        return [a.name for a in self.artifacts if a.outcome == "failed"]

    def has_failures(self) -> bool:
        return any(a.outcome == "failed" for a in self.artifacts)

    def has_errors(self) -> bool:
        if self.has_failures():
            return True
        return any(w.severity == "error" for w in self.warnings)

    # ── human-readable ───────────────────────────────────────────────
    def summary_lines(self) -> list[str]:
        """One line per thing that went sideways. Empty on a clean run."""
        lines: list[str] = []
        # Artifact outcomes worth showing — anything that isn't a plain `ok`.
        _label = {
            "failed": "FAILED — missing",
            "recovered": "recovered (needed a retry/re-ask)",
            "skipped": "skipped",
            "fallback": "fell back to empty",
        }
        for a in self.artifacts:
            if a.outcome == "ok":
                continue
            lines.append(f"{a.name}: {_label.get(a.outcome, a.outcome)}")
        # Warnings, in severity order so errors lead.
        _rank = {"error": 0, "warn": 1, "info": 2}
        for w in sorted(self.warnings, key=lambda x: _rank.get(x.severity, 3)):
            lines.append(f"[{w.severity}] {w.source}: {w.detail}")
        return lines
