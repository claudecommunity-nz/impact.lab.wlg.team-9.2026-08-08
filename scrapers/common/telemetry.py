"""Lets a collector say what it actually polled, without changing collect().

Six scrapers exist and more are being added while this runs, so the collector
contract stays `collect() -> list[dict]`. A source that wants to appear on the
pipeline dashboard in detail calls `record_target()` per endpoint it hits; one
that doesn't still works and simply reports no targets.

The runner clears the buffer before each source and drains it afterwards, so a
source never sees another's entries.
"""

import threading
from datetime import datetime, timezone

_local = threading.local()


def reset() -> None:
    _local.targets = []


def record_target(
    name: str,
    url: str | None = None,
    fetched: int | None = None,
    kept: int | None = None,
    status: str = "ok",
    detail: str | None = None,
) -> None:
    """Note one endpoint this collector polled.

    status: ok | empty | error | skipped — `skipped` covers the case that
    matters most on a dashboard, a source deliberately not being polled (no
    credentials, a closed public timeline) as opposed to one that broke.
    """
    if not hasattr(_local, "targets"):
        reset()
    _local.targets.append(
        {
            "name": name,
            "url": url,
            "fetched": fetched,
            "kept": kept,
            "status": status,
            "detail": detail,
            "at": datetime.now(timezone.utc),
        }
    )


def drain() -> list[dict]:
    targets = getattr(_local, "targets", [])
    reset()
    return targets
