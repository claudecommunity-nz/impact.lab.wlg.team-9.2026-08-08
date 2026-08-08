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


class MediaItem(BaseModel):
    """An image or video a scraper found attached to a post (a Mastodon
    attachment, an RSS enclosure) — the browser loads it straight from the
    publisher's own URL, nothing is re-hosted."""

    type: str  # image | video
    url: str


class SignalIn(BaseModel):
    source: SourceRef
    text: str
    title: str | None = None
    url: str | None = None
    external_id: str | None = None
    published_at: datetime | None = None
    location_hint: LocationHint | None = None
    media: list[MediaItem] = Field(default_factory=list)
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


class CitizenReportIn(BaseModel):
    """What the public submission form sends.

    Deliberately smaller and stricter than SignalIn/SourceRef: nothing here
    lets a caller claim to be a higher-trust source than "someone submitted
    this through the public form" — `source` is stamped server-side instead
    of accepted from the request.
    """

    text: str = Field(..., min_length=5, max_length=2000)
    location_text: str | None = Field(None, max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    contact: str | None = Field(None, max_length=200)


class IngestResult(BaseModel):
    received: int
    inserted: int
    duplicates: int


class TargetReport(BaseModel):
    """One endpoint a collector polled during a run."""

    name: str
    url: str | None = None
    fetched: int | None = None
    kept: int | None = None
    status: str = "ok"
    detail: str | None = None
    at: datetime | None = None


class RunReport(BaseModel):
    """A collector telling the store how its last run went.

    Loose on purpose — this is diagnostics, and a scraper that reports nothing
    useful should still be able to say it ran.
    """

    component: str
    kind: str = "scraper"
    status: str = "ok"  # ok | empty | error
    duration_ms: int | None = None
    interval_seconds: int | None = None
    error: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    targets: list[TargetReport] = Field(default_factory=list)
    describes: dict[str, Any] = Field(default_factory=dict)
