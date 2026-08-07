#!/usr/bin/env python3
"""Scraper runner.

One container image, many scrapers. Which collectors this container runs is
decided by SOURCES:

    SOURCES=rss                     one source per container (compose default)
    SOURCES=rss,geonet,mastodon     all of them in one container (ACI default)

Runs on a fixed interval rather than under cron, so the same image behaves
identically under docker compose, Azure Container Instances, or `python run.py`
on a laptop. RUN_ONCE=true collects a single time and exits.
"""

import logging
import os
import sys
import time

import sources
from common.sink import post_signals, wait_for_api

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("scraper")

INTERVAL = int(os.getenv("SCRAPE_INTERVAL_SECONDS", "600"))
RUN_ONCE = os.getenv("RUN_ONCE", "false").lower() == "true" or "--once" in sys.argv


def selected_sources() -> list[str]:
    names = [s.strip() for s in os.getenv("SOURCES", "rss").split(",") if s.strip()]
    known = sources.available()
    unknown = [n for n in names if n not in known]
    if unknown:
        log.error("unknown source(s) %s — available: %s", unknown, known)
        names = [n for n in names if n in known]
    if not names:
        log.error("no valid sources selected, exiting")
        sys.exit(1)
    return names


def run_once(names: list[str]) -> None:
    for name in names:
        try:
            collect = sources.load(name)
            signals = collect()
        except Exception:  # noqa: BLE001 — one broken source must not stop the loop
            log.exception("collector %s raised", name)
            continue

        if not signals:
            log.info("%s: nothing to send", name)
            continue

        result = post_signals(signals)
        log.info(
            "%s: %d collected → %d new, %d already known",
            name,
            len(signals),
            result.get("inserted", 0),
            result.get("duplicates", 0),
        )


def main() -> None:
    names = selected_sources()
    log.info("running sources %s every %ds (run_once=%s)", names, INTERVAL, RUN_ONCE)

    if not wait_for_api():
        sys.exit(1)

    while True:
        started = time.time()
        run_once(names)
        if RUN_ONCE:
            return
        elapsed = time.time() - started
        time.sleep(max(5, INTERVAL - elapsed))


if __name__ == "__main__":
    main()
