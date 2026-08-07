"""Issue-type classification.

Keyword rules, not a model. Two reasons, both about the problem statement
rather than about effort: the rule that fired is recordable, so the interface
can show *why* a signal was tagged "landslide"; and a rule can't hallucinate a
hazard that nobody mentioned.

Confidence here means "how strongly the text matches this rule set" — nothing
more. It is not a probability that the event is real. Nothing in this pipeline
estimates that, because nothing in this pipeline verifies anything.

Where an LLM would slot in: a second job writing to `enrichment.classify_llm`,
alongside this one rather than replacing it, so the two can be compared and
disagreement surfaced.
"""

import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

VERSION = "keyword-v1"
BATCH = 500

# Ordered most-specific first: the first rule with the top score wins ties, so
# "surface flooding closed the road" classifies as flooding, not road closure.
RULES: list[tuple[str, list[str]]] = [
    ("tsunami", [
        "tsunami", "marine threat", "evacuation zone", "evacuate the coast",
    ]),
    ("earthquake", [
        "earthquake", "quake", "aftershock", "magnitude", "shaking", "seismic",
    ]),
    ("landslide", [
        "landslide", "land slide", "slip", "slips", "slipped", "rockfall",
        "rock fall", "subsidence", "retaining wall", "hillside gave way", "debris",
    ]),
    ("flooding", [
        "flood", "flooded", "flooding", "surface water", "surface flooding",
        "inundation", "inundated", "submerged", "overtopping", "overtopped",
        "stormwater", "storm water", "water over the road", "king tide",
        "high tide", "swell", "awash",
    ]),
    ("fire", [
        "fire", "blaze", "smoke", "fenz", "fire service", "burning", "alight",
    ]),
    ("power_outage", [
        "power cut", "power out", "power outage", "no power", "powercut",
        "lines down", "power lines", "wellington electricity", "blackout",
    ]),
    ("water_supply", [
        "burst main", "water main", "boil water", "no water", "water outage",
        "low pressure", "wellington water", "wastewater", "sewage",
    ]),
    ("wind_damage", [
        "gale", "wind damage", "gusts", "roofing iron", "roof lifted",
        "trees down", "tree down", "branches down", "fence down", "blown over",
    ]),
    ("road_closure", [
        "road closed", "road closure", "closed to traffic", "lane blocked",
        "lane closed", "detour", "cordon", "impassable", "blocking the road",
        "traffic diverted",
    ]),
    ("transport_disruption", [
        "cancelled", "cancellation", "delays", "disruption", "metlink",
        "ferry", "sailings", "buses replacing", "train services", "flight",
        "stranded", "backed up",
    ]),
    ("building_damage", [
        "collapsed", "structural damage", "damaged building", "cracked",
        "condemned", "red stickered", "evacuated the building",
    ]),
    ("injury_rescue", [
        "injured", "injuries", "rescue", "rescued", "trapped", "ambulance",
        "casualties", "missing person",
    ]),
]

_COMPILED = [
    (label, re.compile(r"(?<![a-z])(" + "|".join(re.escape(t) for t in terms) + r")", re.I))
    for label, terms in RULES
]


def classify_text(text: str) -> dict:
    scores: dict[str, list[str]] = {}
    for label, pattern in _COMPILED:
        matches = sorted({m.lower() for m in pattern.findall(text or "")})
        if matches:
            scores[label] = matches

    if not scores:
        return {
            "issue_type": "unclassified",
            "confidence": 0.0,
            "matched_terms": [],
            "alternatives": [],
            "method": VERSION,
        }

    ranked = sorted(scores.items(), key=lambda kv: (-len(kv[1]), [l for l, _ in RULES].index(kv[0])))
    top_label, top_matches = ranked[0]

    # More distinct matching terms means a firmer keyword match, capped well
    # short of 1.0 — a keyword rule should never look certain.
    confidence = round(min(0.85, 0.35 + 0.15 * len(top_matches)), 2)

    return {
        "issue_type": top_label,
        "confidence": confidence,
        "matched_terms": top_matches,
        # Kept so the UI can say "also matched: flooding, road closure" rather
        # than silently collapsing a multi-hazard report to one label.
        "alternatives": [
            {"issue_type": label, "matched_terms": matches} for label, matches in ranked[1:4]
        ],
        "method": VERSION,
    }


def run(db) -> dict:
    """Classify anything not already stamped with this rule-set version."""
    query = {"enrichment.classify.version": {"$ne": VERSION}}
    cursor = db.signals.find(query, {"signal_id": 1, "title": 1, "text": 1}).limit(BATCH)

    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    processed = 0

    for doc in cursor:
        blob = f"{doc.get('title') or ''} {doc.get('text') or ''}"
        result = classify_text(blob)
        result["version"] = VERSION
        result["at"] = now
        db.signals.update_one({"_id": doc["_id"]}, {"$set": {"enrichment.classify": result}})
        counts[result["issue_type"]] = counts.get(result["issue_type"], 0) + 1
        processed += 1

    if processed:
        log.info("classify: %d signals → %s", processed, counts)
    return {"processed": processed, "by_type": counts}
