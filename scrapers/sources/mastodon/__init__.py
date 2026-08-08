"""Mastodon collector — public posts, no account, no API key.

Public hashtag and local timelines are readable unauthenticated on most
instances, which makes Mastodon the one social platform that can be
demonstrated honestly at a hackathon without credentials or scraping terms
being bent. Instances that have closed public timelines return 401/404; those
are logged and skipped rather than treated as an error.

This is the least reliable source in the pipeline and the interface should say
so — it is exactly the kind of content the problem statement warns against
presenting as fact.
"""

import logging
import os

import requests

from common.relevance import looks_relevant
from common.text import clean_text, parse_time

log = logging.getLogger(__name__)

INSTANCE = os.getenv("MASTODON_INSTANCE", "mastodon.nz")
TAGS = [t.strip() for t in os.getenv("MASTODON_TAGS", "wellington,wgtn,nzwx").split(",") if t.strip()]
INCLUDE_LOCAL_TIMELINE = os.getenv("MASTODON_LOCAL_TIMELINE", "true").lower() == "true"
LIMIT = int(os.getenv("MASTODON_LIMIT", "40"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "impact-lab-team9/0.1 (Wellington emergency signals prototype; hackathon)",
)


def _fetch(path: str, params: dict) -> list[dict]:
    url = f"https://{INSTANCE}{path}"
    try:
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code in (401, 403, 404):
            log.info("%s not publicly readable on %s (%d)", path, INSTANCE, r.status_code)
            return []
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("mastodon %s failed: %s", path, exc)
        return []


def _media_items(status: dict) -> list[dict]:
    """Mastodon attachment types: image, video, gifv, audio, unknown. Only the
    first two render as a static picture or a playable clip — the rest are
    dropped rather than shown as a broken thumbnail."""
    kind_map = {"image": "image", "video": "video", "gifv": "video"}
    items = []
    for att in status.get("media_attachments") or []:
        kind = kind_map.get(att.get("type"))
        url = att.get("url") or att.get("preview_url")
        if kind and url:
            items.append({"type": kind, "url": url})
    return items


def _to_signal(status: dict, origin: str) -> dict | None:
    text = clean_text(status.get("content"))
    if not text:
        return None

    # The instance being NZ-local doesn't make a post Wellington-local, so a
    # hashtag match still has to clear the region+hazard filter.
    keep, reasons = looks_relevant(text, local=False)
    if not keep:
        return None

    account = status.get("account", {}) or {}
    return {
        "source": {
            "type": "mastodon",
            # Instance-level attribution only — no account handle, display name
            # or avatar is stored as a field. The post's own permalink is kept,
            # because an analyst has to be able to read the original before
            # acting on it, and that URL does contain the author's handle. That
            # is the minimum needed to make a signal checkable; anything more
            # would be building a profile.
            "name": f"Mastodon · {INSTANCE}",
            "collector": "mastodon",
            "url": f"https://{INSTANCE}",
            "local": False,
        },
        "title": None,
        "text": text,
        "url": status.get("url"),
        "external_id": status.get("uri") or status.get("id"),
        "published_at": parse_time(status.get("created_at")),
        "media": _media_items(status),
        "raw": {
            "origin": origin,
            "filter": reasons,
            "boosts": status.get("reblogs_count"),
            "replies": status.get("replies_count"),
            "has_media": bool(status.get("media_attachments")),
            "account_is_bot": bool(account.get("bot")),
        },
    }


def collect() -> list[dict]:
    seen: set[str] = set()
    signals: list[dict] = []

    endpoints = [(f"/api/v1/timelines/tag/{tag}", f"#{tag}") for tag in TAGS]
    if INCLUDE_LOCAL_TIMELINE:
        endpoints.append(("/api/v1/timelines/public", "local timeline"))

    for path, origin in endpoints:
        params = {"limit": LIMIT}
        if path.endswith("/public"):
            params["local"] = "true"
        for status in _fetch(path, params):
            key = status.get("uri") or status.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            signal = _to_signal(status, origin)
            if signal:
                signals.append(signal)

    log.info("mastodon: %d relevant posts from %d endpoints", len(signals), len(endpoints))
    return signals
