#!/usr/bin/env python3
"""Enrichment scheduler.

Cron-shaped, but not cron: each job has its own interval and the process tracks
when each is next due. That means the same image behaves identically under
docker compose, Azure Container Instances and on a laptop — none of which agree
on how to run crond in a container.

    ENRICHMENT_SCHEDULE=classify:30,geolocate:30,corroborate:60

Run every job once and exit:

    python run.py --once
    python run.py --once classify geolocate
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import jobs
from db import get_db
from pymongo.errors import PyMongoError

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("enrichment")

DEFAULT_SCHEDULE = "classify:30,geolocate:30,corroborate:60,admiralty:60,prioritise:60"
TICK = 5  # seconds between due-checks


def parse_schedule() -> dict[str, int]:
    """`name:seconds` pairs. Order matters — jobs run in the order listed."""
    raw = os.getenv("ENRICHMENT_SCHEDULE", DEFAULT_SCHEDULE)
    known = jobs.available()
    schedule: dict[str, int] = {}

    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        name, _, interval = entry.partition(":")
        name = name.strip()
        if name not in known:
            log.error("unknown job %r — available: %s", name, known)
            continue
        schedule[name] = int(interval) if interval.strip().isdigit() else 60

    if not schedule:
        log.error("nothing scheduled; check ENRICHMENT_SCHEDULE=%r", raw)
        sys.exit(1)

    # The schedule is set in three places — compose, the ACI template, and the
    # default above. Adding a job and updating only some of them means it runs
    # locally and quietly does not run in production. Nothing fails; the
    # enrichment simply never appears. Say so at startup instead.
    unscheduled = [name for name in known if name not in schedule]
    if unscheduled:
        log.warning(
            "job(s) %s exist but are not in ENRICHMENT_SCHEDULE — they will not run. "
            "Add them, or delete them if they are dead.",
            ", ".join(unscheduled),
        )

    return schedule


def wait_for_mongo(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get_db().command("ping")
            return True
        except PyMongoError:
            time.sleep(2)
    log.error("mongo never became reachable")
    return False


def record_run(name: str, **fields) -> None:
    """Write a heartbeat for the pipeline dashboard.

    Best-effort: a job that worked must not be reported as failed because the
    bookkeeping write failed afterwards.
    """
    try:
        get_db().component_runs.update_one(
            {"component": name},
            {
                "$set": {
                    "component": name,
                    "kind": "enrichment",
                    "last_run_at": datetime.now(timezone.utc),
                    **fields,
                },
                "$inc": {"run_count": 1},
                # A short history, so the dashboard can show that a job is
                # ticking rather than just that it ran once at some point.
                "$push": {
                    "recent": {
                        "$each": [{"at": datetime.now(timezone.utc), **fields}],
                        "$slice": -10,
                    }
                },
            },
            upsert=True,
        )
    except PyMongoError as exc:
        log.debug("could not record run for %s: %s", name, exc)


def run_job(name: str, interval: int | None = None) -> None:
    started = time.time()
    try:
        module = jobs.load(name)
        result = module.run(get_db())
        if any(result.values()):
            log.info("%s → %s", name, result)
        record_run(
            name,
            status="ok",
            duration_ms=int((time.time() - started) * 1000),
            result=result,
            version=getattr(module, "VERSION", None),
            interval_seconds=interval,
            error=None,
            description=(module.__doc__ or "").strip().split("\n")[0],
        )
    except Exception as exc:  # noqa: BLE001 — one failing job must not stop the others
        log.exception("job %s raised", name)
        record_run(
            name,
            status="error",
            duration_ms=int((time.time() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}"[:500],
            interval_seconds=interval,
        )


def main() -> None:
    once = "--once" in sys.argv
    explicit = [a for a in sys.argv[1:] if not a.startswith("-")]

    if not wait_for_mongo():
        sys.exit(1)

    if once:
        names = explicit or list(parse_schedule())
        log.info("running %s once", names)
        for name in names:
            run_job(name)
        return


    schedule = parse_schedule()
    log.info("scheduled: %s", ", ".join(f"{n} every {s}s" for n, s in schedule.items()))

    next_due = {name: 0.0 for name in schedule}
    while True:
        now = time.monotonic()
        for name, interval in schedule.items():
            if now >= next_due[name]:
                run_job(name, interval)
                next_due[name] = time.monotonic() + interval
        time.sleep(TICK)


if __name__ == "__main__":
    main()
