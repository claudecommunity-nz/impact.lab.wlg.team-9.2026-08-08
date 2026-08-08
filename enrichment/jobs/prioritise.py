"""Cluster prioritisation enrichment job — WCC response doctrine triage tiers.

Wellington City Council Emergency Management's response doctrine orders attention:
**people at risk → property at risk → transport → monitor.** This job stamps each
cluster's triage tier so an EOC intelligence team can see, at a glance, "which of
these should I look at first?"

This is a **keyword-derived hint, not a severity judgement.** There is no numeric
score and no model. Each tier is matched by plain case-insensitive substring against
the combined text of the cluster's member signals. The stamp carries the exact terms
that matched (for the winning tier only), plus a note making clear it is a hint.
An operator can see precisely why a cluster was tiered the way it was.

Eventual consistency — the corroborate job rebuilds ``db.clusters`` from scratch
every tick (``delete_many`` + ``insert_many``), so this stamp is wiped and simply
re-applied on the next prioritise tick — exactly like the admiralty stamp. The
``priority`` field exists only between the corroborate and prioritise ticks of a
cycle; do not "fix" its absence by persisting it elsewhere.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

VERSION = "priority-v1"

# Triage tiers in rank order (lowest rank wins). Terms came from the team session.
# (level, rank, terms)
TIERS = [
    ("people_at_risk", 1, [
        "trapped", "injured", "injuries", "missing", "buried",
        "rescue", "casualties", "evacuate", "evacuation", "stranded",
    ]),
    ("property", 2, [
        "collapsed", "damaged", "damage", "destroyed", "red stickered",
        "roof", "retaining wall",
    ]),
    ("transport", 3, [
        "road closed", "blocked", "impassable", "detour", "cordon",
        "closed to traffic",
    ]),
    ("monitor", 4, []),  # default — no terms, wins when nothing else matches
]


def prioritise_cluster(cluster: dict, members: list[dict]) -> dict:
    """Return the triage stamp for a cluster given its member signals.

    Pure and clock-free so it is trivially testable. ``run(db)`` adds ``"at"``.
    """
    # Build one lowercase blob from every member's title+text, using the classify.py
    # idiom: f"{doc.get('title') or ''} {doc.get('text') or ''}".
    blob = " ".join(
        f"{m.get('title') or ''} {m.get('text') or ''}" for m in members
    ).lower()

    # Scan tiers in rank order; the first (highest) tier with any hit wins.
    # Substring (not regex, not word-boundary) — the simplest rule to explain to
    # an operator. By design 'damage' also catches 'damaged' and 'rescue' catches
    # 'rescued'.
    for level, rank, terms in TIERS:
        if level == "monitor":
            break  # default tier, no terms to match
        evidence = sorted({t for t in terms if t in blob})
        if evidence:
            return {
                "level": level,
                "rank": rank,
                "evidence": evidence,
                "note": "Keyword-derived triage hint, not a severity judgement.",
                "version": VERSION,
            }

    # No hits in any tier — monitor is the default.
    return {
        "level": "monitor",
        "rank": 4,
        "evidence": [],
        "note": "Keyword-derived triage hint, not a severity judgement.",
        "version": VERSION,
    }


def run(db) -> dict:
    """Stamp every cluster in the database with its triage tier."""
    now = datetime.now(timezone.utc)

    cluster_count = 0
    for cluster in db.clusters.find({}):
        signal_ids = cluster.get("signal_ids", [])
        if signal_ids:
            members = list(
                db.signals.find(
                    {"signal_id": {"$in": signal_ids}}, {"title": 1, "text": 1}
                )
            )
        else:
            members = []
        stamp = prioritise_cluster(cluster, members)
        stamp["at"] = now
        db.clusters.update_one(
            {"_id": cluster["_id"]}, {"$set": {"priority": stamp}}
        )
        cluster_count += 1

    if cluster_count:
        log.info("prioritise: stamped %d clusters", cluster_count)

    return {"clusters": cluster_count}
