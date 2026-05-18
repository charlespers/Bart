"""Publish an approved Reel via the official Instagram Graph API.

Reels are published in three steps (Meta's documented flow):

  1. POST /{ig-user-id}/media       media_type=REELS, video_url, caption
                                    -> a creation (container) id
  2. GET  /{creation-id}            poll status_code until FINISHED
  3. POST /{ig-user-id}/media_publish  creation_id -> the published media id

The Graph API ingests video from a **public HTTPS URL** — it does not
accept a local file upload here. The rendered MP4 must therefore be
reachable at `{public_video_base_url}/{date}.mp4` (or a URL passed to
`publish`). No password is ever used; auth is the OAuth access token only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .config import OutreachConfig

_POLL_INTERVAL = 6        # seconds between container status checks
_POLL_TIMEOUT = 600       # give up after 10 minutes of processing


class PublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    media_id: str
    permalink: str


def _graph_error(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"[{err.get('code', resp.status_code)}] {err.get('message', resp.text[:200])}"
    except ValueError:
        return f"[{resp.status_code}] {resp.text[:200]}"


def verify_token(cfg: OutreachConfig) -> str:
    """Cheap liveness check used by `doctor`. Returns the account username."""
    if not (cfg.meta_access_token and cfg.ig_user_id):
        raise PublishError("meta_access_token and ig_user_id are not set.")
    resp = requests.get(
        f"{cfg.graph_base}/{cfg.ig_user_id}",
        params={"fields": "username", "access_token": cfg.meta_access_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PublishError(f"token/IG-id check failed: {_graph_error(resp)}")
    return resp.json().get("username", "(unknown)")


def video_url_for(cfg: OutreachConfig, date: str) -> str:
    if not cfg.public_video_base_url:
        raise PublishError(
            "public_video_base_url is not set, and no --video-url was given. "
            "The Graph API needs the MP4 at a public HTTPS URL."
        )
    return f"{cfg.public_video_base_url.rstrip('/')}/{date}.mp4"


def _create_container(cfg: OutreachConfig, video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{cfg.graph_base}/{cfg.ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": cfg.meta_access_token,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise PublishError(f"container creation failed: {_graph_error(resp)}")
    cid = resp.json().get("id")
    if not cid:
        raise PublishError(f"container creation returned no id: {resp.text[:200]}")
    return cid


def _await_container(cfg: OutreachConfig, container_id: str, log=print) -> None:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        resp = requests.get(
            f"{cfg.graph_base}/{container_id}",
            params={"fields": "status_code,status", "access_token": cfg.meta_access_token},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(f"status poll failed: {_graph_error(resp)}")
        code = resp.json().get("status_code", "")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(
                f"container {code}: {resp.json().get('status', 'no detail')}"
            )
        log(f"  container {code or 'IN_PROGRESS'} — waiting…")
        time.sleep(_POLL_INTERVAL)
    raise PublishError("container processing timed out after 10 minutes.")


def _publish_container(cfg: OutreachConfig, container_id: str) -> str:
    resp = requests.post(
        f"{cfg.graph_base}/{cfg.ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": cfg.meta_access_token},
        timeout=60,
    )
    if resp.status_code != 200:
        raise PublishError(f"media_publish failed: {_graph_error(resp)}")
    mid = resp.json().get("id")
    if not mid:
        raise PublishError(f"media_publish returned no id: {resp.text[:200]}")
    return mid


def _permalink(cfg: OutreachConfig, media_id: str) -> str:
    resp = requests.get(
        f"{cfg.graph_base}/{media_id}",
        params={"fields": "permalink", "access_token": cfg.meta_access_token},
        timeout=30,
    )
    return resp.json().get("permalink", "") if resp.status_code == 200 else ""


def publish_reel(
    cfg: OutreachConfig,
    video_url: str,
    caption: str,
    log=print,
) -> PublishResult:
    """Run the full REELS publish flow. Raises PublishError on any failure."""
    if not (cfg.meta_access_token and cfg.ig_user_id):
        raise PublishError("meta_access_token and ig_user_id must be set.")

    log(f"  creating REELS container for {video_url}")
    container_id = _create_container(cfg, video_url, caption)
    log(f"  container {container_id} created — Instagram is fetching the video")
    _await_container(cfg, container_id, log=log)
    log("  container FINISHED — publishing")
    media_id = _publish_container(cfg, container_id)
    permalink = _permalink(cfg, media_id)
    return PublishResult(media_id=media_id, permalink=permalink)
