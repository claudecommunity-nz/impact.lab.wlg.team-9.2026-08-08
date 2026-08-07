"""Turn place names in text into approximate coordinates.

Gazetteer matching, so every result is explainable: the job records which
phrase it matched, what kind of place that was, and how confident that makes
it. A signal placed from the phrase "Ngauranga Gorge" is a firmer claim than
one placed from "Wellington", and the record says which happened.

Signals that arrived with coordinates from the source (GeoNet, for example)
are left alone — an instrument beats a keyword.
"""

import logging
import re
from datetime import datetime, timezone

from . import gazetteer

log = logging.getLogger(__name__)

VERSION = "gazetteer-v1"
BATCH = 500

# Longest names first so "Mount Victoria Tunnel" wins over "Mount Victoria".
_LOOKUP: list[tuple[str, re.Pattern, float, float, str]] = sorted(
    (
        (name, re.compile(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", re.I), lat, lon, kind)
        for name, lat, lon, kind, aliases in gazetteer.ENTRIES
        for phrase in (name, *aliases)
    ),
    key=lambda row: -len(row[1].pattern),
)


def locate_text(text: str) -> dict | None:
    """Best place match for a block of text, with its supporting evidence."""
    hits: list[dict] = []
    seen_names: set[str] = set()

    for name, pattern, lat, lon, kind in _LOOKUP:
        match = pattern.search(text or "")
        if not match or name in seen_names:
            continue
        seen_names.add(name)
        hits.append(
            {
                "place": name,
                "lat": lat,
                "lon": lon,
                "kind": kind,
                "matched": match.group(0).lower(),
                "position": match.start(),
            }
        )

    if not hits:
        return None

    # Most specific kind wins; ties go to whichever is mentioned first.
    order = ["landmark", "suburb", "town", "region"]
    hits.sort(key=lambda h: (order.index(h["kind"]), h["position"]))
    best = hits[0]

    confidence = gazetteer.KIND_CONFIDENCE.get(best["kind"], 0.3)

    # Several different specific places in one item usually means a roundup
    # article rather than one located event, so trust the pin less.
    competing = [h for h in hits if h["kind"] in ("landmark", "suburb", "town")]
    if len(competing) > 1:
        confidence = round(max(0.15, confidence - 0.1 * (len(competing) - 1)), 2)

    return {
        "place": best["place"],
        "lat": best["lat"],
        "lon": best["lon"],
        "kind": best["kind"],
        "confidence": confidence,
        "method": VERSION,
        "matched": best["matched"],
        # Everything else the text mentioned, so a reader can see the pin was a
        # choice between candidates rather than the only possibility.
        "other_candidates": [
            {"place": h["place"], "kind": h["kind"], "matched": h["matched"]} for h in hits[1:6]
        ],
        "precision_note": (
            f"Approximate {best['kind']} centroid, inferred from the phrase "
            f"\"{best['matched']}\". Not a reported position."
        ),
    }


def run(db) -> dict:
    """Locate anything not already stamped with this gazetteer version.

    `source-hint` is treated as a version too, which is what keeps this job off
    signals that arrived with real coordinates.
    """
    query = {"enrichment.geolocate.version": {"$nin": [VERSION, "source-hint"]}}
    cursor = db.signals.find(query, {"signal_id": 1, "title": 1, "text": 1}).limit(BATCH)

    now = datetime.now(timezone.utc)
    located = unlocated = 0

    for doc in cursor:
        blob = f"{doc.get('title') or ''} {doc.get('text') or ''}"
        result = locate_text(blob)

        if result:
            result["version"] = VERSION
            result["at"] = now
            db.signals.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "enrichment.geolocate": result,
                        "geo": {"type": "Point", "coordinates": [result["lon"], result["lat"]]},
                    }
                },
            )
            located += 1
        else:
            # Recorded as a miss rather than left blank, so "we could not place
            # this" is visible in the interface instead of the signal quietly
            # vanishing from the map.
            db.signals.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "enrichment.geolocate": {
                            "place": None,
                            "confidence": 0.0,
                            "method": VERSION,
                            "version": VERSION,
                            "at": now,
                            "precision_note": "No known place name found in the text.",
                        }
                    }
                },
            )
            unlocated += 1

    if located or unlocated:
        log.info("geolocate: %d located, %d without a place name", located, unlocated)
    return {"located": located, "unlocated": unlocated}
