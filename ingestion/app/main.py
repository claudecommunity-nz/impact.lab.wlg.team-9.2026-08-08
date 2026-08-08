"""Ingestion API — the one door into the signal store.

Scrapers POST here. The UI and any other consumer read from here. Enrichment
jobs are the exception: they talk to Mongo directly, because they rewrite
documents in place.

Everything this API returns is unverified public information. The
`verification` block on every signal says so explicitly, and the GeoJSON
carries it in properties so a downstream map can't lose it by accident.
"""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError, PyMongoError

from . import screenshots
from .db import ensure_indexes, get_db
from .models import CitizenReportIn, IngestResult, LocationHint, RunReport, SignalBatch, SignalIn, SourceRef

# Sources anyone can post to with no editorial gate — the ones a human should
# look at before the map treats them as anything more than "someone said so".
REVIEW_SOURCE_TYPES = ["mastodon", "rss", "citizen_report", "screenshot"]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ingestion")

DISCLAIMER = (
    "Unverified public information. Signals are candidates for triage, not "
    "confirmed fact. In an emergency, call 111."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compose waits for Mongo's healthcheck, but an Azure container group has no
    # such thing — every container starts at once. Retry rather than crash-loop.
    for attempt in range(30):
        try:
            ensure_indexes(get_db())
            break
        except PyMongoError as exc:
            log.warning("mongo not ready (%s), retrying", exc)
            await asyncio.sleep(2)
    else:
        log.error("mongo never became reachable; starting anyway, /healthz will fail")
    yield


app = FastAPI(
    title="Team 9 — emerging impact signals",
    description=DISCLAIMER,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_REL = re.compile(r"^(\d+)([mhd])$")


def parse_since(value: str | None) -> datetime | None:
    """Accept `90m`, `6h`, `7d` or an ISO timestamp."""
    if not value:
        return None
    m = _REL.match(value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return datetime.now(timezone.utc) - delta
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"cannot parse since={value!r}; use 6h, 2d or ISO 8601")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def clean(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


def public_signal(doc: dict) -> dict:
    """A signal doc safe to hand to a client.

    Media from a publisher is a link to wherever they already host it. The
    one exception is a shared screenshot, which nobody else hosts: those
    carry a `/screenshots/<name>` path served by this API out of the store
    the collector wrote them to.

    A citizen report's contact details, if it has any, are popped here and
    nowhere else reads the field — this whole API is unauthenticated, so
    anything not stripped in this one function is public. The review team
    looks contact details up directly in Mongo if they need to follow up.
    """
    doc = clean(doc)
    doc.pop("reporter_contact", None)
    doc["media"] = [{"type": m.get("type", "image"), "url": m["url"]} for m in doc.get("media") or []]
    return doc


def signal_filter(
    since: str | None = None,
    issue_type: str | None = None,
    source_type: str | None = None,
    located: bool = False,
) -> dict:
    q: dict = {}
    cutoff = parse_since(since)
    if cutoff:
        # Fall back to ingest time for sources that don't publish a timestamp.
        q["$or"] = [{"published_at": {"$gte": cutoff}}, {"ingested_at": {"$gte": cutoff}}]
    if issue_type:
        q["enrichment.classify.issue_type"] = issue_type
    if source_type:
        q["source.type"] = source_type
    if located:
        q["geo"] = {"$exists": True}
    return q


def signal_feature(doc: dict) -> dict:
    """One signal as a GeoJSON point feature, reliability included."""
    enrich = doc.get("enrichment") or {}
    classify = enrich.get("classify") or {}
    geoloc = enrich.get("geolocate") or {}
    corrob = enrich.get("corroborate") or {}
    admiralty = enrich.get("admiralty") or {}
    return {
        "type": "Feature",
        "geometry": doc["geo"],
        "properties": {
            "signal_id": doc["signal_id"],
            "title": doc.get("title") or doc["text"][:120],
            "text": doc["text"][:600],
            "url": doc.get("url"),
            "source_name": doc["source"]["name"],
            "source_type": doc["source"]["type"],
            "published_at": _iso(doc.get("published_at")),
            "ingested_at": _iso(doc.get("ingested_at")),
            "issue_type": classify.get("issue_type", "unclassified"),
            # Never null: MapLibre paint expressions error on a null property,
            # and these two drive opacity and colour.
            "issue_confidence": classify.get("confidence") or 0.0,
            "issue_method": classify.get("method"),
            "issue_evidence": classify.get("matched_terms", []),
            "place": geoloc.get("place"),
            "location_confidence": geoloc.get("confidence") or 0.0,
            "location_method": geoloc.get("method"),
            "cluster_id": corrob.get("cluster_id"),
            "source_count": corrob.get("source_count", 1),
            "admiralty_grade": admiralty.get("grade"),
            "admiralty_reliability": admiralty.get("source_reliability"),
            "admiralty_credibility": admiralty.get("info_credibility"),
            "admiralty_meaning": admiralty.get("meaning"),
            "verification_status": doc.get("verification", {}).get("status", "unverified"),
        },
    }


def cluster_feature(doc: dict, verified_count: int = 0) -> dict:
    adm = doc.get("admiralty") or {}
    return {
        "type": "Feature",
        "geometry": doc["geo"],
        "properties": {
            "cluster_id": doc["cluster_id"],
            "issue_type": doc["issue_type"],
            "source_count": doc["source_count"],
            "signal_count": doc["signal_count"],
            "sources": doc["sources"],
            "corroboration": doc.get("corroboration"),
            "admiralty_grade": adm.get("grade"),
            "admiralty_reliability": adm.get("source_reliability"),
            "admiralty_credibility": adm.get("info_credibility"),
            "admiralty_meaning": adm.get("meaning"),
            "first_seen": _iso(doc.get("first_seen")),
            "last_seen": _iso(doc.get("last_seen")),
            "location_confidence": doc.get("location_confidence") or 0.0,
            "place": doc.get("place"),
            # A cluster is "verified" once a human has judged at least one of
            # its member reports plausible — not all of them, and this is not
            # confirmation that the underlying event happened.
            "verified_count": verified_count,
            "verification_status": "verified" if verified_count > 0 else "unverified",
            # Carried into the map layer so replayed demo data is labelled
            # everywhere it appears, not just in the list.
            "contains_synthetic": bool(doc.get("contains_synthetic")),
            "note": doc.get("note"),
        },
    }


def _verified_counts_by_cluster(db, clusters: list[dict]) -> dict[str, int]:
    """How many of each cluster's member signals a human has verified.

    One query for the whole page of clusters rather than one per cluster —
    the map can ask for a few hundred of these on every 30s refresh.
    """
    all_ids = {sid for c in clusters for sid in c.get("signal_ids", [])}
    if not all_ids:
        return {}
    verified_ids = {
        d["signal_id"]
        for d in db.signals.find(
            {"signal_id": {"$in": list(all_ids)}, "verification.status": "verified"},
            {"signal_id": 1},
        )
    }
    return {
        c["cluster_id"]: sum(1 for sid in c.get("signal_ids", []) if sid in verified_ids)
        for c in clusters
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else dt


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    try:
        get_db().command("ping")
    except Exception as exc:  # noqa: BLE001 — health endpoint reports, never raises
        raise HTTPException(503, f"mongo unreachable: {exc}")
    return {"ok": True}


@app.post("/signals", response_model=IngestResult)
def ingest(batch: SignalBatch):
    """Accept scraped items. Re-posting the same item is a no-op."""
    db = get_db()
    now = datetime.now(timezone.utc)
    inserted = duplicates = 0

    for sig in batch.signals:
        doc = build_document(sig, now)
        try:
            db.signals.insert_one(doc)
            inserted += 1
        except DuplicateKeyError:
            # Seen it before. Bump last_seen so "still being talked about" is
            # visible without creating a second signal.
            db.signals.update_one(
                {"signal_id": doc["signal_id"]},
                {"$set": {"last_seen_at": now}, "$inc": {"seen_count": 1}},
            )
            duplicates += 1

    if inserted:
        log.info("ingested %d new signals (%d duplicates)", inserted, duplicates)
    return IngestResult(received=len(batch.signals), inserted=inserted, duplicates=duplicates)


def build_document(sig: SignalIn, now: datetime) -> dict:
    doc = {
        "signal_id": sig.signal_id(),
        "source": sig.source.model_dump(),
        "text": sig.text,
        "title": sig.title,
        "url": sig.url,
        "external_id": sig.external_id,
        "published_at": sig.published_at,
        "ingested_at": now,
        "last_seen_at": now,
        "seen_count": 1,
        "media": [m.model_dump() for m in sig.media],
        "raw": sig.raw,
        "enrichment": {},
        # Nothing in this pipeline verifies anything. Stamped on every document
        # at ingest so it can't be forgotten downstream. `method`/`verified_*`
        # stay null until a human reviews it via /signals/{id}/verify.
        "verification": {
            "status": "unverified",
            "method": None,
            "note": DISCLAIMER,
            "verified_at": None,
            "verified_by": None,
        },
    }
    if sig.location_hint:
        hint = sig.location_hint
        doc["geo"] = {"type": "Point", "coordinates": [hint.lon, hint.lat]}
        doc["enrichment"]["geolocate"] = {
            "place": hint.place,
            "lat": hint.lat,
            "lon": hint.lon,
            "confidence": hint.confidence,
            "method": hint.method,
            "matched": [],
            "version": "source-hint",
            "at": now,
        }
    return doc


REPORT_DISCLAIMER = (
    "Thanks — this has been added to the review queue. It is not confirmed, and "
    "it is not a substitute for calling 111 in an emergency."
)


@app.post("/reports")
def submit_report(body: CitizenReportIn):
    """A member of the public reporting something directly through the site.

    Goes through the exact same unverified-until-reviewed flow as a scraped
    Mastodon post or RSS item (see REVIEW_SOURCE_TYPES) — it never counts for
    anything on the map until a human checks it on /review/queue. `source` is
    stamped here rather than accepted from the request, so a caller cannot
    claim to be a higher-trust source than "someone submitted this".
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    location_text = (body.location_text or "").strip() or None

    sig = SignalIn(
        source=SourceRef(
            type="citizen_report",
            name="Public report",
            collector="citizen-report-form",
            local=True,
        ),
        text=body.text.strip(),
        title=location_text,
        location_hint=(
            LocationHint(lat=body.lat, lon=body.lon, place=location_text, confidence=0.9, method="citizen_pin")
            if body.lat is not None and body.lon is not None
            else None
        ),
    )
    doc = build_document(sig, now)
    contact = (body.contact or "").strip()
    if contact:
        doc["reporter_contact"] = contact

    try:
        db.signals.insert_one(doc)
    except DuplicateKeyError:
        pass  # identical text submitted twice in a row; treat as accepted either way

    return {"ok": True, "signal_id": doc["signal_id"], "disclaimer": REPORT_DISCLAIMER}


@app.get("/signals")
def list_signals(
    since: str | None = Query(None, description="6h, 2d or ISO 8601"),
    issue_type: str | None = None,
    source_type: str | None = None,
    located: bool = False,
    q: str | None = Query(None, description="substring match on title and text"),
    unlocated_only: bool = Query(False, description="only signals no place could be found for"),
    sort: str = Query("published_at", pattern="^(published_at|ingested_at)$"),
    limit: int = Query(200, le=2000),
    offset: int = Query(0, ge=0),
):
    """The raw store, paged.

    Deliberately unfiltered by map-ability: `unlocated_only` exists because the
    signals that never reach the map are the ones most worth eyeballing — they
    are real collected items the gazetteer could not place, and they are
    invisible everywhere else in the interface.
    """
    db = get_db()
    query = signal_filter(since, issue_type, source_type, located)

    if unlocated_only:
        query["geo"] = {"$exists": False}
    if q:
        # Escaped: a user typing "(" into a search box should get no results,
        # not a 500 from an invalid regular expression.
        pattern = re.escape(q.strip())
        query["$and"] = query.get("$and", []) + [
            {"$or": [{"title": {"$regex": pattern, "$options": "i"}},
                     {"text": {"$regex": pattern, "$options": "i"}}]}
        ]

    total = db.signals.count_documents(query)
    cur = db.signals.find(query).sort(sort, -1).skip(offset).limit(limit)

    return {
        "disclaimer": DISCLAIMER,
        "total": total,
        "offset": offset,
        "limit": limit,
        "signals": [public_signal(d) for d in cur],
    }


@app.get("/signals.geojson")
def signals_geojson(
    since: str | None = Query("7d"),
    issue_type: str | None = None,
    source_type: str | None = None,
    limit: int = Query(1000, le=5000),
):
    """Located signals as GeoJSON — drop straight into MapLibre."""
    db = get_db()
    q = signal_filter(since, issue_type, source_type, located=True)
    cur = db.signals.find(q).sort("published_at", -1).limit(limit)
    return {
        "type": "FeatureCollection",
        "disclaimer": DISCLAIMER,
        "features": [signal_feature(d) for d in cur],
    }


@app.get("/clusters")
def list_clusters(
    min_sources: int = Query(1, ge=1),
    since: str | None = Query("7d"),
    limit: int = Query(200, le=1000),
):
    """Groups of signals that may describe the same event.

    `source_count` is how many *independent publishers* the group draws on. It
    is a reason to look, not evidence that the event is real.
    """
    db = get_db()
    q: dict = {"source_count": {"$gte": min_sources}}
    cutoff = parse_since(since)
    if cutoff:
        q["last_seen"] = {"$gte": cutoff}
    cur = db.clusters.find(q).sort([("source_count", -1), ("last_seen", -1)]).limit(limit)
    return {
        "disclaimer": DISCLAIMER,
        "clusters": [clean(d) for d in cur],
    }


@app.get("/clusters.geojson")
def clusters_geojson(
    min_sources: int = Query(1, ge=1),
    since: str | None = Query("7d"),
    limit: int = Query(500, le=2000),
):
    db = get_db()
    q: dict = {"source_count": {"$gte": min_sources}, "geo": {"$exists": True}}
    cutoff = parse_since(since)
    if cutoff:
        q["last_seen"] = {"$gte": cutoff}
    clusters = list(db.clusters.find(q).sort([("source_count", -1), ("last_seen", -1)]).limit(limit))
    verified_counts = _verified_counts_by_cluster(db, clusters)
    return {
        "type": "FeatureCollection",
        "disclaimer": DISCLAIMER,
        "features": [cluster_feature(d, verified_counts.get(d["cluster_id"], 0)) for d in clusters],
    }


@app.get("/clusters/{cluster_id}")
def get_cluster(cluster_id: str):
    db = get_db()
    cluster = db.clusters.find_one({"cluster_id": cluster_id})
    if not cluster:
        raise HTTPException(404, "no such cluster")
    signals = db.signals.find({"signal_id": {"$in": cluster["signal_ids"]}}).sort("published_at", 1)
    return {
        "disclaimer": DISCLAIMER,
        "cluster": clean(cluster),
        "signals": [public_signal(s) for s in signals],
    }


# --------------------------------------------------------------------------
# human review — a person looks at low-trust scraped items and says whether
# they're plausible. This is a judgement call, not confirmation; the wording
# returned to the client says so every time.
# --------------------------------------------------------------------------

REVIEW_DISCLAIMER = (
    "These come from sources anyone can post to, with no editorial check. "
    "Confirming here records that a person found an item plausible — it is not "
    "official confirmation that the event happened. In an emergency, call 111."
)


@app.get("/screenshots/{name}")
def get_screenshot(name: str):
    """The image a screenshot-sourced signal was extracted from.

    Served through the API rather than linked directly so the credential that
    opens the store never reaches a public response. The image itself is
    unredacted — the poster's name and profile photo are still in it, even
    though they were stripped out of the signal's text — so the interface
    blurs it until a reviewer chooses to look.
    """
    if not screenshots.is_valid_name(name):
        raise HTTPException(404, "no such screenshot")
    image = screenshots.read(name)
    if image is None:
        raise HTTPException(404, "no such screenshot")
    return Response(
        content=image,
        media_type=screenshots.content_type(name),
        headers={
            # Content-addressed by sha1, so it can never change under the name.
            "Cache-Control": "private, max-age=86400",
            # Not for indexing: this is unredacted third-party content.
            "X-Robots-Tag": "noindex, noimageindex",
        },
    )


class VerifyRequest(BaseModel):
    reviewer: str | None = None
    note: str | None = None


@app.get("/review/queue")
def review_queue(
    source_type: str = Query(",".join(REVIEW_SOURCE_TYPES), description="comma-separated source types"),
    since: str | None = Query("3d", description="6h, 2d or ISO 8601"),
    limit: int = Query(100, le=500),
):
    """Unreviewed items from low-trust sources, newest first."""
    db = get_db()
    types = [t.strip() for t in source_type.split(",") if t.strip()]
    q: dict = {"verification.status": "unverified"}
    if types:
        q["source.type"] = {"$in": types}
    cutoff = parse_since(since)
    if cutoff:
        q["$or"] = [{"published_at": {"$gte": cutoff}}, {"ingested_at": {"$gte": cutoff}}]
    cur = db.signals.find(q).sort("ingested_at", -1).limit(limit)
    return {
        "disclaimer": REVIEW_DISCLAIMER,
        "signals": [public_signal(d) for d in cur],
    }


def _set_verification(signal_id: str, status: str, method: str, default_note: str, body: VerifyRequest) -> dict:
    db = get_db()
    now = datetime.now(timezone.utc)
    reviewer = (body.reviewer or "").strip() or None
    note = (body.note or "").strip() or default_note
    result = db.signals.update_one(
        {"signal_id": signal_id},
        {
            "$set": {
                "verification.status": status,
                "verification.method": method,
                "verification.note": note,
                "verification.verified_at": now,
                "verification.verified_by": reviewer,
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(404, "no such signal")
    return public_signal(db.signals.find_one({"signal_id": signal_id}))


@app.post("/signals/{signal_id}/verify")
def verify_signal(signal_id: str, body: VerifyRequest = VerifyRequest()):
    """A human reviewed this item and judged it plausible."""
    return _set_verification(
        signal_id,
        status="verified",
        method="human_reviewed",
        default_note="Confirmed plausible by a human reviewer. This is a judgement call, not official confirmation.",
        body=body,
    )


@app.post("/signals/{signal_id}/dismiss")
def dismiss_signal(signal_id: str, body: VerifyRequest = VerifyRequest()):
    """A human reviewed this item and judged it not worth carrying forward."""
    return _set_verification(
        signal_id,
        status="dismissed",
        method="human_reviewed",
        default_note="Reviewed and set aside — judged not plausible or not relevant.",
        body=body,
    )


VERIFIED_DISCLAIMER = (
    "A human reviewer judged this plausible — that is not official confirmation "
    "that the event happened. In an emergency, call 111."
)


@app.get("/verified")
def verified_signals(
    since: str | None = Query("7d", description="6h, 2d or ISO 8601, filtered on when it was verified"),
    limit: int = Query(100, le=500),
):
    """Everything currently marked verified, newest confirmation first."""
    db = get_db()
    q: dict = {"verification.status": "verified"}
    cutoff = parse_since(since)
    if cutoff:
        q["verification.verified_at"] = {"$gte": cutoff}
    cur = db.signals.find(q).sort("verification.verified_at", -1).limit(limit)
    return {
        "disclaimer": VERIFIED_DISCLAIMER,
        "signals": [public_signal(d) for d in cur],
    }


@app.post("/runs")
def report_run(run: RunReport):
    """Record how a pipeline component's last run went.

    Upserted per component rather than appended, plus a short rolling history.
    The dashboard needs "is this thing alive and what did it just do", not an
    audit log, and an unbounded run log on a free-tier database is a slow leak.
    """
    now = datetime.now(timezone.utc)
    doc = run.model_dump()
    targets = doc.pop("targets", [])
    summary = {"at": now, "status": doc["status"], "result": doc.get("result", {})}

    get_db().component_runs.update_one(
        {"component": run.component},
        {
            "$set": {**doc, "last_run_at": now, "targets": targets},
            "$inc": {"run_count": 1},
            "$push": {"recent": {"$each": [summary], "$slice": -10}},
        },
        upsert=True,
    )
    return {"ok": True}


@app.get("/pipeline")
def pipeline():
    """Everything the dashboard needs: what is running, when it last ran, what it polls.

    `stale` is computed here rather than in the browser so every consumer
    agrees on it. A component is stale once it has missed roughly two of its
    own intervals — that is the difference between "quiet source" and "stopped
    working", which is the whole question a pipeline dashboard exists to answer.
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    components = []

    for doc in db.component_runs.find().sort("component", 1):
        clean(doc)
        last = doc.get("last_run_at")
        interval = doc.get("interval_seconds") or 600
        age = (now - last).total_seconds() if isinstance(last, datetime) else None

        doc["last_run_at"] = _iso(last)
        doc["age_seconds"] = round(age) if age is not None else None
        doc["stale"] = age is not None and age > max(120, interval * 2.5)
        for entry in doc.get("recent", []):
            entry["at"] = _iso(entry.get("at"))
        for target in doc.get("targets", []):
            target["at"] = _iso(target.get("at"))
        components.append(doc)

    return {
        "disclaimer": DISCLAIMER,
        "generated_at": _iso(now),
        "components": components,
        "counts": {
            "total": len(components),
            "healthy": sum(1 for c in components if c["status"] == "ok" and not c["stale"]),
            "erroring": sum(1 for c in components if c["status"] == "error"),
            "stale": sum(1 for c in components if c["stale"]),
        },
    }


@app.get("/stats")
def stats():
    """Enough to see, at a glance, whether the pipeline is actually running."""
    db = get_db()
    total = db.signals.count_documents({})

    def group(field):
        return {
            d["_id"] or "none": d["n"]
            for d in db.signals.aggregate(
                [{"$group": {"_id": f"${field}", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]
            )
        }

    latest = db.signals.find_one(sort=[("ingested_at", -1)])
    return {
        "disclaimer": DISCLAIMER,
        "signals": {
            "total": total,
            "by_source_type": group("source.type"),
            "by_issue_type": group("enrichment.classify.issue_type"),
            "located": db.signals.count_documents({"geo": {"$exists": True}}),
            "classified": db.signals.count_documents({"enrichment.classify": {"$exists": True}}),
            "latest_ingest": _iso(latest["ingested_at"]) if latest else None,
        },
        "clusters": {
            "total": db.clusters.count_documents({}),
            "multi_source": db.clusters.count_documents({"source_count": {"$gte": 2}}),
        },
    }
