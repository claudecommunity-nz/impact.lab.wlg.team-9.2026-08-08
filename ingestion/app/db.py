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
    # One document per pipeline component, upserted on every run — this is what
    # the dashboard reads to say when each scraper and job last did anything.
    ("component_runs", [("component", ASCENDING)], True),
]


def ensure_indexes(db) -> None:
    db.signals.create_index([("signal_id", ASCENDING)], unique=True)
    db.signals.create_index([("published_at", DESCENDING)])
    db.signals.create_index([("ingested_at", DESCENDING)])
    db.signals.create_index([("source.type", ASCENDING)])
    db.signals.create_index([("enrichment.classify.version", ASCENDING)])
    db.signals.create_index([("enrichment.geolocate.version", ASCENDING)])
    db.signals.create_index([("enrichment.classify.issue_type", ASCENDING)])
    db.signals.create_index([("verification.status", ASCENDING)])
    db.signals.create_index([("geo", GEOSPHERE)])
    db.clusters.create_index([("cluster_id", ASCENDING)], unique=True)
    db.clusters.create_index([("source_count", DESCENDING)])
    db.clusters.create_index([("last_seen", DESCENDING)])
    log.info("indexes ensured on %s", MONGO_DB)
