"""Mongo connection and index setup.

One database, two collections:

  signals   one row per thing a scraper found, plus whatever enrichment has
            stamped onto it so far
  clusters  groups of signals that look like they might describe the same
            event, rebuilt by the corroborate job
"""

import logging
import os

from pymongo import ASCENDING, DESCENDING, GEOSPHERE, MongoClient
from pymongo.errors import OperationFailure, PyMongoError

log = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "signals")

_client: MongoClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=True)
    return _client[MONGO_DB]


# (collection, keys, unique). The two unique ones are the load-bearing pair —
# they are what makes re-scraping the same item a no-op. The rest are speed.
INDEXES = [
    ("signals", [("signal_id", ASCENDING)], True),
    ("signals", [("published_at", DESCENDING)], False),
    ("signals", [("ingested_at", DESCENDING)], False),
    ("signals", [("source.type", ASCENDING)], False),
    ("signals", [("enrichment.classify.version", ASCENDING)], False),
    ("signals", [("enrichment.geolocate.version", ASCENDING)], False),
    ("signals", [("enrichment.classify.issue_type", ASCENDING)], False),
    ("signals", [("geo", GEOSPHERE)], False),
    ("clusters", [("cluster_id", ASCENDING)], True),
    ("clusters", [("source_count", DESCENDING)], False),
    ("clusters", [("last_seen", DESCENDING)], False),
]


def ensure_indexes(db) -> None:
    """Create indexes, tolerating a backend that refuses some of them.

    Real MongoDB accepts all of these. Hosted services that merely speak the
    wire protocol do not always — Cosmos DB's Mongo API, for one, rejects a
    unique index on a collection that already holds data. A refused index
    should cost performance or de-duplication, not take the whole API down, so
    each is attempted independently and failures are logged loudly rather than
    raised.

    The connection itself still has to work: a genuine connectivity failure
    surfaces on the first attempt and propagates, which is what the caller's
    retry loop is watching for.
    """
    created = refused = 0
    for i, (collection, keys, unique) in enumerate(INDEXES):
        try:
            db[collection].create_index(keys, unique=unique)
            created += 1
        except OperationFailure as exc:
            refused += 1
            log.warning(
                "index %s on %s refused by the server: %s%s",
                keys,
                collection,
                exc,
                " — duplicate signals will not be prevented" if unique else "",
            )
        except PyMongoError:
            # Not the server disliking the index — the server not being there.
            if i == 0:
                raise
            log.exception("index %s on %s failed", keys, collection)

    log.info("indexes on %s: %d created, %d refused", MONGO_DB, created, refused)
