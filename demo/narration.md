# Demo script — 4 minutes, slides + live

Replaces the earlier five-beat version, which was recorded before the review
queue, human verification, public submission and the Reddit corpus existed.

Runs against **localhost**, not the deployed site: the local stack carries the
fixture collector as well, so the corroboration story has something in it on a
quiet afternoon. Bring the stack up first and let it settle for two minutes.

```bash
export REDDIT_API_KEY="$(az keyvault secret show \
  --vault-name "$(grep KEY_VAULT_NAME deploy/azure/.azure-env | cut -d= -f2)" \
  --name reddit-api-key --query value -o tsv)"
docker compose up --build -d
```

**Structure.** Each slide is followed immediately by the thing on screen that
proves it. Six beats, roughly four minutes. Slide numbers refer to
`eoc-signal-demo v0.2.pdf`.

**Voice.** Measured and neutral, not a product-launch read — credibility is the
entire pitch. Speed 0.95–1.0, stability high. One MP3 per beat as before, so
the capture script can hold each beat for its real duration.

---

## Beat 1 — Slides 1 and 2 (~30s)

*Title slide, then What We Do.*

> When something happens in Wellington, the emergency operations centre spends
> the first couple of hours working out what is actually going on. Public posts
> are the fastest source available, and the least trustworthy. So we consume
> them, we grade them, and we hand the operations centre something it can act
> on — where an emergency assistance centre might be needed, and how much to
> trust the reason for putting one there.

## Beat 2 — Slide 3, then the pipeline dashboard (~40s)

*Five-stage slide, then `localhost:8080/#pipeline`.*

> Every item goes through the same five stages. This is that pipeline running.
> Six collectors polling on a timer, covering seven public sources — GeoNet,
> three news and warning feeds, Mastodon, the transport agency, the lines
> company. Each shows when it last returned something and how much of it
> survived: most of what a national news feed carries has nothing to do with
> Wellington, and you can watch it being discarded. A source that has quietly
> died looks exactly like a quiet source from a map, and this is the page that
> tells them apart.

## Beat 3 — Raw data and the evidence trail (~45s)

*`#data` tab, then expand one record.*

> Everything they collect lands here in full — one row per item, nothing
> summarised away, every one stamped unverified the moment it arrives. There is
> no code path in this system that produces anything else.
>
> Open a record and you see what was inferred and how. The hazard type, with
> the keywords that triggered it. The location, with the phrase it was read
> from — and a note that it is a suburb centroid, not a rooftop. Most of what
> you are seeing today is a synthetic corpus replayed on a shifted clock, and
> every one of those carries a sample label right through to the map.

## Beat 4 — Slide 4, then the map (~55s)

*Live Map slide, then the map, then open the Island Bay cluster.*

> On the map colour means one thing only — how many independent publishers
> describe the same event. Not how bad it is. Not how true it is.
>
> Island Bay is darker because three separate sources describe flooding in the
> same place inside the same window. Open it and you get them side by side,
> each with its own evidence and its own grade.
>
> Graded F-two on the Admiralty scale, which Wellington's operations centre
> already uses. Two independent axes: F, because we cannot judge how reliable
> an anonymous account is — and two, because they agree with each other. A
> rumour and a Police report never collapse into the same number. And
> credibility one, confirmed, is never assigned by this software at all.

## Beat 5 — Slide 5, then verify one live (~50s)

*Human Loop slide, then `review.html`, verify an item, then `verified.html`.*

> Confirmation is a person's job, so here is the person's job. Anything the
> pipeline could not place, or that arrived from the public submission form,
> queues here for triage.
>
> An intelligence officer sees a cluster forming, asks operations to send
> somebody to the street, and when that person reports back it is one click.
> That click is the only route to credibility grade one in the entire system,
> and it records who did it and why — "field-verified by council officer",
> not "the algorithm decided".
>
> That is also how we handle Facebook and TikTok, which have no usable public
> API. Rather than pretend otherwise, somebody submits a screenshot and it
> enters the same queue, graded accordingly.

## Beat 6 — Slide 6 (~20s)

*One Shared Picture slide, then the GeoJSON endpoint.*

> None of this is trapped in the page. Signals and clusters both come out as
> GeoJSON from an open endpoint, ready to drop straight into the shared
> operating picture — unverified, and labelled as such, all the way out. And
> when the next source appears, adding it is a scaffolded folder and one
> compose service, not a rewrite.
>
> We do not tell you what is true. We tell you what is worth looking at, and
> exactly how much to trust it.

---

## Generating the voiceover

`demo/voiceover.py` reads the blockquotes above and sends them to ElevenLabs,
so the script people read and the script the API reads cannot drift apart.

Put the key and the voice in `.env` in the repo root — it is gitignored, so
neither ends up in the repository:

```
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

```bash
python demo/voiceover.py --voices    # list the voices on the account
python demo/voiceover.py --dry-run   # word counts and timings, calls nothing
python demo/voiceover.py             # writes demo/vo/beat-1.mp3 ... beat-6.mp3
```

Every run prints which voice it is using and where that value came from —
environment, `.env`, or the built-in default. A key exported in the shell and a
voice id sitting in `.env` is exactly the mix that otherwise burns a run's
credits on the wrong voice.

It skips beats whose text has not changed since the last run, so re-running
after editing one beat costs one beat's credit rather than six. `--force`
overrides, `--beats 4 5` limits it to those beats.

`ELEVENLABS_MODEL_ID` and `ELEVENLABS_STABILITY` resolve the same way. The
default is a measured, neutral read. Worth auditioning two or three voices on
beat 1 alone — `--beats 1` — before spending credits on all six.

Current draft: **573 words, about 3.7 minutes**, which fits the four-minute
slot with a little room.

## Deck accuracy

The v0.2 deck now matches the build — Facebook is correctly described as
arriving via screen capture, Bluesky is gone, the typos are fixed, and slide 3
no longer claims that only corroborated reports reach the map.

One thing left: the **Enrichment** card on slide 3 still has no body text.
Something like *"Infer hazard type and location, and keep the evidence for
both"* would match what beat 3 shows.

Slide 4 now claims a *"Claude Skill to make new ones"*. That is real —
`.claude/skills/new-scraper/` — and beat 6 refers to it. Worth having open in a
tab in case anyone asks.

Deliberately no live counts appear in the narration. They drift between
recording the voiceover and running the demo, and a number contradicting the
screen behind it costs more than it adds.

## Capture script

`demo/capture.py` still drives the old five beats and needs updating before it
can record this: beat 5 is new and has to drive `review.html`, click through a
verification and land on `verified.html`; the slides need interleaving; and
`timing.json` needs six entries rather than five.

## Demo-day fallbacks

- **Nothing on the map.** Widen the time filter to 7 days — the map defaults to
  24 hours and the corpus window is narrow.
- **No multi-source cluster.** `make seed` re-runs the fixture collector, which
  is what produces the Island Bay group beat 4 depends on.
- **Review queue empty.** Submit one through `submit.html` — it appears in the
  queue immediately, which is a fine thing to show live.
- **Reddit collector shows skipped.** `REDDIT_API_KEY` is not exported in that
  shell. Everything else still runs.
