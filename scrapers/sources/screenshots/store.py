"""Where shared screenshots live between being dropped off and being read.

Two backends behind one interface:

* **A directory on disk** — the compose default, and what ``python run.py``
  uses on a laptop. Someone drops a file into ``./screenshot-inbox``.
* **An Azure Blob Storage container** — what the Azure deployment uses. A
  container group's filesystem is thrown away and rebuilt on every deploy, and
  nobody outside the container can put a file into it, so a local inbox in
  Azure is an inbox no one can reach and that loses its contents on the next
  push to main. Blob storage is also the only one of the two the browser can
  load an image out of, which is what makes the screenshot showable in the
  review interface at all.

Both present the same three operations, so the collector neither knows nor
cares which it is talking to. Blob is selected by setting
``SCREENSHOT_BLOB_URL``; otherwise it is the directory.

Blob access is over the REST API with a SAS token rather than the Azure SDK:
one ``requests`` call per operation against a service this repo already talks
to over HTTP, versus a dependency tree the scraper image does not otherwise
need.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

log = logging.getLogger(__name__)

# Folders within the inbox. `INBOX` is the only one a person writes to; the
# collector moves each file into one of the other three as it finishes with it,
# so what is left in `inbox/` is exactly what has not been looked at yet.
INBOX = "inbox"
PROCESSED = "processed"
SKIPPED = "skipped"
FAILED = "failed"

# Requesting an explicit version keeps the response shape stable if the
# account's default moves on underneath us.
BLOB_API_VERSION = "2021-08-06"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def safe_name(name: str) -> str:
    """Reduce a name to characters that are safe in a path and a URL.

    Phone camera rolls and share sheets produce names with spaces, colons and
    the occasional emoji. Nothing downstream needs the original name — it is
    kept only so a person can recognise their own upload in a log line.
    """
    cleaned = _SAFE_NAME.sub("_", name).lstrip(".")
    return cleaned[:80] or "screenshot"


class LocalInbox:
    """A directory. `pending()` reads the top level, the folders below it hold
    what has already been dealt with."""

    kind = "directory"

    def __init__(self, root: Path):
        self.root = root
        self.location = str(root)

    def pending(self, extensions: set[str], limit: int) -> list[str]:
        if not self.root.is_dir():
            log.debug("screenshot inbox %s does not exist", self.root)
            return []
        names = sorted(
            p.name for p in self.root.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        )
        return names[:limit]

    def read(self, name: str) -> bytes:
        return (self.root / name).read_bytes()

    def move(self, name: str, folder: str, rename_to: str | None = None) -> str | None:
        """Move ``name`` into ``folder``, returning the name it landed under.

        A destination that already exists is renamed rather than overwritten.
        On local disk ``rename`` would silently replace it, which is survivable;
        over a network filesystem it can fail instead, and a file that fails to
        move stays in the inbox and is re-processed on the next pass — every
        20 seconds, each time costing another vision API call. Cheaper to be
        careful here than to notice that at the wrong moment.
        """
        dest_dir = self.root / folder
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            final = _unique(rename_to or name, lambda n: (dest_dir / n).exists())
            (self.root / name).rename(dest_dir / final)
            return final
        except OSError as exc:
            log.warning("screenshots: could not move %s to %s: %s", name, folder, exc)
            return None


class BlobInbox:
    """An Azure Blob Storage container, addressed over REST with a SAS token.

    ``container_url`` is the container itself — for example
    ``https://team9st1a2b3c4d.blob.core.windows.net/screenshots`` — and ``sas``
    is a token granting read, write, delete and list on it.
    """

    kind = "azure-blob"

    def __init__(self, container_url: str, sas: str):
        self.container_url = container_url.rstrip("/")
        self.sas = sas.lstrip("?")
        # Account and container only. The SAS is a credential and must not
        # reach a log line or the pipeline dashboard.
        self.location = self.container_url

    def _url(self, blob: str) -> str:
        return f"{self.container_url}/{blob}?{self.sas}"

    def pending(self, extensions: set[str], limit: int) -> list[str]:
        url = (
            f"{self.container_url}?restype=container&comp=list"
            f"&prefix={INBOX}/&maxresults=200&{self.sas}"
        )
        r = requests.get(url, headers={"x-ms-version": BLOB_API_VERSION}, timeout=30)
        if not r.ok:
            log.warning("screenshots: could not list the blob inbox (%d)", r.status_code)
            return []

        names = []
        for blob in ET.fromstring(r.text).iter():
            if _tag(blob) != "Blob":
                continue
            full = next((c.text for c in blob if _tag(c) == "Name"), None)
            if not full:
                continue
            name = full[len(INBOX) + 1:]
            # A blob name may contain slashes; a nested "folder" is somebody
            # dragging a directory in. Take the leaf and leave the rest alone.
            if "/" in name or not name:
                continue
            if Path(name).suffix.lower() in extensions:
                names.append(name)
        return sorted(names)[:limit]

    def read(self, name: str) -> bytes:
        r = requests.get(
            self._url(f"{INBOX}/{name}"),
            headers={"x-ms-version": BLOB_API_VERSION},
            timeout=60,
        )
        r.raise_for_status()
        return r.content

    def move(self, name: str, folder: str, rename_to: str | None = None) -> str | None:
        """Copy to ``folder`` then delete the original.

        Blob storage has no rename. The copy is server-side — the bytes never
        come back through this process — and is synchronous for a blob this
        size within one account.
        """
        final = rename_to or name
        source = self._url(f"{INBOX}/{name}")
        try:
            put = requests.put(
                self._url(f"{folder}/{final}"),
                headers={
                    "x-ms-version": BLOB_API_VERSION,
                    "x-ms-copy-source": source,
                    "content-length": "0",
                },
                timeout=60,
            )
            if not put.ok:
                log.warning(
                    "screenshots: could not copy %s to %s/ (%d)", name, folder, put.status_code
                )
                return None

            delete = requests.delete(
                source, headers={"x-ms-version": BLOB_API_VERSION}, timeout=30
            )
            if not delete.ok:
                # The copy landed, so the file is where it should be; the
                # original is still in the inbox and will be seen again next
                # pass. Said out loud because the symptom — one screenshot
                # ingested repeatedly — is otherwise baffling.
                log.warning(
                    "screenshots: copied %s to %s/ but could not delete the original (%d)",
                    name, folder, delete.status_code,
                )
            return final
        except requests.RequestException as exc:
            log.warning("screenshots: could not move %s to %s: %s", name, folder, exc)
            return None


def _tag(element) -> str:
    """Local element name, ignoring any XML namespace."""
    return element.tag.rsplit("}", 1)[-1]


def _unique(name: str, taken) -> str:
    """``name``, or ``name-2``/``name-3``… if ``taken(name)`` says otherwise."""
    if not taken(name):
        return name
    stem, dot, suffix = name.rpartition(".")
    stem, suffix = (stem, f".{suffix}") if dot else (name, "")
    for n in range(2, 100):
        candidate = f"{stem}-{n}{suffix}"
        if not taken(candidate):
            return candidate
    return name


def open_inbox() -> LocalInbox | BlobInbox:
    """The inbox this process should read, from the environment.

    Blob when ``SCREENSHOT_BLOB_URL`` and ``SCREENSHOT_BLOB_SAS`` are both set,
    otherwise the directory at ``SCREENSHOT_INBOX``. A blob URL with no SAS is
    a misconfiguration rather than a request for local storage, so it says so
    instead of quietly reading an empty directory in Azure.
    """
    url = os.getenv("SCREENSHOT_BLOB_URL", "").strip()
    sas = os.getenv("SCREENSHOT_BLOB_SAS", "").strip()
    if url and sas:
        return BlobInbox(url, sas)
    if url and not sas:
        log.error(
            "SCREENSHOT_BLOB_URL is set but SCREENSHOT_BLOB_SAS is not — "
            "falling back to the local inbox, which in Azure is empty and "
            "does not survive a deploy"
        )
    return LocalInbox(Path(os.getenv("SCREENSHOT_INBOX", "/inbox")))
