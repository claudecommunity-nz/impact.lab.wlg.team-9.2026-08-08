"""Where collected signals go: POST to the ingestion API.

Scrapers never touch Mongo. That keeps the storage decision in one place and
means a new scraper only needs an HTTP client — it can be written in any
language, or run outside this compose file entirely.
"""

import json
import logging
import os
import time
from datetime import datetime

import requests

log = logging.getLogger(__name__)

INGESTION_URL = os.getenv("INGESTION_URL", "http://localhost:8000").rstrip("/")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))


def _encode(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


def post_signals(signals: list[dict]) -> dict:
    """Send signals in batches. Returns aggregate counts."""
    totals = {"received": 0, "inserted": 0, "duplicates": 0}
    if not signals:
        return totals

    for i in range(0, len(signals), BATCH_SIZE):
        chunk = signals[i : i + BATCH_SIZE]
        body = json.dumps({"signals": chunk}, default=_encode)
        for attempt in range(5):
            try:
                r = requests.post(
                    f"{INGESTION_URL}/signals",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )
                r.raise_for_status()
                for k, v in r.json().items():
                    totals[k] = totals.get(k, 0) + v
                break
            except requests.RequestException as exc:
                wait = 2**attempt
                log.warning("post failed (%s), retrying in %ss", exc, wait)
                time.sleep(wait)
        else:
            log.error("gave up posting a batch of %d signals", len(chunk))
    return totals


def wait_for_api(timeout: int = 120) -> bool:
    """Block until the ingestion API answers, so a cold start isn't a crash loop."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            requests.get(f"{INGESTION_URL}/healthz", timeout=5).raise_for_status()
            return True
        except requests.RequestException:
            time.sleep(2)
    log.error("ingestion API never came up at %s", INGESTION_URL)
    return False
