"""Waka Kotahi NZTA road delays and events collector."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

log = logging.getLogger(__name__)

URL = "https://www.journeys.nzta.govt.nz/assets/map-data-cache/delays.json"
WELLINGTON_REGION = 16
TIMEOUT = 30


def _fetch() -> dict:
    resp = requests.get(URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def parse_event(feature: dict) -> dict | None:
    if not isinstance(feature, dict):
        return None

    props = feature.get("properties")
    if not isinstance(props, dict):
        return None

    regions = props.get("regions") or []
    if not isinstance(regions, list) or WELLINGTON_REGION not in regions:
        return None

    status = props.get("Status")
    if status != "Active":
        return None

    event_type = props.get("EventType") or ""
    loc_area = props.get("LocationArea") or ""
    event_desc = props.get("EventDescription") or ""
    is_planned = bool(props.get("IsPlanned"))

    prefix = "Planned " if is_planned else ""
    text = f"{prefix}{event_type} — {loc_area}: {event_desc}"

    impact = props.get("Impact")
    if impact:
        text += f" Impact: {impact}."

    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    pt = None
    if gtype == "Point" and isinstance(coords, list) and len(coords) >= 2:
        pt = coords
    elif gtype == "LineString" and isinstance(coords, list) and len(coords) > 0 and isinstance(coords[0], list) and len(coords[0]) >= 2:
        pt = coords[0]
    elif gtype == "MultiLineString" and isinstance(coords, list) and len(coords) > 0 and isinstance(coords[0], list) and len(coords[0]) > 0 and isinstance(coords[0][0], list) and len(coords[0][0]) >= 2:
        pt = coords[0][0]

    location_hint = None
    if pt is not None:
        try:
            lon = float(pt[0])
            lat = float(pt[1])
            location_hint = {
                "lat": lat,
                "lon": lon,
                "place": loc_area,
                "confidence": 0.9,
                "method": "source-provided",
            }
        except (ValueError, TypeError):
            location_hint = None

    published_at = None
    last_edited = props.get("LastEdited")
    if last_edited:
        try:
            dt = datetime.strptime(str(last_edited), "%Y-%m-%d %H:%M:%S")
            published_at = dt.replace(tzinfo=ZoneInfo("Pacific/Auckland"))
        except (ValueError, TypeError):
            published_at = None

    ext_id = str(props["id"]) if props.get("id") is not None else None

    res = {
        "source": {
            "type": "nzta",
            "name": "Waka Kotahi NZTA (Journeys)",
            "collector": "nzta",
            "local": True,
        },
        "text": text,
        "external_id": ext_id,
        "published_at": published_at,
        "raw": {
            "EventType": event_type,
            "IsPlanned": is_planned,
            "Impact": impact,
            "ExpectedResolutionText": props.get("ExpectedResolutionText"),
            "regions": regions,
        },
    }

    if location_hint:
        res["location_hint"] = location_hint

    return res


def collect() -> list[dict]:
    try:
        data = _fetch()
    except requests.RequestException as exc:
        log.warning("nzta fetch failed: %s", exc)
        return []

    features = data.get("features") if isinstance(data, dict) else []
    if not isinstance(features, list):
        features = []

    signals = []
    for feat in features:
        sig = parse_event(feat)
        if sig is not None:
            signals.append(sig)

    log.info("nzta: %d signals collected from %d features", len(signals), len(features))
    return signals
