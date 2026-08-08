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

Run this from the repo root, from whatever is on `main` at the time:

```bash
docker compose up -d --build
docker compose ps          # every service Up; mongo and api healthy
open http://localhost:8080
```

Checklist:

- [ ] **Docker daemon is actually running** (`colima start` if not — it takes ~90s).
      If image builds fail with `DeadlineExceeded: context deadline exceeded`,
      prefix with `DOCKER_BUILDKIT=0` — the venue network makes BuildKit's
      registry metadata lookup time out even though the registry is reachable.
- [ ] `WELECTRICITY_INCLUDE_CLOSED=1` in `.env` if there are no live outages,
      so the outage lane has something to show.
- [ ] One social-post screenshot staged on the desktop, ready to drag into
      `screenshot-inbox/` live.
- [ ] `GEMINI_API_KEY` set in `.env` (gitignored) or the screenshot lane idles.
- [ ] Fixtures are running — if the venue wifi dies, there is still data on the
      map. Anything replayed is labelled `contains sample data` in the UI.
- [ ] Check the legend renders in the room's lighting; dark mode is supported.
- [ ] Have `http://localhost:8000/signals.geojson` open in a second tab.

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

Real signals, right now: GeoNet, Mastodon, RNZ and MetService RSS, NZ Police
Wellington, NZTA road events, Wellington Electricity outages.

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
Prioritisation triage (people at risk → property → transport → monitor), and
"pieces on the board" — when a cluster needs checking, notify Council staff
already near that location.
