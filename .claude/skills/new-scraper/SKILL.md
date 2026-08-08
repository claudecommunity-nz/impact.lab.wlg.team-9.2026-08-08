---
name: new-scraper
description: Scaffold a new signal collector for this pipeline — an RSS/Atom feed, or a JSON API such as the Reddit corpus. Use when asked to add a data source, add a scraper, pull in a new feed, or wire up another platform. Produces a working collector, a compose service, a test, and the deployment wiring.
---

# Adding a collector

A collector polls one public source on a timer and POSTs what it finds to the
ingestion API. It never touches the database. That is the whole contract, and
it is why a new source is a folder and a compose service rather than a change
to anything that already works.

## Before writing anything

**Check the source is actually public and stable.** Ask, in order:

1. Is there a documented feed or API? Prefer it over scraping HTML.
2. Does it need a key? If so it goes in Key Vault — never in the repo. See
   `deploy/azure/README.md`.
3. What does it cost the publisher? Council and operator endpoints are polled
   no faster than every 120 seconds in this repo. An undocumented backend
   behind a public status map is fair game to read, gently, and must be
   labelled as indicative rather than authoritative.
4. Does it carry personal information? Store the minimum that makes a signal
   checkable — a permalink, not a profile. `sources/mastodon` and
   `sources/reddit` both show the line: instance or subreddit for attribution,
   no account handles as fields.

If the platform has no viable public API — Facebook, Instagram, TikTok, X —
**do not scrape a logged-in feed**. `sources/screenshots` is the pattern:
a human submits a screen capture through `submit.html`, and it is graded
accordingly. That is an honest answer rather than a workaround.

## The contract

Each collector is `scrapers/sources/<name>/__init__.py` exposing:

```python
def collect() -> list[dict]:      # required — the signals found this run
def describe() -> dict:           # optional — config shown on the dashboard
```

`collect()` returns plain dicts matching `SignalIn` in
`ingestion/app/models.py`. Only `source` and `text` are required. Anything else
you can supply — a URL, a publish time, real coordinates — makes the enrichment
jobs' work easier, and none of it is mandatory.

Call `record_target()` per endpoint polled so the pipeline dashboard can show
what happened. A collector that skips it still works; it just reports no detail.

## Steps

1. **Write the collector** from the closest template below.
2. **Add a compose service** in `docker-compose.yml`, copying the shape of an
   existing `scraper-*` entry and setting `SOURCES: <name>`.
3. **Add it to the deployed source list** — the `SOURCES` value in
   `deploy/azure/aci.template.yaml`. Forgetting this is the classic mistake:
   it runs locally and silently never runs in Azure.
4. **Write a test** in `scrapers/tests/test_<name>.py` against a captured
   sample response. Do not hit the network in a test.
5. **Run it once** before wiring it into the loop:

   ```bash
   docker compose build scraper-rss
   docker compose run --rm --no-deps -T -e SOURCES=<name> scraper-rss \
     python -c "import sources; print(len(sources.load('<name>')()))"
   ```

6. **Check it end to end** — bring the stack up and look at
   `localhost:8080/#pipeline`. The card should show what it polled and how much
   survived the relevance filter. A collector returning 0 of 40 every cycle is
   either pointed at the wrong thing or being filtered wrongly, and both are
   visible there.

## Template — RSS or Atom

Most news, warning and community feeds. `sources/rss` already handles a
configurable list of them, so **prefer adding a feed to `DEFAULT_FEEDS` there**
over writing a new collector. Write a separate one only when the items need
different parsing or a different source label.

