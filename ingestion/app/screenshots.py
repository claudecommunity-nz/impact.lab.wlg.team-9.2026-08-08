"""Serving the screenshot behind a screenshot-sourced signal.

The collector stores each processed image under its own sha1 — on disk under
compose, in an Azure Blob container in the deployment — and puts a
``/screenshots/<name>`` media URL on the signal. This reads that image back so
a reviewer can see what the text was extracted from.

The image goes through this API rather than being linked directly, so the
credential that opens the blob container stays server-side. A SAS token in a
public ``/signals`` response would be a read credential published alongside
the thing it protects.

Two backends, chosen by environment, matching the collector's:

* ``SCREENSHOT_BLOB_URL`` + ``SCREENSHOT_BLOB_SAS`` → Azure Blob Storage
* otherwise ``SCREENSHOT_INBOX`` → a directory (mounted read-only under compose)

Stdlib ``urllib`` rather than ``requests``: this is the only outbound HTTP the
ingestion API makes, and it does not otherwise carry an HTTP client.
"""

import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("ingestion.screenshots")

# Processed images are stored under the sha1 of their own bytes, so a valid
# name is exactly that and nothing else. This is what keeps a crafted name
# from reaching an arbitrary path or an arbitrary blob — the check is an
# allowlist of the one shape we ever write, not a search for bad characters.
NAME_RE = re.compile(r"^[0-9a-f]{40}\.(png|jpe?g|webp)$")

PROCESSED = "processed"
BLOB_API_VERSION = "2021-08-06"

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def content_type(name: str) -> str:
    return _MIME_BY_EXT.get(Path(name).suffix.lower(), "application/octet-stream")


def is_valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def read(name: str) -> bytes | None:
    """The stored image's bytes, or ``None`` if it isn't there.

    Callers must have checked ``is_valid_name`` first; this asserts it rather
    than trusting them, because the cost of being wrong is an arbitrary read.
    """
    if not is_valid_name(name):
        raise ValueError(f"refusing to read {name!r}: not a stored screenshot name")

    blob_url = os.getenv("SCREENSHOT_BLOB_URL", "").strip().rstrip("/")
    blob_sas = os.getenv("SCREENSHOT_BLOB_SAS", "").strip().lstrip("?")
    if blob_url and blob_sas:
        return _read_blob(f"{blob_url}/{PROCESSED}/{name}?{blob_sas}")

    path = Path(os.getenv("SCREENSHOT_INBOX", "/inbox")) / PROCESSED / name
    try:
        return path.read_bytes()
    except OSError:
        return None


def _read_blob(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"x-ms-version": BLOB_API_VERSION})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            # Logged without the URL: it carries the SAS token.
            log.warning("screenshot blob read returned %d", exc.code)
        return None
    except OSError as exc:
        log.warning("screenshot blob read failed: %s", exc)
        return None
