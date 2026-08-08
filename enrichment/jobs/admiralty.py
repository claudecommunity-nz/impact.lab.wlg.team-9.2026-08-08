"""Admiralty Code grading enrichment job.

Wellington's EOC grades information with the Admiralty Code:
source reliability A–F × information credibility 1–6.

Note on eventual consistency for clusters:
The corroborate job rebuilds db.clusters from scratch every tick
(delete_many + insert_many), so any admiralty stamp on a cluster is wiped
and must simply be re-stamped on the next admiralty tick.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

VERSION = "admiralty-v1"

RELIABILITY_MEANING = {
    "A": "Completely reliable",
    "B": "Usually reliable",
    "C": "Fairly reliable",
    "D": "Not usually reliable",
    "E": "Unreliable",
    "F": "Reliability cannot be judged",
}

CREDIBILITY_MEANING = {
    1: "Confirmed by other sources",
    2: "Probably true",
    3: "Possibly true",
    4: "Doubtfully true",
    5: "Improbable",
    6: "Truth cannot be judged",
}

_REL_RANK = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5}


def source_reliability(source: dict) -> tuple[str, str]:
    """Determine source reliability letter (A-F) and rationale."""
    if not isinstance(source, dict):
        source = {}
    stype = (source.get("type") or "").lower()
    sname = (source.get("name") or "").lower()

    if stype == "geonet":
        return "A", "source type 'geonet' → A"
    elif stype == "welectricity":
        return "A", "source type 'welectricity' → A"
    elif stype == "nzta":
        return "A", "source type 'nzta' → A"
    elif stype == "mastodon":
        return "F", "source type 'mastodon' → F"
    elif stype == "fixtures":
        return "F", "source type 'fixtures' → F"
    elif stype == "screenshot":
        return "F", "source type 'screenshot' → F"

    substring_rules = [
        ("metservice", "A"),
        ("nz police", "A"),
        ("police", "A"),
        ("fire and emergency", "A"),
        ("fenz", "A"),
        ("geonet", "A"),
        ("civil defence", "A"),
        ("nema", "A"),
        ("wellington electricity", "A"),
        ("waka kotahi", "A"),
        ("nzta", "A"),
        ("transpower", "A"),
        ("wellington water", "A"),
        ("rnz", "B"),
        ("nz herald", "B"),
        ("the post", "B"),
        ("scoop", "C"),
    ]

    for sub, rel in substring_rules:
        if sub in sname:
            return rel, f"source name substring '{sub}' → {rel}"

    return "F", "default source reliability → F"


def info_credibility(corroboration: str | None, reliability: str) -> tuple[int, str]:
    """Determine information credibility digit (1-6) and rationale."""
    if corroboration == "multi_source":
        return 2, "corroboration multi_source → 2"
    elif corroboration == "two_sources":
        return 3, "corroboration two_sources → 3"
    else:
        if reliability in ("A", "B"):
            return 3, f"single source (reliability {reliability}) → 3"
        else:
            return 6, f"single source (reliability {reliability}) → 6"


def grade_signal(doc: dict) -> dict:
    """Stamp Admiralty Code grading for a single signal document."""
    source = doc.get("source") or {}
    enrich = doc.get("enrichment") or {}
    corrob_dict = enrich.get("corroborate") or {}
    corrob = corrob_dict.get("corroboration")

    rel, rel_rat = source_reliability(source)
    cred, cred_rat = info_credibility(corrob, rel)

    return {
        "grade": f"{rel}{cred}",
        "source_reliability": rel,
        "info_credibility": cred,
        "meaning": f"{RELIABILITY_MEANING[rel]} · {CREDIBILITY_MEANING[cred]}",
        "rationale": [rel_rat, cred_rat],
        "note": (
            "Automated Admiralty grading. Credibility 1 (confirmed) is never assigned "
            "by this pipeline — confirmation is a human decision."
        ),
        "method": VERSION,
        "version": VERSION,
    }


def grade_cluster(cluster: dict, members: list[dict]) -> dict:
    """Stamp Admiralty Code grading for a cluster document based on member signals."""
    if members:
        member_rels = [source_reliability(m.get("source") or {}) for m in members]
        best_rel, best_rel_rat = min(member_rels, key=lambda r: _REL_RANK.get(r[0], 5))
        rel_rat = f"best member source reliability ({best_rel}) across {len(members)} signals: {best_rel_rat}"
    else:
        best_rel = "F"
        rel_rat = "no cluster members → default reliability F"

    corrob = cluster.get("corroboration")
    cred, cred_rat = info_credibility(corrob, best_rel)

    return {
        "grade": f"{best_rel}{cred}",
        "source_reliability": best_rel,
        "info_credibility": cred,
        "meaning": f"{RELIABILITY_MEANING[best_rel]} · {CREDIBILITY_MEANING[cred]}",
        "rationale": [rel_rat, cred_rat],
        "note": (
            "Automated Admiralty grading. Credibility 1 (confirmed) is never assigned "
            "by this pipeline — confirmation is a human decision."
        ),
        "method": VERSION,
        "version": VERSION,
    }


def run(db) -> dict:
    """Grade all signals and clusters in the database."""
    now = datetime.now(timezone.utc)

    # 1. Signals
    signal_cursor = db.signals.find({}, {"signal_id": 1, "source": 1, "enrichment.corroborate": 1}).limit(2000)
    sig_count = 0
    for doc in signal_cursor:
        stamp = grade_signal(doc)
        stamp["at"] = now
        db.signals.update_one({"_id": doc["_id"]}, {"$set": {"enrichment.admiralty": stamp}})
        sig_count += 1

    # 2. Clusters
    cluster_cursor = db.clusters.find({})
    cluster_count = 0
    for cluster in cluster_cursor:
        signal_ids = cluster.get("signal_ids", [])
        if signal_ids:
            members = list(db.signals.find({"signal_id": {"$in": signal_ids}}, {"source": 1, "enrichment.corroborate": 1}))
        else:
            members = []
        stamp = grade_cluster(cluster, members)
        stamp["at"] = now
        db.clusters.update_one({"_id": cluster["_id"]}, {"$set": {"admiralty": stamp}})
        cluster_count += 1

    if sig_count or cluster_count:
        log.info("admiralty: graded %d signals, %d clusters", sig_count, cluster_count)

    return {"signals": sig_count, "clusters": cluster_count}