```python
"""<Publisher> — <what it publishes>."""

import logging
import os

import feedparser

from common.relevance import looks_relevant
from common.telemetry import record_target
from common.text import clean_text, parse_time

log = logging.getLogger(__name__)

FEED_URL = os.getenv("<NAME>_FEED", "https://example.org/feed.xml")
# True when the publication is inherently Wellington — the relevance filter
# then needs only a hazard term, not a place name as well.
LOCAL = True


def describe() -> dict:
    return {"summary": "<Publisher> RSS", "endpoint": FEED_URL}


def collect() -> list[dict]:
    parsed = feedparser.parse(FEED_URL)
    if parsed.bozo and not parsed.entries:
        reason = str(getattr(parsed, "bozo_exception", "?"))[:200]
        record_target("<Publisher>", FEED_URL, status="error", detail=reason)
        return []

    signals, kept = [], 0
    for entry in parsed.entries:
        title = clean_text(entry.get("title"))
        summary = clean_text(entry.get("summary") or entry.get("description"))
        text = f"{title}. {summary}".strip(". ").strip()
        if not text:
            continue

        keep, reasons = looks_relevant(text, local=LOCAL)
        if not keep:
            continue

        signals.append({
            "source": {
                "type": "<name>",
                "name": "<Publisher>",
                "collector": "<name>",
                "url": FEED_URL,
                "local": LOCAL,
            },
            "title": title or None,
            "text": text,
            "url": entry.get("link"),
            "external_id": entry.get("id") or entry.get("link"),
            "published_at": parse_time(
                entry.get("published_parsed") or entry.get("updated_parsed")
            ),
            "raw": {"filter": reasons},
        })
        kept += 1

    record_target(
        "<Publisher>", FEED_URL,
        fetched=len(parsed.entries), kept=kept,
        status="ok" if parsed.entries else "empty",
        detail=f"{kept} of {len(parsed.entries)} passed the relevance filter",
    )
    return signals
```

## Template — JSON API

For anything with a real API. `sources/reddit` is the fullest worked example
and also shows how to replay a historical corpus on a shifted clock; `sources/geonet`
is the simplest.

```python
"""<Source> — <what it returns>."""

import logging
import os

import requests

from common.relevance import looks_relevant
from common.telemetry import record_target
from common.text import clean_text, parse_time

log = logging.getLogger(__name__)

BASE_URL = os.getenv("<NAME>_URL", "https://api.example.org").rstrip("/")
API_KEY = os.getenv("<NAME>_API_KEY", "")
TIMEOUT = int(os.getenv("<NAME>_TIMEOUT", "30"))


def describe() -> dict:
    return {
        "summary": "<Source>",
        "endpoint": f"{BASE_URL}/items",
        "configured": bool(API_KEY),
    }


def collect() -> list[dict]:
    # An unconfigured source is not a broken one. Report it as skipped so the
    # dashboard shows why it is quiet, and let everything else run.
    if not API_KEY:
        record_target("<Source>", BASE_URL, status="skipped",
                      detail="<NAME>_API_KEY not set")
        return []

    try:
        r = requests.get(
            f"{BASE_URL}/items",
            headers={"X-API-Key": API_KEY},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("<name> fetch failed: %s", exc)
        record_target("<Source>", BASE_URL, status="error", detail=str(exc)[:200])
        return []

    signals, kept = [], 0
    for item in items:
        text = clean_text(item.get("text"))
        if not text:
            continue

        keep, reasons = looks_relevant(text, local=False)
        if not keep:
            continue

        signal = {
            "source": {
                "type": "<name>",
                "name": "<Source>",
                "collector": "<name>",
                "url": BASE_URL,
                "local": False,
            },
            "title": clean_text(item.get("title")) or None,
            "text": text,
            "url": item.get("permalink"),
            "external_id": f"<name>-{item.get('id')}",
            "published_at": parse_time(item.get("created_at")),
            "raw": {"filter": reasons},
        }

        # If the source gives real coordinates, pass them through — an
        # instrument beats the gazetteer, and geolocate leaves them alone.
        if item.get("lat") and item.get("lon"):
            signal["location_hint"] = {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "place": item.get("place"),
                "confidence": 0.95,
                "method": "<name>-provided",
            }

        signals.append(signal)
        kept += 1

    record_target("<Source>", BASE_URL, fetched=len(items), kept=kept,
                  status="ok" if items else "empty",
                  detail=f"{kept} of {len(items)} kept")
    return signals
```

## Things that will bite

**Everything is optional except `source` and `text`.** Do not invent fields to
fill the schema. A signal with no location is still useful — it shows up under
*Unmapped only* in the raw data view and in the review queue.

**Do not set `verification.status`.** The API stamps every signal `unverified`
at ingest and nothing else may write it. Confirmation is a human action through
the review queue.

**Mark synthetic data as synthetic.** Anything replayed, sampled or generated
carries `raw.synthetic = True` and says so in `source.name`. It is labelled
through the API, the map and the table, and that labelling is load-bearing.

**`external_id` is what makes re-scraping idempotent.** Use the source's own
identity for it. Without a stable one, every poll creates duplicates and
inflates the corroboration count, which is the one number this system asks
people to trust.

**Relevance filtering is region AND hazard by default.** A feed that is already
Wellington-only should set `local=True`, or everything it publishes is discarded
for not naming a suburb. Check the dashboard after the first run.
