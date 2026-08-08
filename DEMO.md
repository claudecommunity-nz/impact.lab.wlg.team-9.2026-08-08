# Demo — four minutes

Submissions close 16:00. Demo 16:30.

The one line, if you only get one: **we find where impacts may be emerging from
public information, and we are honest about how much to trust each one.**

---

## Before you stand up

> **Two parts of this script need open PRs merged first.** Section 3 (the
> field-verified button) needs **#15**; section 4 (the screenshot drop) needs
> **#4**. If either is still open at 16:00, cut that section and give the time
> to the map — do not demo a branch you haven't run.

> **Docker image builds do not work on the venue network — verified at 15:05.**
> Do not plan on `--build` succeeding. See "If you must rebuild" below.

Run this from the repo root, from whatever is on `main` at the time:

```bash
docker compose up -d --no-build   # --no-build: reuse images already on disk
docker compose ps                 # every service Up; mongo and api healthy
open http://localhost:8080
```

Checklist:

- [ ] **Docker daemon is actually running** (`colima start` if not — it takes ~90s).
- [ ] **Don't run `--build` unless you have to.** Confirm what's actually up with
      `docker compose ps` and check the scraper logs name the feeds you expect to
      demo — a stale image looks identical to a fresh one until you read the logs.
- [ ] `WELECTRICITY_INCLUDE_CLOSED=1` in `.env` if there are no live outages,
      so the outage lane has something to show.
- [ ] One social-post screenshot staged on the desktop, ready to drag into
      `screenshot-inbox/` live.
- [ ] `GEMINI_API_KEY` set in `.env` (gitignored) or the screenshot lane idles.
- [ ] Fixtures are running — if the venue wifi dies, there is still data on the
      map. Anything replayed is labelled `contains sample data` in the UI.
- [ ] Check the legend renders in the room's lighting; dark mode is supported.
- [ ] Have `http://localhost:8000/signals.geojson` open in a second tab.

### If you must rebuild

Every route was tried at ~15:00 and all of them hang indefinitely:

| Attempt | Result |
|---|---|
| `docker compose up -d --build` | `DeadlineExceeded: context deadline exceeded` |
| `DOCKER_BUILDKIT=0 docker compose build` | hangs, 35 min, no progress |
| `docker pull python:3.12-slim` | hangs, 5 min, zero bytes |
| `docker build` from an **already-local** base image | hangs, 5 min |

The registry is genuinely reachable — `curl https://registry-1.docker.io/v2/`
from inside the colima VM returns 401 in 0.66s. It is the image-transfer path
specifically that is broken, and the last row shows the builder is wedged
independently of the network.

The untried fix is `colima stop && colima start` to reset the builder. **Do not
try that within an hour of demoing** — if it fails you have no stack at all,
and right now you have a working one. Rebuild the morning after, on a network
that works.

---

## The script

### 1 · The problem, in her words (~30s)

From zero hour to about +2, the EOC is running blind except for official feeds.
Social is where the signal is — *"if you want to know anything in Wellington,
it's on Vic Deals"* — but Council can't use it institutionally, because nothing
there is verified and presenting it as fact is not survivable.

So the question isn't "can we read social media". It's **"can we read it
honestly enough to act on"**.

### 2 · The live map (~90s)

Real signals, right now: GeoNet, Mastodon, and RSS (RNZ, MetService,
Wellington.Scoop, NZ Herald).

> **Check before you claim the official feeds.** NZ Police, NZTA and Wellington
> Electricity are merged in code (#3), but the containers running at 15:10 were
> built from an older image and are **not** polling them — `docker compose logs
> scraper-rss` lists the feeds it actually has. If Police isn't in that list,
> say "official operator feeds are wired in" rather than naming them as live.
> Claiming a source that isn't on the map is the one mistake that costs you the
> room.

Two things to point at:

- **Colour means corroboration and nothing else.** A cluster goes warmer as
  independent sources describe the same thing in the same place. It does not
  mean "worse" or "true".
- **Click a cluster — every inference carries its evidence.** Location
  confidence says how it was placed. The issue type shows the terms that
  triggered it. Nothing is asserted without showing why.

Then the money shot: **Admiralty grades on everything.** A–F for source
reliability, 1–6 for information credibility — the same grading Wellington's
EOC already uses. `F2` reads as *"can't judge the source, but probably true"*.
Two independent axes, so a rumour from an unknown account and a confirmed
report from Police never collapse into one number.

> **"Credibility 1 — confirmed — is never assigned by this software."**
> Say this line out loud. It's the whole ethical posture in one sentence.

### 3 · The human loop (~60s)

*"Twenty people have posted about this street in Newtown — can somebody go and
verify that?"* That's the SME's actual workflow: intelligence flags, logistics
asks operations, someone goes and takes a photo.

Click **Mark field-verified**. On the next enrichment tick that cluster grades
credibility **1**, and the rationale says *field-verified by council officer* —
not "the algorithm decided". The button is the only route to a 1.

That verification lives in its own collection, so it survives the cluster
rebuild that happens every tick. A person's decision outlasts the machinery.

### 4 · The screenshot bridge (~40s)

Facebook and TikTok have no viable public API, and scraping a logged-in feed
breaches their terms. So we built the honest bridge instead: **a community
member screenshots a post they can already see and shares it.**

Drag the file into `screenshot-inbox/` live. It lands on the map within ~20s as
an F-grade `screenshot` signal, with handles redacted — the vision prompt is
told never to extract usernames, and a redaction pass strips any that leak.

Nothing is scraped. A person chooses to share what they can already see.

### 5 · Close (~20s)

Everything composes: `signals.geojson` and `clusters.geojson` are live
endpoints, not a walled garden. Any other team's module can consume them, which
is the point of a shared common operating picture. Already deployed to Azure
with MongoDB Atlas behind it.

**We don't tell you what's true. We tell you what's worth looking at, and
exactly how much to trust it.**

---

## Questions you should expect

**"How do you stop it spreading misinformation?"**
It never presents a post as fact. Everything carries an Admiralty grade, colour
encodes corroboration only, and the interface says confirmation is a human
decision. The output is a queue of things to check, not a feed of claims.

**"What about privacy?"**
Only public posts. The screenshot lane forbids usernames in the extraction
prompt and redacts any handle that slips through. No profiles, no identities,
no tracking individuals.

**"Is this operational?"**
No — it's a prototype built today on hazard-planning and public data. In an
emergency, 111.

**"What would you do next?"**
Surface the triage tiers in the interface. The backend already stamps every
cluster **people at risk → property → transport → monitor** with the exact
keywords that put it there (merged, #18) — it just isn't on screen yet, so
sort the list by tier and add a neutral chip. Colour stays reserved for
corroboration.

After that, "pieces on the board" — when a cluster needs checking, notify
Council staff already near that location.
