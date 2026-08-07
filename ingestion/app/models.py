"""Wire format between the scrapers and the ingestion API.

Deliberately loose: a scraper only has to supply `source` and `text`. Anything
it happens to know beyond that (a URL, a publish time, real coordinates) makes
the enrichment jobs' work easier, but nothing downstream requires it.
"""

import hashlib
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

_WS = re.compile(r"\s+")


class SourceRef(BaseModel):
    type: str  # rss | geonet | mastodon | fixtures | ...
    name: str  # human-readable publisher, shown in the UI
    collector: str  # which scraper produced this
    url: str | None = None  # the feed/endpoint, not the item
    # Set when the source is inherently local (a Wellington-only publication),
    # so relevance filtering doesn't need a place name in the text.
    local: bool = False


class LocationHint(BaseModel):
    """Coordinates the source itself provided — always beats inference."""

    lat: float
    lon: float
    place: str | None = None
    confidence: float = 0.95
    method: str = "source-provided"


class SignalIn(BaseModel):
    source: SourceRef
    text: str
    title: str | None = None
    url: str | None = None
    external_id: str | None = None
    published_at: datetime | None = None
    location_hint: LocationHint | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def signal_id(self) -> str:
        """Stable identity so re-scraping the same item is a no-op.

        Prefer whatever the source considers the item's identity; fall back to
        the URL, then to the text itself.
        """
        basis = self.external_id or self.url or _WS.sub(" ", self.text).strip()[:300]
        return hashlib.sha1(f"{self.source.type}|{basis}".encode()).hexdigest()


class SignalBatch(BaseModel):
    signals: list[SignalIn]


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int
