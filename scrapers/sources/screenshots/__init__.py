"""Screenshot-share intake source — vision-parsed shared screenshots.

Facebook and TikTok have no viable public API, and scraping logged-in feeds
breaches ToS. The workaround used here: a community member screenshots a post
they can already see and shares the image by dropping it into an inbox. This
collector sends each image to Claude, extracts the post's content as structured
JSON, and hands it to the ingestion API like any other scraper. The human
chooses to share; nothing is scraped from any platform.

Privacy is load-bearing: usernames, handles and personal identifiers are
forbidden in the extraction prompt and a redaction pass strips any that leak
through. No profile information is stored — though the screenshot itself is
unredacted, which is why the review interface blurs it until asked.
"""

import base64
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import anthropic

from . import store

log = logging.getLogger(__name__)

MODEL = os.getenv("SCREENSHOT_VISION_MODEL", "claude-opus-5")
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PER_RUN = 10

# Reading a screenshot is a scoped extraction, not a reasoning problem, so it
# runs at low effort — cheaper and faster, and the schema below does the work
# that a longer prompt would otherwise have to.
EFFORT = "low"
MAX_TOKENS = 8000

PROMPT = (
    "Extract the content of the social media post in this screenshot. A "
    "community member chose to share it during an emergency in Wellington, "
    "New Zealand.\n\n"
    "Never include usernames, handles, profile names, or any other personal "
    "identifier in any field — not in the text, not in the place mentions. "
    "Transcribe the post's own words verbatim, but leave the identity of "
    "whoever wrote them out of it.\n\n"
    "If the image is not a social media post, set is_social_post to false and "
    "leave the other fields empty."
)

# Structured outputs: the model is constrained to this shape, so there is no
# prose to strip, no fences to unwrap, and no malformed-JSON retry path.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "is_social_post": {
            "type": "boolean",
            "description": "Whether the image shows a social media post at all.",
        },
        "platform": {
            "type": "string",
            "enum": [
                "facebook", "instagram", "tiktok", "x",
                "reddit", "mastodon", "other", "unknown",
            ],
        },
        "text": {
            "type": "string",
            "description": "The post's main text, verbatim, with no usernames or handles.",
        },
        "author_type": {
            "type": "string",
            "enum": ["individual", "organisation", "news", "unknown"],
        },
        "posted_time_text": {
            "type": "string",
            "description": "The timestamp exactly as shown (e.g. '2 hrs ago'); empty if none.",
        },
        "place_mentions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Place names mentioned in the post.",
        },
    },
    "required": [
        "is_social_post", "platform", "text",
        "author_type", "posted_time_text", "place_mentions",
    ],
    "additionalProperties": False,
}

_HANDLE_RE = re.compile(r"@[\w.]+")


# --- pure helpers (carry the tests, no I/O) ---------------------------------

def redact_handles(text: str) -> str:
    """Replace ``@handle``-shaped tokens with ``@[redacted]``."""
    return _HANDLE_RE.sub("@[redacted]", text)


def parse_extraction(message) -> dict | None:
    """Pull the extraction out of a Claude response.

    Structured outputs guarantee the first text block is JSON matching
    ``EXTRACTION_SCHEMA``, so this is mostly a safe unwrap: a refusal, a
    truncated response, or anything unparseable returns ``None`` rather than
    raising, because one unreadable screenshot must not stop the run.

    Takes the response object (or anything shaped like one), so the caller
    never has to reach into ``content`` itself.
    """
    # A safety classifier can decline a request; the response is a normal 200
    # with no usable content, so this has to be checked before reading it.
    if getattr(message, "stop_reason", None) == "refusal":
        details = getattr(message, "stop_details", None)
        log.warning(
            "screenshots: extraction refused (%s)",
            getattr(details, "category", None) or "no category given",
        )
        return None
    if getattr(message, "stop_reason", None) == "max_tokens":
        log.warning("screenshots: extraction hit the token cap; treating as unreadable")
        return None

    text = next(
        (b.text for b in (getattr(message, "content", None) or [])
         if getattr(b, "type", None) == "text"),
        None,
    )
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        log.warning("screenshots: extraction was not valid JSON")
        return None
    return parsed if isinstance(parsed, dict) else None


