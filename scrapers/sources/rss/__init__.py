"""RSS / Atom collector — local news and official warning feeds.

Feeds are configured, not hard-coded: set RSS_FEEDS to override the defaults.
Format is `Name|url|local` separated by `;`, where the third field is optional
and marks a publication that is inherently Wellington-focused (so relevance
filtering doesn't also require a place name in the text).

A feed that is unreachable or malformed is logged and skipped. One dead feed
must never take the collector down.
"""

import logging
import os

import feedparser

from common.relevance import looks_relevant
from common.telemetry import record_target
from common.text import clean_text, parse_time

log = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    ("RNZ National", "https://www.rnz.co.nz/rss/national.xml", False),
    ("RNZ Top Stories", "https://www.rnz.co.nz/rss/top.xml", False),
    ("MetService Severe Weather Warnings", "https://alerts.metservice.com/cap/rss", False),
    ("Wellington.Scoop", "https://wellington.scoop.co.nz/?feed=rss2", True),
    ("NZ Herald — New Zealand", "https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/nz/?outputType=xml", False),
    ("NZ Police — Wellington District", "https://www.police.govt.nz/rss/district-news/wellington", True),
]

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; impact-lab-team9 hackathon)",
)


def _configured_feeds() -> list[tuple[str, str, bool]]:
    raw = os.getenv("RSS_FEEDS", "").strip()
    if not raw:
        return DEFAULT_FEEDS
    feeds = []
    for entry in raw.split(";"):
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) < 2 or not parts[1]:
            continue
        local = len(parts) > 2 and parts[2].lower() in ("1", "true", "yes", "local")
        feeds.append((parts[0] or parts[1], parts[1], local))
    return feeds


def describe() -> dict:
    """What this collector is configured to poll — shown on the dashboard."""
    feeds = _configured_feeds()
    return {
        "summary": f"{len(feeds)} RSS/Atom feeds",
        "filter_mode": os.getenv("FILTER_MODE", "both"),
        "feeds": [{"name": n, "url": u, "local": l} for n, u, l in feeds],
    }


def collect() -> list[dict]:
    signals = []
    for name, url, local in _configured_feeds():
        try:
            parsed = feedparser.parse(url, agent=USER_AGENT)
        except Exception as exc:  # noqa: BLE001 — a bad feed must not stop the rest
            log.warning("feed %s failed: %s", name, exc)
            record_target(name, url, status="error", detail=str(exc)[:200])
            continue

        if parsed.bozo and not parsed.entries:
            reason = str(getattr(parsed, "bozo_exception", "?"))[:200]
            log.warning("feed %s unusable: %s", name, reason)
            record_target(name, url, fetched=0, status="error", detail=reason)
            continue

        kept = 0
        for entry in parsed.entries:
            title = clean_text(entry.get("title"))
            summary = clean_text(entry.get("summary") or entry.get("description"))
            text = f"{title}. {summary}".strip(". ").strip()
            if not text:
                continue

            keep, reasons = looks_relevant(text, local=local)
            if not keep:
                continue

            signals.append(
                {
                    "source": {
                        "type": "rss",
                        "name": name,
                        "collector": "rss",
                        "url": url,
                        "local": local,
                    },
                    "title": title or None,
                    "text": text,
                    "url": entry.get("link"),
                    "external_id": entry.get("id") or entry.get("link"),
                    "published_at": parse_time(
                        entry.get("published_parsed") or entry.get("updated_parsed")
                    ),
                    "raw": {"filter": reasons},
                }
            )
            kept += 1

        record_target(
            name,
            url,
            fetched=len(parsed.entries),
            kept=kept,
            status="ok" if parsed.entries else "empty",
            # The interesting number on the dashboard is not how many entries a
            # feed has, it is how many survived the relevance filter. A feed
            # returning 40 items and keeping 0 every cycle is either irrelevant
            # or the filter is wrong, and both are worth seeing.
            detail=f"{kept} of {len(parsed.entries)} passed the relevance filter",
        )
        log.info("rss %s: %d/%d entries kept", name, kept, len(parsed.entries))
    return signals
