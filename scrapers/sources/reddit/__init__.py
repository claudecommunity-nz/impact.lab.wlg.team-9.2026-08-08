"""Synthetic Reddit corpus collector, replayed on a shifted clock.

The corpus is historical, so querying it with today's timestamps returns
nothing. `common.simclock` shifts the clock back by a fixed interval, and this
asks the API for the last hour of *simulated* time. Because the offset is an
interval rather than a fixed date, leaving the scraper running walks forward
through the April event at real-time speed.

Two things this is careful about, both about not lying:

  * Every signal carries `published_at` mapped back onto the real clock, so it
    behaves like a live item in the map's time filters — and carries the true
    corpus timestamp in `raw.corpus_published_at` alongside it, so the real
    time is never lost.
  * The source is named as a replay and every item is flagged `synthetic`, the
    same treatment the fixture collector gets. A corpus replay dressed as live
    Reddit is exactly the failure mode this whole prototype is meant to avoid.

The API needs an X-API-Key header. Without REDDIT_API_KEY set, the collector
reports itself as skipped rather than failing — an unconfigured source is not
a broken one.
"""

import logging
import os

import requests

from common.relevance import looks_relevant
from common.telemetry import record_target
from common.text import clean_text, parse_time

log = logging.getLogger(__name__)

BASE_URL = os.getenv("REDDIT_API_URL", "https://responding-bull-weapon-friendship.trycloudflare.com").rstrip("/")
API_KEY = os.getenv("REDDIT_API_KEY", "")

# `q` is required by /v1/search, so "everything recent" is expressed as a set
# of hazard terms rather than one empty query. One request per term, deduped on
# the way in — which also gives the dashboard a per-term hit count, so a term
# that never matches anything is visible instead of silently wasting a call.
QUERIES = [
    q.strip() for q in os.getenv(
        "REDDIT_QUERIES",
        "flood,slip,landslide,road closed,power cut,outage,storm,wind,evacuate,"
        "earthquake,water,emergency,damage,rain",
    ).split(",") if q.strip()
]

# The corpus covers 25 subreddits including Auckland and Christchurch. Narrowed
# here rather than filtered later, so the request does the work instead of the
# relevance filter throwing most of it away.
SUBREDDITS = [
    s.strip() for s in os.getenv("REDDIT_SUBREDDITS", "wellington,newzealand").split(",") if s.strip()
]

# post | comment | all. Comments outnumber posts roughly six to one and carry
# most of the on-the-ground detail — "water over the road at X" is usually a
# reply, not a submission — so they are worth having.
KIND = os.getenv("REDDIT_KIND", "all")

WINDOW_MINUTES = int(os.getenv("REDDIT_WINDOW_MINUTES", "60"))
LIMIT = int(os.getenv("REDDIT_LIMIT", "50"))
TIMEOUT = int(os.getenv("REDDIT_TIMEOUT", "30"))

# Subreddits that are inherently Wellington. A post in r/wellington saying
# "big slip on the road" has no place name in it and would otherwise be thrown
# away by the region-term half of the relevance filter.
LOCAL_SUBREDDITS = {"wellington"}


def describe() -> dict:
    from common import simclock

    clock = simclock.describe()
    return {
        "summary": (
            f"Synthetic Reddit corpus · {clock['summary']} · "
            f"last {WINDOW_MINUTES} min of simulated time · {len(QUERIES)} query terms"
        ),
        "endpoint": f"{BASE_URL}/v1/search",
        "configured": bool(API_KEY),
        "queries": QUERIES,
        "subreddits": SUBREDDITS or ["(all)"],
        **clock,
    }


def _search(query: str, start, end) -> list[dict]:
    params = {
        "q": query,
        "kind": KIND,
        # The API takes naive timestamps; it rejects the trailing Z.
        "start": start.replace(tzinfo=None).isoformat(timespec="seconds"),
        "end": end.replace(tzinfo=None).isoformat(timespec="seconds"),
        "limit": LIMIT,
        "sort": "new",
    }
    if SUBREDDITS:
        params["subreddit"] = SUBREDDITS

    r = requests.get(
        f"{BASE_URL}/v1/search",
        params=params,
        headers={"X-API-Key": API_KEY},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def map_post(item: dict) -> dict | None:
    """Turn one corpus record into a signal.

    Field names are the API's actual ones, confirmed against a live response:
    id, kind, subreddit, created_at, title, text, permalink, score. Comments
    carry an empty title, which is why the two are joined rather than assumed.
    """
    from common import simclock

    title = clean_text(item.get("title") or "")
    body = clean_text(item.get("text") or "")
    text = f"{title}. {body}".strip(". ").strip()
    if not text:
        return None

    subreddit = (item.get("subreddit") or "unknown").lower()
    keep, reasons = looks_relevant(text, local=subreddit in LOCAL_SUBREDDITS)
    if not keep:
        return None

    # Naive timestamps in the corpus; Reddit records UTC, so read them as UTC.
    corpus_time = parse_time(item.get("created_at"))
    post_id = item.get("id")

    return {
        "source": {
            "type": "reddit",
            # Named as a replay wherever it appears. No account handles are
            # stored — the subreddit is the finest attribution kept.
            "name": f"Reddit r/{subreddit} (synthetic corpus replay)",
            "collector": "reddit",
            "url": f"{BASE_URL}/v1/search",
            "local": False,
        },
        "title": title or None,
        "text": text,
        "url": item.get("permalink"),
        "external_id": f"reddit-{post_id}" if post_id else None,
        # Mapped onto the real clock so it behaves like a live signal in the
        # map's time filters. The true corpus time is kept below.
        "published_at": simclock.to_real(corpus_time) if corpus_time else None,
        "raw": {
            "synthetic": True,
            "note": "Replayed from a synthetic Reddit corpus on a shifted clock. Not a live post.",
            "corpus_published_at": corpus_time.isoformat() if corpus_time else None,
            "subreddit": subreddit,
            "kind": item.get("kind"),
            "score": item.get("score"),
            "filter": reasons,
        },
    }


def collect() -> list[dict]:
    from common import simclock

    if not API_KEY:
        log.warning("REDDIT_API_KEY is not set — skipping the Reddit corpus")
        record_target(
            "Reddit corpus",
            BASE_URL,
            status="skipped",
            detail="REDDIT_API_KEY not set",
        )
        return []

    start, end = simclock.sim_window(WINDOW_MINUTES)
    log.info("reddit: simulated window %s → %s", start.isoformat(), end.isoformat())

    seen: set = set()
    signals: list[dict] = []

    for query in QUERIES:
        try:
            items = _search(query, start, end)
        except (requests.RequestException, ValueError) as exc:
            log.warning("reddit search %r failed: %s", query, exc)
            record_target(f"q={query}", None, status="error", detail=str(exc)[:160])
            continue

        kept = 0
        for item in items:
            # Terms overlap heavily — a flood post matches "flood", "rain" and
            # "water" — so the same item comes back from several queries.
            key = item.get("id")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            signal = map_post(item)
            if signal:
                signals.append(signal)
                kept += 1

        record_target(
            f"q={query}",
            None,
            fetched=len(items),
            kept=kept,
            status="ok" if items else "empty",
            detail=f"{kept} of {len(items)} kept",
        )

    record_target(
        "simulated window",
        None,
        status="ok",
        detail=f"{start:%d %b %H:%M} → {end:%d %b %H:%M} UTC",
    )
    log.info("reddit: %d relevant posts from %d queries", len(signals), len(QUERIES))
    return signals
