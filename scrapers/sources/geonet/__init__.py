"""GeoNet earthquake collector.

GeoNet's public API is open, needs no key, and — unlike the text sources —
gives real coordinates and a real magnitude. That makes it useful for two
reasons: the map has something trustworthy on it within a minute of startup,
and it shows the reliability gradient the interface is meant to make visible.
A GeoNet epicentre is instrument-derived; a post saying "felt a big one in
Newtown" is not. Both appear, labelled differently.

Docs: https://api.geonet.org.nz/
"""

import logging
import os

import requests

from common.geo import WELLINGTON, haversine_km
from common.text import parse_time

log = logging.getLogger(__name__)

API = "https://api.geonet.org.nz/quake"
MIN_MMI = int(os.getenv("GEONET_MIN_MMI", "3"))  # 3 = weakly felt
RADIUS_KM = float(os.getenv("GEONET_RADIUS_KM", "250"))

MMI_DESCRIPTION = {
    -1: "not felt", 0: "not felt", 1: "not felt", 2: "barely felt",
    3: "weak", 4: "light", 5: "moderate", 6: "strong",
    7: "severe", 8: "extreme",
}


def collect() -> list[dict]:
    try:
        r = requests.get(
            API,
            params={"MMI": MIN_MMI},
            headers={"Accept": "application/vnd.geo+json;version=2"},
            timeout=30,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("geonet fetch failed: %s", exc)
        return []

    signals = []
    for feat in features:
        props = feat.get("properties", {})
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])

        distance = haversine_km((lat, lon), WELLINGTON)
        if distance > RADIUS_KM:
            continue

        mag = props.get("magnitude")
        locality = props.get("locality", "unknown location")
        mmi = props.get("mmi", -1)
        depth = props.get("depth")
        text = (
            f"M{mag:.1f} earthquake {locality}, {depth:.0f} km deep. "
            f"Shaking {MMI_DESCRIPTION.get(mmi, 'unknown')} (MMI {mmi}). "
            f"{distance:.0f} km from Wellington CBD."
        ) if isinstance(mag, (int, float)) and isinstance(depth, (int, float)) else (
            f"Earthquake {locality} (MMI {mmi})."
        )

        signals.append(
            {
                "source": {
                    "type": "geonet",
                    "name": "GeoNet (GNS Science)",
                    "collector": "geonet",
                    "url": API,
                    "local": True,
                },
                "title": f"M{mag} earthquake {locality}" if mag else f"Earthquake {locality}",
                "text": text,
                "url": f"https://www.geonet.org.nz/earthquake/{props.get('publicID')}",
                "external_id": props.get("publicID"),
                "published_at": parse_time(props.get("time")),
                # Instrument-located, so the geolocate job leaves it alone.
                "location_hint": {
                    "lat": lat,
                    "lon": lon,
                    "place": locality,
                    "confidence": 0.98,
                    "method": "geonet-epicentre",
                },
                "raw": {
                    "magnitude": mag,
                    "depth_km": depth,
                    "mmi": mmi,
                    "quality": props.get("quality"),
                    "distance_from_wellington_km": round(distance, 1),
                },
            }
        )

    log.info("geonet: %d quakes within %.0f km", len(signals), RADIUS_KM)
    return signals
