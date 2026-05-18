"""Pull performance insights for published Reels via the Graph API.

Two scopes:
  - per-media:  reach, plays, likes, comments, saved, shares for each Reel
                the pipeline published (it knows their media ids).
  - account:    follower-relative reach/profile activity for the IG account.

Read-only. Used to learn what worked and when to post — never to drive
automated engagement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import requests

from .config import OutreachConfig
from .review import Status, list_items

# Metrics valid for a REELS media object in the current Graph API.
_REEL_METRICS = "reach,plays,likes,comments,saved,shares,total_interactions"


class InsightsError(RuntimeError):
    pass


@dataclass
class MediaInsight:
    date: str
    media_id: str
    permalink: str
    metrics: dict = field(default_factory=dict)
    error: str = ""


def _media_insight(cfg: OutreachConfig, status: Status) -> MediaInsight:
    out = MediaInsight(date=status.date, media_id=status.media_id,
                       permalink=status.permalink)
    resp = requests.get(
        f"{cfg.graph_base}/{status.media_id}/insights",
        params={"metric": _REEL_METRICS, "access_token": cfg.meta_access_token},
        timeout=30,
    )
    if resp.status_code != 200:
        try:
            out.error = resp.json().get("error", {}).get("message", resp.text[:160])
        except ValueError:
            out.error = resp.text[:160]
        return out
    for entry in resp.json().get("data", []):
        values = entry.get("values", [{}])
        out.metrics[entry["name"]] = values[0].get("value") if values else None
    return out


def gather_insights(cfg: OutreachConfig) -> List[MediaInsight]:
    """Insight rows for every Reel the pipeline has published."""
    if not (cfg.meta_access_token and cfg.ig_user_id):
        raise InsightsError("meta_access_token and ig_user_id must be set.")
    published = [s for s in list_items() if s.state == "published" and s.media_id]
    if not published:
        return []
    return [_media_insight(cfg, s) for s in published]


def best_posting_summary(insights: List[MediaInsight]) -> str:
    """A one-line, honest read of the data — no overclaiming on small N."""
    scored = [i for i in insights if not i.error and i.metrics.get("reach")]
    if len(scored) < 3:
        return ("Not enough published Reels yet to call a best posting time — "
                "publish a few more, then check back.")
    best = max(scored, key=lambda i: i.metrics.get("reach", 0) or 0)
    return (f"Highest-reach Reel so far: {best.date} "
            f"({best.metrics.get('reach')} accounts reached). "
            f"Keep posting at that time of day and compare.")
