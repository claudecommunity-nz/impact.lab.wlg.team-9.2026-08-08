# Verifiable public data sources for impact corroboration

All endpoints below were fetched live with `curl` on **8 Aug 2026 (NZT)** from this machine.
No API keys, no auth. Sample snippets are truncated; any personal names/handles in feed
content have been redacted before inclusion here (this repo is public).

**How this feeds the Admiralty Code grading:** official agency feeds (GeoNet, MetService,
Police, NEMA, NZTA, GWRC, Wellington Water) are **A/B reliability** — a social post (E/F)
that matches one of these in place + time + issue type gets upgraded. News media (RNZ,
The Post) sit around **B/C** — professional but secondhand.

## Summary

| Source | Type | Endpoint | Verified | Wellington-filterable? | Update freq |
|---|---|---|---|---|---|
| GeoNet quakes | GeoJSON API | `api.geonet.org.nz/quake?MMI=3` | ✅ 200 | Yes — coordinates | Near-real-time (minutes) |
| GeoNet felt reports | GeoJSON API | `api.geonet.org.nz/intensity?type=reported` | ✅ 200 | Yes — point grid | Real-time as people report |
| MetService CAP | RSS index → CAP XML | `alerts.metservice.com/cap/rss` | ✅ 200 | Yes — CAP `<polygon>` per alert | As warnings issued |
| NZ Police (Wellington district) | RSS | `police.govt.nz/rss/district-news/wellington` | ✅ 200 | Yes — dedicated district feed | As releases published (several/week) |
| NZ Police (national) | RSS | `police.govt.nz/rss/news` | ✅ 200 | Keyword filter needed | Multiple/day |
| NEMA Alerthub (EMA) | RSS | `alerthub.civildefence.govt.nz/rss/pwp` | ✅ 200 (0 items today) | Parse alert text/area | Only during Emergency Mobile Alerts |
| Mastodon (mastodon.nz) | JSON API + RSS | `/api/v1/timelines/tag/{tag}`, `/tags/{tag}.rss` | ✅ 200, no auth | Hashtag/keyword only, no geo | Real-time; 300 req/5 min |
| NZTA Journeys road events | GeoJSON | `journeys.nzta.govt.nz/assets/map-data-cache/delays.json` | ✅ 200 | Yes — `regions` contains `16` (Wellington) | Continuously (site's own cache) |
| GWRC Hilltop river/rain telemetry | XML API | `hilltop.gw.govt.nz/data.hts` | ✅ 200 | Yes — per-site | 5-minute telemetry |
| Wellington Water job status | ArcGIS FeatureServer | `services7.arcgis.com/2ECs938g489DMWjt/.../Job_Status_Public_View/FeatureServer/5` | ✅ 200 | Yes — addresses + geometry | Live-ish (their outage map's backend) |
| RNZ national news | RSS | `rnz.co.nz/rss/national.xml` | ✅ 200 | Keyword filter | Continuous, `ttl` 60 |
| The Post / Stuff | Atom | `thepost.co.nz/rss`, `stuff.co.nz/rss` | ✅ 200 | Post is Wellington-centric; section params 500 | Continuous |
| FENZ live incidents | — | website is bot-blocked; no public API found | ❌ | — | — |
| FENZ historical incidents | CSV (data.govt.nz) | CKAN dataset `fire-and-emergency-nz-incident-data` | ✅ 200 | Yes — incident CSVs have location | ~Quarterly (last 2026-07-01) |
| WREMO | — | no RSS/API found on wremo.nz | ❌ | — | — |
| WCC newsroom | — | `wellington.govt.nz/rss` returns 403 | ❌ | — | — |
| Wellington Electricity outages | JSON | `welectricity.co.nz/outages/getalloutages` | ✅ 200 | Yes — per-street lat/lng | Near-real-time (their live map) |
| Transpower customer advice notices | HTML → PDFs | `transpower.co.nz/.../customer-advice-notices-can` | ✅ 200 | National, coarse | As issued |
| Chorus outage map | — | JS-rendered SPA, no API found | ❌ | — | — |
| Spark network status | — | Radware bot-manager blocks curl | ❌ | — | — |
| One NZ network status | — | API gateway found, no open outage endpoint | ❌ | — | — |
| 2degrees network status | — | JS address-checker, no API found | ❌ | — | — |
| Electra (Kāpiti) / Powerco (Wairarapa) | — | outage-map apps found, endpoints not extracted | ❌ | — | — |

---

## GeoNet — quakes + felt reports (Grade A)

**Quake list** (GeoJSON, WGS84; `MMI` param filters minimum intensity 0–8):

```bash
curl -s "https://api.geonet.org.nz/quake?MMI=3" \
  -H "Accept: application/vnd.geo+json;version=2"
```

Sample (fetched today):

```json
{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point",
"coordinates":[172.833297729,-42.775669098]},"properties":{"publicID":"2026p591078",
"time":"2026-08-07T16:46:50.556Z","depth":32.78,"magnitude":2.94,"mmi":3,
"locality":"Within 5 km of Culverden","quality":"best"}}, ...]}
```

**Felt reports** — aggregated "shaking reported" points, filterable to one quake:

```bash
curl -s "https://api.geonet.org.nz/intensity?type=reported" \
  -H "Accept: application/vnd.geo+json;version=2"
# or per event:
curl -s "https://api.geonet.org.nz/intensity?type=reported&publicID=2026p591078" \
  -H "Accept: application/vnd.geo+json;version=2"
```

Returns point features with `mmi` and `count` (people reporting at that intensity).

- **Wellington filter:** by coordinates / bounding box client-side.
- **Update freq:** quake solutions appear within minutes; felt reports stream live.
- **Licence:** GeoNet data is free under **CC BY 3.0 NZ** (attribute GNS Science/EQC/Toka Tū Ake). Send the versioned `Accept` header as they request; polling every 30–60 s is fine.
- **Corroboration use:** the gold standard — a social post saying "big shake in Newtown" is instantly checkable against a located, magnitude-graded event *and* the crowd-sourced felt grid. Grade A1.

## MetService CAP warnings (Grade A)

RSS index of current watches/warnings; each item links to a full CAP 1.2 XML alert:

```bash
curl -s "https://alerts.metservice.com/cap/rss"
# then follow each <link>, e.g.:
curl -s "https://alerts.metservice.com/cap/alert?id=2.49.0.0.554.0.severeweather.nz.20260807210130883.1"
```

Sample from the index today:

```xml
<title>Heavy Rain Watch</title>
<pubDate>Sat, 08 Aug 2026 09:01:30 +1200</pubDate>
<description>Periods of heavy rain, and amounts may approach warning criteria...</description>
```

Each CAP alert carries `<event>`, `<severity>`, `<certainty>`, `<onset>`, `<expires>`,
`<areaDesc>` and a full **`<polygon>`** (lat,lng pairs) — point-in-polygon against
Wellington is trivial.

- **Update freq:** as issued/updated; poll every 1–5 min.
- **Licence:** stated in the feed itself — *"Licensed under Creative Commons BY 4.0"*. Attribute MetService.
- **Corroboration use:** a cluster of "street flooding" posts inside an active Heavy Rain Warning polygon is far more credible. Also provides *anticipatory* context (onset/expires windows). Grade A.

## NZ Police news (Grade A/B)

There is a **dedicated Wellington district RSS feed** (covers Wellington City, Hutt,
Porirua, Kāpiti, Wairarapa):

```bash
curl -s -A "Mozilla/5.0" "https://www.police.govt.nz/rss/district-news/wellington"
# national feed (mixes releases with other site content):
curl -s -A "Mozilla/5.0" "https://www.police.govt.nz/rss/news"
```

Sample from the Wellington feed today (real, current items):

```xml
<title>Man arrested for drug importation, Wellington</title>
<pubDate>Fri, 07 Aug 2026 03:47:01 +0000</pubDate>
<title>Three prolific shoplifters arrested in the Hutt</title>
```

Every police district has one: `https://www.police.govt.nz/rss/district-news/{district}`
(see `https://www.police.govt.nz/news/districts` for the list). Direct URL guesses like
`/rss/news/wellington` trip their WAF (403) — use the exact path above and a browser UA.

- **Update freq:** releases published throughout the day during incidents.
- **Licence:** Crown copyright; police.govt.nz content is generally CC BY 4.0 — attribute NZ Police. Poll gently (every few minutes at most); it's a Drupal site behind a WAF.
- **Corroboration use:** official confirmation of crashes, closures, evacuations, missing persons. During emergencies Police publish frequent updates. Grade A/B (B because media releases lag the event).

## NEMA / Civil Defence Alerthub (Grade A)

The national **Emergency Mobile Alert** feed. `https://alerthub.civildefence.govt.nz/rss`
returns a JSON index confirming `pwp` is the only feed:

```bash
curl -s "https://alerthub.civildefence.govt.nz/rss/pwp"
```

Verified 200 today; currently **zero `<item>`s** because no EMA is active — which is the
expected steady state:

```xml
<title>New Zealand Emergency Mobile Alert feed</title>
<description>A feed of current or recent Emergency Mobile Alerts issued in New Zealand.</description>
<lastBuildDate>Fri, 07 Aug 2026 23:26:00 GMT</lastBuildDate>
```

- **Wellington filter:** parse the alert text/area when items appear (EMAs are geo-targeted; the feed carries the description).
- **Note:** `www.civildefence.govt.nz` itself is behind Cloudflare (403 to curl); the alerthub subdomain is the machine-readable channel.
- **Corroboration use:** if an EMA is active for the region, that is the highest-possible official signal — anything consistent with it upgrades immediately. Grade A1.

## Mastodon — mastodon.nz (Grade E/F until corroborated)

Public timelines and hashtag timelines are readable **without any auth**:

```bash
# local public timeline
curl -s "https://mastodon.nz/api/v1/timelines/public?limit=20&local=true"
# hashtag timeline (includes federated posts, e.g. from cloudisland.nz)
curl -s "https://mastodon.nz/api/v1/timelines/tag/wellington?limit=20"
# same thing as RSS, zero code:
curl -s "https://mastodon.nz/tags/wellington.rss"
```

Verified 200 on all three today; returns standard Mastodon status JSON
(`created_at`, `content` HTML, `url`, `tags`, …). Handles redacted here.

- **Rate limit (verified from response headers):** `x-ratelimit-limit: 300` per 5 minutes per IP — poll each tag every 30–60 s and you're nowhere near it.
- **Caveat:** `/api/v2/search?type=statuses` returns 200 without auth but **empty results** — full-text search needs an authed account on most instances. Rely on **hashtag timelines** (`wellington`, `flood`, `earthquake`, `eqnz` — `#eqnz` is the long-standing NZ quake tag) and the local public timeline.
- **No geo filter** — location must be inferred from text (which is exactly this prototype's job).
- **Licence/ToS:** posts belong to their authors; display with attribution/link, don't republish wholesale.
- **Corroboration use:** this is the E/F-grade *input* stream. Multiple independent accounts + one official source above = upgrade.

## Waka Kotahi NZTA — Journeys road events (Grade A/B)

The Journeys site's own GeoJSON cache of live road events (delays, closures, warnings, roadworks):

```bash
curl -s "https://www.journeys.nzta.govt.nz/assets/map-data-cache/delays.json"
curl -s "https://www.journeys.nzta.govt.nz/assets/map-data-cache/regions.json"  # region id -> name + polygon
```

Verified 200; 116 features nationally today. Properties include `EventType`,
`EventDescription`, `LocationArea`, `Status`, `LastEdited`, `regions` (numeric ids).
**Wellington = region id `16`** (confirmed via `regions.json`). Filtering
`16 in properties.regions` today gives 4 events, e.g.:

```
Area Warning | SH 58 Haywards, between Flightys Road and Moonshine Road | Active
Road Work    | SH 58 Pauatahanui, at the intersection with Joseph Banks Drive | Active
```

- **Update freq:** `LastEdited` timestamps show continuous updates (this is what powers their public map).
- **Caveat/etiquette:** this cache URL is undocumented — the *official* programmatic channel is InfoConnect/TREIS, which needs a (free) registration key we don't have today. Fine for a one-day prototype; label it and poll no more than ~1/min.
- **Licence:** NZTA open data is CC BY 4.0 — attribute Waka Kotahi.
- **Corroboration use:** confirms "road closed / slip / crash blocking SH2" posts, and TREIS state-highway events are official. Grade A/B.

## Greater Wellington Regional Council — Hilltop telemetry (Grade A)

River level/flow and rainfall telemetry, 5-minute resolution:

```bash
# list all sites
curl -s "https://hilltop.gw.govt.nz/data.hts?Service=Hilltop&Request=SiteList"
# last 24h of flow at a river gauge
curl -s "https://hilltop.gw.govt.nz/data.hts?Service=Hilltop&Request=GetData&Site=Hutt%20River%20at%20Taita%20Gorge&Measurement=Flow&TimeInterval=P1D"
```

Verified 200 today; live values with today's timestamps:

```xml
<T>2026-08-08T11:10:00</T><I1>18.694</I1>   <!-- cumecs, Hutt River at Taita Gorge -->
```

- **Wellington filter:** inherently regional; pick the sites you care about.
- **Licence:** GWRC environmental data is CC BY 4.0 — attribute Greater Wellington. The repo's `wcc_gis.py` (`hilltop_data(...)`) already wraps this.
- **Corroboration use:** a "river's about to burst" post checked against actual gauge trend = physical ground truth. Grade A.

## Wellington Water — live job/fault layer (Grade A/B)

Their public "network status" outage map (an ArcGIS Experience app) is backed by an
openly queryable FeatureServer:

```bash
curl -s "https://services7.arcgis.com/2ECs938g489DMWjt/arcgis/rest/services/Job_Status_Public_View/FeatureServer/5/query?where=1%3D1&outFields=*&outSR=4326&f=json&resultRecordCount=50"
```

Verified 200 today; returns work orders/faults with status, description and formatted
address, e.g.:

```json
{"wonum":"814467","status":"INPRG.HD.PAUSE","commoditygroup":"CSR",
 "description":"Blockage 19 Pringle Street, TAITA",
 "wsadd_formattedaddress":"19 Pringle Street, Taita, Lower Hutt, Wellington, 5011"}
```

- **Wellington filter:** inherently — it's Wellington Water's whole service area; geometry + addresses included. Ask for `outSR=4326` (same NZTM trap as the WCC layers).
- **Caveat:** undocumented backend of a public map (note: wellingtonwater.co.nz pages now redirect assets to tiakiwai.co.nz — the layer worked today but could move under the water-services reorganisation). Attribute Wellington Water.
- **Corroboration use:** confirms burst mains, no-water, wastewater overflow and flooding-related fault reports at street level. Grade A/B.

## News media — RNZ and The Post/Stuff (Grade B/C)

```bash
curl -s "https://www.rnz.co.nz/rss/national.xml"          # verified 200, ttl 60
curl -s -A "Mozilla/5.0" "https://www.thepost.co.nz/rss"  # verified 200, Atom
curl -s -A "Mozilla/5.0" "https://www.stuff.co.nz/rss"    # verified 200, Atom
```

- The Post is Wellington's paper, so its feed skews local already; RNZ needs keyword
  filtering (`Wellington`, suburb names). RNZ regional feed (`rss/wellington.xml`)
  returns a soft-404; Stuff/Post `?section=/wellington` returned HTTP 500 — use the
  main feeds + keyword filter.
- **Licence:** headlines + links + attribution only; don't republish article bodies (both are commercial copyright).
- **Corroboration use:** professional-but-secondhand confirmation; a news item matching a social cluster lifts it to roughly B/C.

## FENZ — Fire and Emergency New Zealand (❌ live / ✅ historical)

**Could not verify a live feed.** Verified honestly:

- `fireandemergency.nz` (incl. the incident-reports pages) is behind **Imperva/Incapsula**
  bot protection — curl gets a 200 challenge shell with no content, even with full browser
  headers. Their published incident reports are also delayed ~3 hours and paginated HTML.
- No `api.fireandemergency.nz` DNS record; no incident layer found on their ArcGIS hub
  (only administrative boundaries).
- **What does exist:** the official **"Fire and Emergency New Zealand Incident Data"**
  dataset on data.govt.nz (CKAN API verified 200; last updated **2026-07-01**) — full
  ICAD/SMS incident CSVs with locations, but roughly quarterly, so useless for live
  corroboration, useful for baselining ("how many flooding callouts does Kilbirnie
  normally get?").

```bash
curl -s "https://catalogue.data.govt.nz/api/3/action/package_search?q=title:%22Fire%20and%20Emergency%20New%20Zealand%20Incident%20Data%22&rows=1"
```

- **For the demo:** treat FENZ as a *manually confirmed* source (an intel officer marks a
  signal FENZ-confirmed), or scrape their incident pages via a headless browser later —
  not achievable in curl today. Historical CSVs are CC BY 4.0.

## WREMO and WCC (❌ machine-readable)

- **WREMO** (`wremo.nz`): site is up (200) but `/feed` and `/rss` are 404 — no
  machine-readable channel found. Their live updates go out via social channels;
  their Facebook page has no public unauthenticated API. Treat WREMO updates as a
  manual-confirmation source in the demo.
- **WCC newsroom** (`wellington.govt.nz/rss`): 403 to non-browser clients. The WCC GIS
  layers (via `wcc_gis.py`) remain the machine-readable WCC channel — hazard context,
  not live status.

---

## Utility & network outages

Verified live on **8 Aug 2026 (NZT)**, same rules as above — no keys, no auth,
personal data excluded from samples.

### Wellington Electricity — live outage feed (Grade A)

The JSON backend of their public outages map, dug out of `outages-app.js` on
welectricity.co.nz. **This is the standout find** — Wellington Electricity is the
lines company for Wellington City, Porirua and the Hutt Valley, so this single feed
covers power black-spots for almost the whole WREMO urban area except Kāpiti/Wairarapa:

```bash
curl -s -A "Mozilla/5.0" "https://www.welectricity.co.nz/outages/getalloutages"
```

Verified 200 today. Returns `{"unplannedOutages": [...], "plannedOutages": [...]}` —
30 unplanned (mostly recently-restored) and 76 planned outages at fetch time. Sample
(real, from today's payload):

```json
{"type":"unplanned","id":"2753097","status":"Closed","timeOfFault":"2026-08-01 08:05:00",
 "lastUpdatedCustomersAffected":"144",
 "lastUpdatedComments":"Local area outage - equipment fault. Power has been fully restored",
 "suburbsText":"Johnsonville",
 "location":{"lat":-41.2301396,"lng":174.80541326},
 "areas":[{"street":"Dominion Park Street","suburb":"Johnsonville","city":"Wellington",
           "region":"Wellington","latitude":-41.2305632,"longitude":174.8072561}, ...]}
```

- **Wellington filter:** inherent, and better — **per-street lat/lng** in `areas` plus an
  outage centroid in `location`, customers affected, ETA, status and restoration comments.
  Planned outages add start/end datetimes and reason.
- Companion endpoint `https://www.welectricity.co.nz/outages/getlvoutagedata` also
  returns 200 (JSON); their low-voltage/individual-outage layer is currently disabled
  (`"lvOutagesEnabled":0`), and the map only shows outages affecting 10+ customers.
- **Update freq:** this is what powers their live outage map — near-real-time. The web
  server is slow (large assets took 60s+); the JSON itself returns in a few seconds.
  Poll every 2 min, no faster.
- **Licence/ToS:** undocumented backend of a public map; no stated licence. Attribute
  Wellington Electricity, label as indicative.
- **Corroboration use:** directly confirms "power's out in [suburb]" posts at street
  level, and `customersAffected` gives impact scale. Grade A (operator's own outage
  management system).

### Transpower — customer advice notices (Grade A, coarse)

The Customer Advice Notices (CAN) page is server-rendered and curl-able despite an
antibot script; each notice is a PDF on a static host:

```bash
curl -s -A "Mozilla/5.0" "https://www.transpower.co.nz/system-operator/notices-and-reporting/customer-advice-notices-can"
# yields PDF links like:
# https://static.transpower.co.nz/public/interfaces/can/CAN%20-%20Planned%20Outage%20-%20800000116.pdf
# https://static.transpower.co.nz/public/interfaces/can/CAN%20-%20Low%20Residual%20Situation%20-%20800000107.pdf
```

Verified 200 today. Grid Emergency Notices would appear through the same
notices-and-reporting section during a grid emergency.

- **Wellington filter:** national and grid-level — only relevant when a notice names a
  Wellington-area substation/circuit. PDF parsing required.
- Transpower's `https://www.transpower.co.nz/rss.xml` also verified (200) but it's
  corporate news (last item March 2026) — low value for this prototype.
- **Corroboration use:** context for any *widespread* power outage signal ("is this a
  grid event or a local fault?"). Grade A, but coarse and PDF-shaped — treat as a
  manual-check source in the demo.

### Telcos — Chorus, Spark, One NZ, 2degrees (❌ all, honestly)

None of the four has a curl-reachable outage feed. Named pages for a browser-based
(Playwright/headless) fallback:

- **Chorus** — outage map at `https://www.chorus.co.nz/optimise/internet-outages-map`
  (redirect target of `/outages`). Nuxt SPA; the HTML shell, entry JS and all preloaded
  chunks contain **no data API** — the outage layer loads from a dynamically-imported
  chunk we couldn't reach without executing JS. Chorus's official API portal requires
  registration. ❌ for today.
- **Spark** — `https://www.spark.co.nz/help/network-status` is intercepted by **Radware
  Bot Manager** (redirects curl to `validate.perfdrive.com`), same class of block as
  FENZ. ❌.
- **One NZ** — `https://one.nz/network/status/` (and `/help/network-status/`). Found the
  in-page API gateway constant `https://api.public.one.nz/vf/public/` (gateway is live —
  returns structured JSON 404s for guessed paths) but the status checker is
  address-based and reCAPTCHA-gated; no open outage-list endpoint found. ❌.
- **2degrees** — `https://www.2degrees.nz/network-status` is a Drupal page + microfrontend
  address checker; no API endpoints in its config JS. ❌.

**Corroboration use if browser-scraped later:** operator status pages are Grade A/B for
"cell/broadband down in [suburb]" posts. Until then, telco outage claims can only be
corroborated indirectly (e.g. a power outage in the same area from Wellington
Electricity often explains cell-site loss).

### Regional lines companies + aggregators (leads only)

- **Electra** (Kāpiti) — outage viewer app at
  `https://outagemap-prd-euc9cccch9f9dugn.a02.azurefd.net/` (found via electra.co.nz/outages;
  Azure Static Web App, verified 200). Angular SPA; API endpoint not extractable from the
  first-load chunks without running JS. Good browser-fallback target for Kāpiti coverage. ❌ today.
- **Powerco** (Wairarapa) — ArcGIS Experience Builder app at
  `https://outages.powerco.co.nz/` (verified 200); its config isn't at a guessable public
  path, so the FeatureServer behind it wasn't identified. Same trick as Wellington Water
  would likely work with a browser session. ❌ today.
- **Aggregators:** no NZ outage aggregator worth having. Downdetector NZ exists but is
  crowd-sourced (so D/E-grade anyway — no better than our own social signals), behind
  Cloudflare, and its ToS prohibits scraping. PowerOutage.com is US-only. Skip.

---

## Recommended polling loop for today

| Feed | Poll every | Why |
|---|---|---|
| Mastodon tag timelines (`eqnz`, `wellington`, `flood`, …) | 60 s | the raw E/F signal |
| GeoNet quake + intensity | 60 s | instant A-grade check for shaking posts |
| MetService CAP | 2 min | polygon context for weather posts |
| NZTA delays.json | 2 min | road impact confirmation |
| Hilltop gauges (2–3 rivers) | 5 min | data is 5-minute anyway |
| Police Wellington RSS | 5 min | releases, not telemetry — be gentle |
| Wellington Water jobs | 5 min | undocumented backend — be gentle |
| Wellington Electricity outages | 2 min | undocumented backend, slow server — be gentle |
| NEMA pwp, RNZ, The Post | 5 min | low churn |