def build_signal(
    extraction: dict, sha1_hex: str, filename: str, image_name: str | None = None
) -> dict:
    """Build a signal dict from a parsed extraction.

    Pure: plain dicts in and out (redaction is in-memory string work).

    ``image_name`` is what the image was stored as. When it is given the signal
    carries a media item pointing at it, so a reviewer can see the screenshot
    the text was read out of and judge the extraction for themselves — the
    text alone gives them no way to tell a good read from a bad one.

    That URL is a path, not an absolute address: it resolves against the API
    serving the signal, whether that is the UI's own origin through nginx or
    the API's port directly.
    """
    platform = (extraction.get("platform") or "unknown").lower()
    text = redact_handles((extraction.get("text") or "").strip())
    mentions = extraction.get("place_mentions") or []
    places = [p for p in mentions if p and p.lower() not in text.lower()]
    if places:
        text = f"{text} (places mentioned: {', '.join(places)})"
    return {
        "source": {
            "type": "screenshot",
            "name": f"Screenshot · {platform}",
            "collector": "screenshots",
            # The sharer is a local person reporting what they see, so relevance
            # filtering must not require a Wellington keyword in the text.
            "local": True,
        },
        "text": text,
        "external_id": sha1_hex,  # sha1 of the image bytes — resharing the same image is a no-op
        # The screenshot is unredacted: whatever name and profile photo were on
        # screen when it was taken are still in the picture, even though they
        # have been stripped out of `text`. The interface blurs it until a
        # reviewer chooses to look, and says why.
        "media": (
            [{"type": "image", "url": f"/screenshots/{image_name}"}] if image_name else []
        ),
        "raw": {
            "screenshot": True,
            "screenshot_unredacted": bool(image_name),
            "platform": platform,
            "author_type": extraction.get("author_type") or "unknown",
            "posted_time_text": extraction.get("posted_time_text"),
            "extraction_model": MODEL,
            "filename": filename,
        },
    }


# --- network-exercising (tests monkeypatch this) ---------------------------

def _extract(image_bytes: bytes, mime: str, api_key: str) -> dict | None:
    """Send one image to Claude and return the extracted post, or ``None``.

    Any API error is logged and swallowed: a screenshot that cannot be read is
    a screenshot to look at later, not a reason to stop the run.
    """
    client = anthropic.Anthropic(api_key=api_key)
    try:
        message = client.messages.create(
            model=MODEL,  # read at call time so tests can set the env first
            max_tokens=MAX_TOKENS,
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.b64encode(image_bytes).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
    except anthropic.APIError as exc:
        log.warning("screenshots: vision request failed: %s", exc)
        return None
    return parse_extraction(message)


# --- collect ---------------------------------------------------------------

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def collect() -> list[dict]:
    """Scan the configured inbox for shared screenshots, extract, and return
    signals.

    Each file is moved out of ``inbox/`` as it is dealt with — into
    ``processed/``, ``skipped/`` (not a social post) or ``failed/`` — so what
    remains in the inbox is exactly what has not been looked at. A processed
    image is stored under its own sha1 and stays there: it is the evidence
    behind the signal, and the review interface loads it back.

    A per-file exception is caught; the file is left where it is and the loop
    continues.
    """
    inbox = store.open_inbox()
    names = inbox.pending(EXTENSIONS, MAX_PER_RUN)
    if not names:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning(
            "screenshots: %d file(s) waiting but ANTHROPIC_API_KEY is unset; "
            "idling until a key is provided", len(names),
        )
        return []

    signals: list[dict] = []
    for name in names:
        try:
            image_bytes = inbox.read(name)
            sha1_hex = hashlib.sha1(image_bytes).hexdigest()
            suffix = Path(name).suffix.lower()
            extraction = _extract(image_bytes, _MIME_BY_EXT.get(suffix, "image/png"), api_key)

            if extraction is None:
                folder, stored = store.FAILED, inbox.move(name, store.FAILED, store.safe_name(name))
            elif not extraction.get("is_social_post") or not (extraction.get("text") or "").strip():
                folder, stored = store.SKIPPED, inbox.move(name, store.SKIPPED, store.safe_name(name))
            else:
                # Stored under the content hash, which is also the signal's
                # external_id — so the same screenshot shared twice is one
                # signal pointing at one image, not two of each.
                folder = store.PROCESSED
                stored = inbox.move(name, store.PROCESSED, f"{sha1_hex}{suffix}")
                signals.append(build_signal(extraction, sha1_hex, name, image_name=stored))
            if stored:
                log.info("screenshots: %s → %s/%s", name, folder, stored)
        except Exception as exc:  # noqa: BLE001 — one file must not stop the loop
            log.warning("screenshots: failed on %s: %s; left in place", name, exc)
            continue

    log.info("screenshots: %d signal(s) from %d file(s)", len(signals), len(names))
    return signals


def describe() -> dict:
    """What this collector is reading, for the pipeline dashboard.

    Deliberately no credential: the inbox location is the account and container
    name, never the SAS token that opens it.
    """
    inbox = store.open_inbox()
    return {
        "storage": inbox.kind,
        "inbox": inbox.location,
        "vision_model": MODEL,
        "configured": bool(os.getenv("ANTHROPIC_API_KEY", "")),
    }
