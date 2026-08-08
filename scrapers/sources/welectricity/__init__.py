"""Wellington Electricity live outages collector."""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

log = logging.getLogger(__name__)

URL = "https://www.welectricity.co.nz/outages/getalloutages"
USER_AGENT = "Mozilla/5.0 (compatible; impact-lab-team9 hackathon)"
TIMEOUT = 30


def _fetch() -> dict:
    resp = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def parse_outage(outage: dict) -> dict | None:
    if not isinstance(outage, dict):
        return None

    if outage.get("type") != "unplanned":
        return None

    status = (outage.get("status") or "").strip()
    is_closed = status.lower() == "closed"

    include_closed = os.getenv("WELECTRICITY_INCLUDE_CLOSED", "").lower() in ("1", "true", "yes")
    if is_closed and not include_closed:
        return None

    suburbs_text = outage.get("suburbsText") or ""
    areas = outage.get("areas") or []
    first_suburb = ""
    streets = []
    if isinstance(areas, list):
        for area in areas:
            if isinstance(area, dict):
                if not first_suburb and area.get("suburb"):
                    first_suburb = area.get("suburb")
                if area.get("street"):
                    streets.append(area.get("street"))

    place = suburbs_text or first_suburb

    comments = outage.get("lastUpdatedComments") or ""
    status_prefix = " (restored)" if is_closed else ""
    text = f"Power outage{status_prefix} — {suburbs_text}: {comments}".strip()

    customers = outage.get("lastUpdatedCustomersAffected")
    if customers:
        text += f" ({customers} customers affected)"

    location_hint = None
    loc = outage.get("location")
    if isinstance(loc, dict) and loc.get("lat") is not None and loc.get("lng") is not None:
        try:
            lat = float(loc["lat"])
            lon = float(loc["lng"])
            location_hint = {
                "lat": lat,
                "lon": lon,
                "place": place,
                "confidence": 0.95,
                "method": "source-provided",
            }
        except (ValueError, TypeError):
            location_hint = None

    published_at = None
    time_of_fault = outage.get("timeOfFault")
    if time_of_fault:
        try:
            dt = datetime.strptime(str(time_of_fault), "%Y-%m-%d %H:%M:%S")
            published_at = dt.replace(tzinfo=ZoneInfo("Pacific/Auckland"))
        except (ValueError, TypeError):
            published_at = None

    ext_id = str(outage["id"]) if outage.get("id") is not None else None
    url = outage.get("link") or None

    res = {
        "source": {
            "type": "welectricity",
            "name": "Wellington Electricity",
            "collector": "welectricity",
            "local": True,
        },
        "text": text,
        "external_id": ext_id,
        "published_at": published_at,
        "raw": {
            "status": outage.get("status"),
            "timeBasedStatus": outage.get("timeBasedStatus"),
            "lastUpdatedEta": outage.get("lastUpdatedEta"),
            "suburbsText": suburbs_text,
            "streets": streets,
        },
    }

    if url:
        res["url"] = url
    if location_hint:
        res["location_hint"] = location_hint

    return res


def collect() -> list[dict]:
    try:
        data = _fetch()
    except requests.RequestException as exc:
        log.warning("welectricity fetch failed: %s", exc)
        return []

    unplanned = data.get("unplannedOutages") if isinstance(data, dict) else []
    if not isinstance(unplanned, list):
        unplanned = []

    signals = []
    for outage in unplanned:
        sig = parse_outage(outage)
        if sig is not None:
            signals.append(sig)

    log.info("welectricity: %d signals collected from %d outages", len(signals), len(unplanned))
    return signals
