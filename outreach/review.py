"""The review queue — the human gate between generation and publishing.

Every dated item carries a `status.json` whose `state` walks a small,
enforced machine:

    draft ──approve──▶ approved ──publish──▶ published
      │                   │
      └────reject────┐    └────reject────┐
                     ▼                   ▼
                  rejected            rejected

`publish.py` refuses to touch anything not in `approved`, so the gate
cannot be skipped with a flag.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List

from pydantic import BaseModel, Field

from .paths import ItemPaths, QUEUE

STATES = ("draft", "approved", "rejected", "published")

_ALLOWED = {
    "draft": {"approved", "rejected"},
    "approved": {"published", "rejected"},
    "rejected": set(),
    "published": set(),
}


class InvalidTransition(RuntimeError):
    pass


class Status(BaseModel):
    date: str
    state: str = "draft"
    media_id: str = ""
    permalink: str = ""
    history: List[str] = Field(default_factory=list)

    def _log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        self.history.append(f"{ts}  {msg}")

    def transition(self, new_state: str, note: str = "") -> None:
        if new_state not in STATES:
            raise InvalidTransition(f"unknown state {new_state!r}")
        if new_state not in _ALLOWED.get(self.state, set()):
            raise InvalidTransition(
                f"cannot move from '{self.state}' to '{new_state}'"
            )
        self._log(f"{self.state} -> {new_state}" + (f"  ({note})" if note else ""))
        self.state = new_state


def read_status(item: ItemPaths) -> Status:
    if not item.status_path.exists():
        return Status(date=item.date)
    return Status(**json.loads(item.status_path.read_text()))


def write_status(item: ItemPaths, status: Status) -> None:
    item.ensure()
    item.status_path.write_text(status.model_dump_json(indent=2))


def init_item(date: str) -> ItemPaths:
    """Create (or return) a draft queue item for `date`."""
    item = ItemPaths.for_date(date).ensure()
    if not item.status_path.exists():
        st = Status(date=date)
        st._log("created (draft)")
        write_status(item, st)
    return item


def list_items() -> List[Status]:
    if not QUEUE.exists():
        return []
    out: List[Status] = []
    for d in sorted(QUEUE.iterdir()):
        if d.is_dir() and (d / "status.json").exists():
            out.append(read_status(ItemPaths.for_date(d.name)))
    return out


def set_state(date: str, new_state: str, note: str = "") -> Status:
    """Apply a validated state transition and persist it."""
    item = ItemPaths.for_date(date)
    if not item.status_path.exists():
        raise FileNotFoundError(f"no queue item for {date}")
    status = read_status(item)
    status.transition(new_state, note)
    write_status(item, status)
    return status
