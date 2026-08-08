# Impact Lab Wellington — Team 9

**Wellington City Council Emergency Management × Claude Code Community NZ**
Saturday 8 August 2026 · Waimanga Room, Wellington City Council

---

## Problem 03 — Identify and verify emerging local impacts from public information

> How might we use public online information to identify where emergency impacts may be emerging, while making the reliability and limitations of that information clear?

A prototype could collect relevant public posts, local news and community reports; identify likely locations and issue types; and show where several independent sources appear to describe the same event. It would not present social-media content as verified fact. It would identify signals for an intelligence team to investigate.

There may be an opportunity to develop this option in collaboration with doctoral research at Massey University's Joint Centre for Disaster Research, on multimodal generative AI for disaster situational awareness using social media. Early discussions with the WCC Emergency Management team have already identified possible overlap. Any collaboration would need to be agreed with the researcher and Massey University.

**Desired outcome:** WCC can detect possible impacts earlier and direct staff attention to matters needing confirmation.

*The common theme is improving the flow and use of information between communities and Council before and during an event.*

---

## Running it

You need Docker. Nothing else — no Python, no Node, no Azure account.

```bash
git clone git@github.com:claudecommunity-nz/impact.lab.wlg.team-9.2026-08-08.git
cd impact.lab.wlg.team-9.2026-08-08

docker compose up --build -d      # or: make up
open http://localhost:8080
```

First build takes a few minutes. After that the collectors poll immediately and
the map fills in within about a minute — a cold start reaches roughly 130
signals and 50 groups in 90 seconds.

| | |
|---|---|
| UI | http://localhost:8080 |
| Pipeline dashboard | http://localhost:8080/#pipeline |
| Raw data | http://localhost:8080/#data |
| API docs | http://localhost:8000/docs |
| What's in the store | http://localhost:8000/stats |
| Signals as GeoJSON | http://localhost:8000/signals.geojson |
| Groups as GeoJSON | http://localhost:8000/clusters.geojson |

```bash
make logs        # tail everything
make stats       # what's in the store right now
make enrich      # run every enrichment job once, now
make down        # stop, keep the database
make clean       # stop and drop the database volume
```

Most collectors need no credentials — GeoNet, the RSS feeds, Mastodon, NZTA and
Wellington Electricity are all public. Two exceptions:

**The Reddit corpus** needs an API key. Without it that one collector reports
itself as `skipped` on the pipeline dashboard and everything else runs normally.
With a key:

```bash
export REDDIT_API_KEY="paste-the-key"
docker compose up -d scraper-reddit
```

If you have access to the team's Azure Key Vault, take it from there rather
than keeping a copy on disk:

```bash
export REDDIT_API_KEY="$(az keyvault secret show \
  --vault-name team9-kv-f76f44cb --name reddit-api-key --query value -o tsv)"
```

**The fixture collector** needs nothing and is local-only. It replays synthetic
Wellington scenarios so the map has content with no network at all, and every
item it produces is labelled as sample data through the API, the map and the
table. It is deliberately not part of the deployed configuration.

### If something looks wrong

Open the **pipeline dashboard** first — `http://localhost:8080/#pipeline`. It
shows every collector and enrichment job, when each last ran, what it polled
and what it returned. A collector that has quietly stopped looks identical to a
quiet one from the map, and this is the page that tells them apart.

## Deployment

The prototype was deployed to Azure Container Instances during the build, with
MongoDB Atlas for storage, secrets in Azure Key Vault and HTTPS via Caddy.

**Automatic deployment is currently switched off.** The GitHub Actions workflow
is disabled and its Azure credentials have been removed from the repository, so
nothing deploys on push. Everything needed to turn it back on is in
[deploy/azure/README.md](deploy/azure/README.md) — see *Re-enabling automatic
deployment*.

Deploying by hand from a checkout still works, given an Azure login:
`./deploy/azure/deploy.sh`.

## The pipeline

```
scrapers/  ──HTTP──▶  ingestion/  ──▶  MongoDB  ◀──  enrichment/
  rss                  FastAPI                        classify    (keyword rules)
  geonet               dedupes                        geolocate   (gazetteer)
  mastodon             stamps "unverified"            corroborate (proximity grouping)
  fixtures                  │
                            └──────────▶  ui/  (MapLibre + nginx)
```

A folder per stage, a subfolder per scraper source and per enrichment job.
Scrapers only ever speak HTTP to the ingestion API, so a new one can be written
in any language and run anywhere — add `scrapers/sources/<name>/` with a
`collect()` and a compose service with `SOURCES=<name>`. Enrichment jobs are
modules in `enrichment/jobs/` listed in `ENRICHMENT_SCHEDULE`.

**Sources.** `rss` (RNZ, MetService warnings, Wellington.Scoop, NZ Herald),
`geonet` (real earthquake epicentres, no key needed), `mastodon` (public
timelines, unauthenticated), and `fixtures` — synthetic Wellington scenarios
replayed on startup so the demo survives dead venue wifi. Everything the
fixture source emits is labelled as sample data all the way through to the map.

