"""Find where several independent sources appear to describe the same event.

This is the job the problem statement is really about. Two people posting about
water over the road in Island Bay within the hour is not proof of anything, but
it is a much better reason to send someone to look than one person posting.

The grouping is deliberately simple and deliberately explainable: same issue
type, within CLUSTER_RADIUS_KM, within CLUSTER_WINDOW_HOURS. Every cluster
records its members so a duty officer can read the original items and decide
for themselves.

Two things this does *not* do, on purpose:

  * It does not treat multiple sources as verification. `source_count` is a
    count of publishers, not a truth score, and the note on every cluster says
    so.
  * It does not count two items from the same publisher as two sources. A
    syndicated story reprinted five times is one source.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt

log = logging.getLogger(__name__)

VERSION = "proximity-v1"

RADIUS_KM = float(os.getenv("CLUSTER_RADIUS_KM", "2.0"))
WINDOW_HOURS = float(os.getenv("CLUSTER_WINDOW_HOURS", "12"))
LOOKBACK_HOURS = float(os.getenv("CLUSTER_LOOKBACK_HOURS", "72"))

NOTE = (
    "Several sources describing something similar nearby. This is a reason to "
    "check, not confirmation that it happened."
)


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _when(doc) -> datetime:
    return doc.get("published_at") or doc.get("ingested_at") or datetime.now(timezone.utc)


def run(db) -> dict:
    """Rebuild the cluster set from scratch.

    Rebuilding rather than updating incrementally: at hackathon volumes it costs
    nothing, and it means changing the radius or window takes effect on the next
    tick instead of leaving stale groupings behind.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    signals = list(
        db.signals.find(
            {
                "geo": {"$exists": True},
                "enrichment.classify.issue_type": {"$exists": True, "$ne": "unclassified"},
                "$or": [{"published_at": {"$gte": cutoff}}, {"ingested_at": {"$gte": cutoff}}],
            }
        ).sort("published_at", 1)
    )

    clusters: list[dict] = []

    for doc in signals:
        lon, lat = doc["geo"]["coordinates"]
        issue = doc["enrichment"]["classify"]["issue_type"]
        when = _when(doc)

        target = None
        for cluster in clusters:
            if cluster["issue_type"] != issue:
                continue
            if abs((when - cluster["last_seen"]).total_seconds()) > WINDOW_HOURS * 3600:
                continue
            if haversine_km(lat, lon, cluster["lat"], cluster["lon"]) > RADIUS_KM:
                continue
            target = cluster
            break

        if target is None:
            clusters.append(
                {
                    # The earliest member's id — stable, because a cluster's
                    # first member never changes as later ones arrive.
                    "cluster_id": f"{issue}:{doc['signal_id'][:12]}",
                    "issue_type": issue,
                    "lat": lat,
                    "lon": lon,
                    "first_seen": when,
                    "last_seen": when,
                    "signal_ids": [doc["signal_id"]],
                    "members": [doc],
                }
            )
            continue

        # Running mean, so a cluster's position drifts toward its members
        # rather than sticking to wherever the first one happened to land.
        n = len(target["members"])
        target["lat"] = (target["lat"] * n + lat) / (n + 1)
        target["lon"] = (target["lon"] * n + lon) / (n + 1)
        target["last_seen"] = max(target["last_seen"], when)
        target["first_seen"] = min(target["first_seen"], when)
        target["signal_ids"].append(doc["signal_id"])
        target["members"].append(doc)

    docs = [_finalise(c) for c in clusters]

    # Swap the whole set in one go.
    db.clusters.delete_many({})
    if docs:
        db.clusters.insert_many(docs)

    # Stamp the result back onto each signal so a signal can be read on its own
    # and still say how much company it has.
    now = datetime.now(timezone.utc)
    for cluster in docs:
        db.signals.update_many(
            {"signal_id": {"$in": cluster["signal_ids"]}},
            {
                "$set": {
                    "enrichment.corroborate": {
                        "cluster_id": cluster["cluster_id"],
                        "source_count": cluster["source_count"],
                        "signal_count": cluster["signal_count"],
                        "corroboration": cluster["corroboration"],
                        "note": NOTE,
                        "method": VERSION,
                        "version": VERSION,
                        "at": now,
                    }
                }
            },
        )

    multi = sum(1 for c in docs if c["source_count"] >= 2)
    log.info(
        "corroborate: %d signals → %d clusters, %d with 2+ independent sources",
        len(signals),
        len(docs),
        multi,
    )
    return {"signals": len(signals), "clusters": len(docs), "multi_source": multi}


def _finalise(cluster: dict) -> dict:
    members = cluster.pop("members")

    # Independence is by publisher, not by item.
    sources = sorted({m["source"]["name"] for m in members})
    source_types = sorted({m["source"]["type"] for m in members})

    location_confidences = [
        m.get("enrichment", {}).get("geolocate", {}).get("confidence", 0.0) for m in members
    ]
    places = [
        m.get("enrichment", {}).get("geolocate", {}).get("place")
        for m in members
        if m.get("enrichment", {}).get("geolocate", {}).get("place")
    ]

    source_count = len(sources)
    corroboration = (
        "single_source" if source_count < 2 else "two_sources" if source_count == 2 else "multi_source"
    )

    return {
        "cluster_id": cluster["cluster_id"],
        "issue_type": cluster["issue_type"],
        "geo": {"type": "Point", "coordinates": [round(cluster["lon"], 5), round(cluster["lat"], 5)]},
        "place": max(set(places), key=places.count) if places else None,
        "first_seen": cluster["first_seen"],
        "last_seen": cluster["last_seen"],
        "signal_ids": cluster["signal_ids"],
        "signal_count": len(members),
        "sources": sources,
        "source_types": source_types,
        "source_count": source_count,
        "corroboration": corroboration,
        "location_confidence": round(max(location_confidences, default=0.0), 2),
        # If any member is replayed demo data, the whole cluster says so.
        "contains_synthetic": any(m.get("raw", {}).get("synthetic") for m in members),
        "verification_status": "unverified",
        "note": NOTE,
        "method": VERSION,
        "params": {"radius_km": RADIUS_KM, "window_hours": WINDOW_HOURS},
    }
