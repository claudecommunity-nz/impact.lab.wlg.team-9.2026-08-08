"""Fixture replay — synthetic signals, for demoing with no network.

Venue wifi fails, feeds go quiet, and a live source has no obligation to
produce a flood at 16:30. This collector replays a bundled file so the
pipeline always has something to show.

Every item it emits is marked `synthetic: true` in `raw` and its source name
says so. Synthetic content that isn't visibly labelled is the same failure the
problem statement is about, one step earlier.

Timestamps in the file are relative (`minutes_ago`), so the replayed set always
looks current.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "data" / "wellington_sample.json"


def collect() -> list[dict]:
    if not DATA_FILE.exists():
        log.warning("no fixture file at %s", DATA_FILE)
        return []

    items = json.loads(DATA_FILE.read_text())
    now = datetime.now(timezone.utc)
    signals = []

    for i, item in enumerate(items):
        published = now - timedelta(minutes=item.get("minutes_ago", 0))
        signals.append(
            {
                "source": {
                    "type": "fixtures",
                    "name": f"{item['publisher']} (synthetic sample)",
                    "collector": "fixtures",
                    "url": None,
                    "local": True,
                },
                "title": item.get("title"),
                "text": item["text"],
                "url": None,
                # Stable across replays so re-running dedupes rather than
                # duplicating, but not stable across days.
                "external_id": f"fixture-{now:%Y%m%d}-{i:03d}",
                "published_at": published,
                "raw": {
                    "synthetic": True,
                    "note": "Sample data for demonstration. Not a real report.",
                    "channel": item.get("channel"),
                },
            }
        )

    log.info("fixtures: replayed %d synthetic signals", len(signals))
    return signals
