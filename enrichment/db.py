"""Enrichment talks to Mongo directly — it rewrites documents in place rather
than creating new ones, which is not what the ingestion API is for."""

import os

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "signals")

_client: MongoClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=True)
    return _client[MONGO_DB]