**Enrichment runs on an interval, not under cron.** Same image, same behaviour
on a laptop, under compose, and in an Azure container group — none of which
agree on how to run crond in a container.

## How reliability is handled

The problem statement is as much about showing limitations as finding signals,
so the answer is structural rather than a disclaimer bolted on at the end:

- Every signal is stamped `verification.status: unverified` **at ingest**. There
  is no code path that produces anything else.
- Every inferred value carries the method that produced it and its evidence —
  which keywords matched, which phrase produced the location, what kind of
  place it was. The UI shows the evidence, not just the conclusion.
- `source_count` counts **publishers, not posts**, and is labelled a reason to
  check rather than a truth score. A syndicated story reprinted five times is
  one source.
- Colour on the map encodes corroboration only. Issue type is carried by its
  label, because twelve categorical hues collapse under colour-vision
  deficiency long before the twelfth.
- Locations are suburb centroids and say so. A pin reads as a precise claim,
  so each one carries the phrase it was inferred from.

## What we're building

One working prototype, demoed in four minutes at 16:30.

Each team's module is meant to slot into a shared **common operating picture** —
a live map of emergency signals that the ten prototypes feed together. Aim for
something that can be pointed at a map, a feed or an API, rather than a
closed-off demo.

Two teams work each problem statement independently. That's deliberate: two
honest attempts at the same problem tell WCC more than one.

## How reliability is graded — the Admiralty Code

Wellington's EOC records confidence the way NZ CIMS practice does: the
**Admiralty Code** — source reliability A–F × information credibility 1–6
(A1 = completely reliable source, independently confirmed; F6 = unknown
source, truth cannot be judged). The pipeline stamps an automated grade on
every signal and group: official instruments such as GeoNet rate A,
established media B–C, unknown social accounts F. Credibility improves with
independent corroboration, to 2 at best — **grade 1 ("confirmed by other
sources") is never assigned by software**. Confirmation is a human
intelligence decision, and the interface says so.

Two intake paths complement the live scrapers:

- **Screenshot sharing** — a community member screenshots a post they can
  already see and drops it in; a vision model extracts the content with
  personal identifiers redacted. No platform is scraped.
- **Verified official feeds** — `research/verifiable-sources.md` catalogues
  the machine-readable public feeds we verified today (NZ Police Wellington
  district news, MetService CAP, GeoNet, NZTA delays, Wellington Water live
  faults, Mastodon) as corroboration sources, each with its access method,
  cadence and licence.

## Data

The public GIS datasets Wellington City Council Emergency Management shared are
catalogued, checked and made queryable here:

- **Catalogue + SDK** — https://github.com/claudecommunity-nz/wcc-emergency-gis-data
- **Browse the datasets** — https://claudecommunity-nz.github.io/wcc-emergency-gis-data/

74 datasets: flood, landslide, earthquake, tsunami, coastal inundation and
climate layers, plus emergency hubs, post-quake road reopening order, water
tanks, deprivation by area, and live river-level and rainfall telemetry.
`wcc_gis.py` is a single file with no dependencies — copy it and
`catalogue.json` into your project.

```python
import wcc_gis

wcc_gis.ids("tsunami")                                    # find datasets
wcc_gis.features("tsunami-evacuation-zones", at=(-41.2790, 174.7804))
wcc_gis.geojson("footpaths", bbox=wcc_gis.WELLINGTON)     # straight into MapLibre
wcc_gis.hilltop_data("Hutt River at Taita Gorge", "Flow")[-1]
```

Three traps worth knowing before you lose an hour to them:

- Everything is published in **NZTM2000, not lat/lng**. Request raw and your
  pins land off the coast of Africa. Always ask for `outSR=4326`.
- **A quarter of the layers are rasters** that advertise a query capability,
  then refuse to answer. Ask them for a PNG instead.
- **One query is silently capped** (`footpaths` has 8,130 features; a request
  returns 2,000). Page properly, or check `exceededTransferLimit`.

## Schedule

| Time | What |
|---|---|
| 08:00 | Arrival and mingle |
| 09:00 | Opening address & problem briefing |
| 09:30 | Build begins |
| 12:30 | Lunch + lightning talks |
| 16:00 | Submissions close |
| 16:30 | Demos + judging |
| 17:45 | Awards + next steps |

## Ground rules

- These are **hazard-planning layers, not live emergency information**.
  In an emergency, call 111.
- **The data is not ours.** Each dataset belongs to its publisher — WCC, Greater
  Wellington, GNS Science, NIWA, Wellington Water, MBIE, NZTA, MetService.
  Licence terms vary per dataset; check the dataset's page before publishing
  anything derived from it, and credit the publisher.
- Be considerate with request rates. These are council servers, and at least one
  host throttles under concurrent load.
- **Keep personal details out of this repo.** It is public. No participant
  names, contact details or application material.
- Treat public social content as a *signal to investigate*, never as verified
  fact — surfacing something unverified as confirmed is the failure mode these
  problem statements are most wary of.

## Licence

Code here is MIT unless stated otherwise. The data is not covered by it.
Started !
