"""Screenshot-share intake source — vision-parsed shared screenshots.

Facebook and TikTok have no viable public API, and scraping logged-in feeds
breaches ToS. The workaround used here: a community member screenshots a post
they can already see and shares the image by dropping it into an inbox folder.
This collector sends each image to the Gemini vision API, extracts the post's
content as structured JSON, and hands it to the ingestion API like any other
scraper. The human chooses to share; nothing is scraped from any platform.

Privacy is load-bearing: usernames, handles and personal identifiers are
forbidden in the extraction prompt and a redaction pass strips any that leak
through. No profile information is stored.
"""

import base64
import hashlib
import logging
import os
import re
from pathlib import Path

import requests

log = logging.getLogger(__name__)

MODEL = os.getenv("SCREENSHOT_VISION_MODEL", "gemini-3.1-flash-lite")
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PER_RUN = 10

PROMPT = (
    "You are extracting the content of a social media post from a screenshot "
    "that a community member chose to share during an emergency. "
    "Return STRICT JSON only, no markdown fences:\n"
    '{"platform": "facebook|instagram|tiktok|x|reddit|mastodon|other|unknown", '
    '"text": "the post\'s main text, verbatim", '
    '"author_type": "individual|organisation|news|unknown", '
    '"posted_time_text": "the timestamp exactly as shown (e.g. \'2 hrs ago\'), or null", '
    '"place_mentions": ["place names mentioned in the post"], '
    '"is_social_post": true}\n'
    "Never include usernames, handles, profile names, or any personal identifier "
    "in any field. If the image is not a social media post, return "
    '{"is_social_post": false}.'
)

_HANDLE_RE = re.compile(r"@[\w.]+")


# --- pure helpers (carry the tests, no I/O) ---------------------------------

def redact_handles(text: str) -> str:
    """Replace ``@handle``-shaped tokens with ``@[redacted]``."""
    return _HANDLE_RE.sub("@[redacted]", text)


def parse_extraction(response_json: dict) -> dict | None:
    """Dig ``candidates[0].content.parts[0].text`` out of a Gemini REST
    response, strip optional ```json fences, ``json.loads`` it.

    Any missing key, empty candidates, or invalid JSON → ``None`` (never raises).
    """
    try:
        candidates = response_json.get("candidates") or []
        if not candidates:
            return None
        parts = (candidates[0].get("content") or {}).get("parts") or []
        if not parts:
            return None
        text = parts[0].get("text")
        if not text:
            return None
    except (AttributeError, IndexError, TypeError):
        return None

    # Strip optional ```json / ``` fences.
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence (handles ```json or bare ```) and the closing ```.
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        else:
            # No newline — fence with nothing after it.
            return None
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    stripped = stripped.strip()

    import json
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def build_signal(extraction: dict, sha1_hex: str, filename: str) -> dict:
    """Build a signal dict from a parsed extraction.

    Pure: plain dicts in and out (redaction is in-memory string work).
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
        "raw": {
            "screenshot": True,
            "platform": platform,
            "author_type": extraction.get("author_type") or "unknown",
            "posted_time_text": extraction.get("posted_time_text"),
            "extraction_model": MODEL,
            "filename": filename,
        },
    }


# --- network-exercising (tests monkeypatch this) ---------------------------

def _extract(image_bytes: bytes, mime: str, api_key: str) -> dict | None:
    """POST one image to the Gemini vision endpoint and parse the response.

    Non-2xx or ``requests.RequestException`` → log a warning, return ``None``.
    """
    model = MODEL  # resolved at call time so tests can set the env first
    try:
        r = requests.post(
            API_URL.format(model=model) + f"?key={api_key}",
            json={
                "contents": [{"parts": [
                    {"text": PROMPT},
                    {"inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(image_bytes).decode(),
                    }},
                ]}],
                "generationConfig": {"response_mime_type": "application/json"},
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        log.warning("screenshot vision request failed: %s", exc)
        return None
    if not (200 <= r.status_code < 300):
        log.warning("screenshot vision API returned %d", r.status_code)
        return None
    return parse_extraction(r.json())


# --- collect ---------------------------------------------------------------

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def collect() -> list[dict]:
    """Scan the configured inbox for shared screenshots, extract, and return
    signals. Files are moved to ``processed/``, ``skipped/`` or ``failed/``
    subdirectories. A per-file exception is caught; the file is left in place
    and the loop continues.
    """
    inbox = Path(os.getenv("SCREENSHOT_INBOX", "/inbox"))
    if not inbox.is_dir():
        log.debug("screenshot inbox %s does not exist", inbox)
        return []

    candidates = sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() in EXTENSIONS
    )
    if not candidates:
        return []
    candidates = candidates[:MAX_PER_RUN]

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        log.warning(
            "screenshots: %d file(s) in inbox but GEMINI_API_KEY is unset; "
            "idling until a key is provided", len(candidates),
        )
        return []

    signals: list[dict] = []
    for path in candidates:
        try:
            image_bytes = path.read_bytes()
            sha1_hex = hashlib.sha1(image_bytes).hexdigest()
            extraction = _extract(image_bytes, _MIME_BY_EXT.get(path.suffix.lower(), "image/png"), api_key)

            if extraction is None:
                dest = _move(path, inbox / "failed")
            elif not extraction.get("is_social_post") or not (extraction.get("text") or "").strip():
                dest = _move(path, inbox / "skipped")
            else:
                signals.append(build_signal(extraction, sha1_hex, path.name))
                dest = _move(path, inbox / "processed")
            if dest:
                log.info("screenshots: %s → %s", path.name, dest.name)
        except Exception as exc:  # noqa: BLE001 — one file must not stop the loop
            log.warning("screenshots: failed on %s: %s; left in place", path.name, exc)
            continue

    log.info("screenshots: %d signal(s) from %d file(s)", len(signals), len(candidates))
    return signals


def _move(path: Path, dest_dir: Path) -> Path | None:
    """Create ``dest_dir`` if needed and rename ``path`` into it."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return path.rename(dest_dir / path.name)
    except OSError as exc:  # pragma: no cover — guarded by caller
        log.warning("screenshots: could not move %s to %s: %s", path.name, dest_dir.name, exc)
        return None
