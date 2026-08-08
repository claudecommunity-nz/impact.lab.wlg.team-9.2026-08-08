# What the WCC emergency-logistics SME told us (8 Aug 2026)

Notes from an interview with a Wellington City Council emergency-management
logistics practitioner during the hackathon. Personal details omitted — this
repo is public. Paraphrased; grouped by what it means for the build.

## The blind window is the product's reason to exist

- There is a recognised **"zero hour"** — the moment the team knows something
  has happened. From roughly **zero to +2 hours the EOC is "running blind"**
  except for what Police/FENZ pass along. By +4 hours staffing and field teams
  catch up. The tool's value is concentrated in that window.
- During the floods, MetService itself had little warning — some events give
  24–48 h lead time (cyclone), others none (earthquake). The tool must be
  useful with zero lead time.

## How they actually treat sources (validates the Admiralty layer)

- Official sources are accepted outright: **"If NZTA comes to us and says this
  road is closed, we're going right there."** Police and NZTA closures are
  treated as done. MetService, Police, FENZ, NZTA were the four named trusted
  sources. → our A-grade list.
- Community social content (the local Facebook trading/community group was the
  example — "if you want to know anything in Wellington, it's on there") is
  read constantly by staff *personally*, but **never used institutionally**:
  "Is it a verified source? No. Do we make decisions based on that? No."
  → our F-grade, and why the screenshot intake matters — council can't
  touch Facebook officially ("things you think should be easy are not when
  it comes to government — things like Facebook").
- Corroboration instinct matches the clustering design exactly: *"It could be
  as simple as 20 people have posted on this street in Newtown… so can
  somebody go and verify that?"* And the GeoNet felt-report mental model:
  one red outlier among peach dots gets ignored; **clusters get attention**.

## The verification loop is human, and ends in the field

- Verification today has **no codified process** ("no A + B = C"). It is:
  intelligence flags → logistics/operations ask "have we got anybody out in
  the field?" → someone drives there, takes a photo, confirms.
- Verifiers are **council officers only** — not trusted community members, at
  this point.
- They rely on partner agencies' own verification (Police verify their way,
  FENZ theirs) and only the EOC's own teams rely on the EOC's verification.
- → the `field-verification` feature: a human marks a cluster verified; only
  that human act can produce credibility 1.

## Operational notes that shaped decisions

- EOC functions: Controller; Intelligence; Logistics; Operations (field
  staff); Welfare; Planning; Tākai Here (iwi/Māori liaison); Safety. Different
  functions will want different views of the same picture.
- A concrete use-case: **siting an Emergency Assistance Centre** — before
  telling the public "go here", they need the situation *around* the EAC and
  on the routes to it. A map of graded signals answers exactly that.
- Deployment: not a laptop-local tool — "we'll put it somewhere that we can
  all see it." → the Azure deploy path matters beyond the demo.
- Keeping imagery/footage historically was raised as valuable ("if we could
  keep that footage… then we could get somebody to go and verify it").
- Emergency-type nuances exist (flood vs quake vs storm collection priorities)
  but the SME's ask was a **verification model that works regardless of
  emergency type**, tuned per event later — which is what a source-reliability
  × corroboration grading gives.

# Team planning discussion (second recording, same day)

Decisions and ideas from the team's own working session:

- **Prioritisation of themes is the missing layer.** The controller gets
  everything at the same level; they need "which should I look at first".
  Tiers, in order: **people at risk** ("a slip with people possibly buried")
  → **property at risk** → **transport issues** → monitor. Staff safety is a
  cross-cutting concern. Prioritisation should be explainable, not a magic
  severity number.
- **Flooding is the deep-dive scenario** — Wellington floods, historical and
  hazard-model data exists (WCC/GWRC layers), and it generalises later.
- **Dedup/threading**: time-of-post ≠ time-of-event; a post spawns a thread
  with sub-threads; once a unique post is classified, don't re-triage it —
  and two officers shouldn't triage the same cluster twice.
- **Enrichment should ask contextual questions** — "is this place prone to
  flooding? has it flooded in the last five years?" — the WCC GIS layers can
  answer that at a point.
- **Verification stays simple and trains the system**: yes/no +
  time/date/location + short note; human decisions become training signal for
  the automated grading over time.
- **"Pieces on the board"** (explicitly flagged to remember): know where your
  own field resources are; when a cluster pops up near one, push them a
  notification to verify. Out of scope today, wanted next.
- Other public sources named for the narrative: talkback radio, CB radio —
  processable in near-real-time in principle, demo-narrative only today.
