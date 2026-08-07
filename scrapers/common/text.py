"""Small text helpers shared by every collector."""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(raw: str | None) -> str:
    """Strip HTML tags and collapse whitespace. Feeds and Mastodon both ship HTML."""
    if not raw:
        return ""
    return _WS.sub(" ", unescape(_TAG.sub(" ", raw))).strip()


def parse_time(value) -> datetime | None:
    """Best-effort timestamp parse across the formats these sources use."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    # feedparser struct_time
    try:
        return datetime(*value[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
