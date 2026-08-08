#!/usr/bin/env python3
"""Record the walkthrough screen capture, paced to the narration.

Each beat holds on screen for exactly as long as its voiceover file runs, so the
video and the audio line up with no editing pass. Generate the MP3s first:

    python demo/voiceover.py            # demo/vo/beat-1.mp3 ... beat-6.mp3
    demo/.venv/bin/python demo/slides.py  # demo/slides/slide-1.png ...

Without the MP3s the estimates in BEATS are used, which is enough to rehearse
with. Without the slides it refuses to record, because a deck that has moved on
is the whole reason the last recording had to be thrown away.

    demo/.venv/bin/python demo/capture.py

Writes a webm into demo/out/. render.sh turns that plus the voiceover into the
final mp4.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
VO = HERE / "vo"

UI = "http://localhost:8080"
API = "http://localhost:8000"
SLIDES = HERE / "slides"

# 1600x900 records comfortably and upscales to 1080p without the 14px UI text
# turning to mush. Bigger viewports just make everything smaller in frame.
VIEWPORT = {"width": 1600, "height": 900}

# The group the whole demo is built around. Matched on visible text rather than
# cluster id, because the id is a content hash and changes as data arrives.
HERO = "Island Bay"

# Held frame at the top, before the first word.
LEAD = 1.5

# Time-stretch applied to the voiceover. Set it here, not in render.sh: the
# beats have to be shot against the sped-up lengths, and render.sh reads the
# factor back out of timing.json so the two can't drift apart.
#     VO_SPEED=1.25 demo/.venv/bin/python demo/capture.py
SPEED = float(os.environ.get("VO_SPEED", "1.0"))


# --- fake cursor -------------------------------------------------------------
# Playwright drives the mouse but renders nothing, so clicks read as things
# happening for no reason. This draws a pointer that follows it.

CURSOR_JS = """
window.addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.id = '__cursor';
  c.style.cssText = `
    position: fixed; top: 0; left: 0; z-index: 2147483647;
    width: 22px; height: 22px; margin: -2px 0 0 -2px;
    pointer-events: none; transition: transform 40ms linear;
    background: no-repeat center/contain url("data:image/svg+xml;utf8,\
<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 22 22'>\
<path d='M4 2 L4 17 L8.2 13.2 L11 19.5 L13.6 18.3 L10.9 12.2 L16.5 12 Z' \
fill='white' stroke='black' stroke-width='1.4' stroke-linejoin='round'/></svg>");
    filter: drop-shadow(0 1px 2px rgba(0,0,0,.45));
  `;
  document.body.appendChild(c);
  document.addEventListener('mousemove', (e) => {
    c.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);

  const ring = document.createElement('div');
  ring.id = '__click';
  ring.style.cssText = `
    position: fixed; top: 0; left: 0; z-index: 2147483646;
    width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%;
    border: 2px solid rgba(57,135,229,.9); pointer-events: none; opacity: 0;
  `;
  document.body.appendChild(ring);
  document.addEventListener('mousedown', (e) => {
    ring.style.transition = 'none';
    ring.style.transform = `translate(${e.clientX}px, ${e.clientY}px) scale(.4)`;
    ring.style.opacity = '1';
    requestAnimationFrame(() => {
      ring.style.transition = 'transform 380ms ease-out, opacity 380ms ease-out';
      ring.style.transform = `translate(${e.clientX}px, ${e.clientY}px) scale(1.5)`;
      ring.style.opacity = '0';
    });
  }, true);
});
"""

SMOOTH_SCROLL_JS = """
([selector, to, ms]) => new Promise((resolve) => {
  const el = selector ? document.querySelector(selector) : document.scrollingElement;
  if (!el) return resolve();
  const from = el.scrollTop;
  const delta = to - from;
  const start = performance.now();
  function step(now) {
    const t = Math.min(1, (now - start) / ms);
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    el.scrollTop = from + delta * eased;
    if (t < 1) requestAnimationFrame(step); else resolve();
  }
  requestAnimationFrame(step);
})
"""


class Pacer:
    """Spends a beat's budget instead of racing through it.

    Actions take however long they take; `until(0.5)` waits until half the
    budget has gone, so the beat always ends on time even when the UI is slow.
    """

    def __init__(self, name, budget):
        self.name = name
        self.budget = budget
        self.t0 = time.monotonic()

    @property
    def elapsed(self):
        return time.monotonic() - self.t0

    def until(self, fraction):
        target = self.budget * fraction
        remaining = target - self.elapsed
        if remaining > 0:
            time.sleep(remaining)

    def done(self):
        self.until(1.0)
        over = self.elapsed - self.budget
        flag = f"  (ran {over:+.1f}s over)" if over > 0.35 else ""
        print(f"  {self.name}: {self.elapsed:.1f}s / {self.budget:.1f}s{flag}")


# --- helpers -----------------------------------------------------------------

def move_to(page, locator, steps=22):
    """Glide the pointer to an element's centre without clicking it."""
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if not box:
        return None
    x = box["x"] + box["width"] / 2
    y = box["y"] + min(box["height"] / 2, 24)
    page.mouse.move(x, y, steps=steps)
    return x, y


def click(page, locator, settle=0.45):
    """Move, pause, then click — a jump-cut click looks like a glitch."""
    point = move_to(page, locator)
    time.sleep(0.28)
    if point:
        page.mouse.down()
        time.sleep(0.06)
        page.mouse.up()
    else:
        locator.click()
    time.sleep(settle)


def scroll(page, selector, to, ms=1100):
    page.evaluate(SMOOTH_SCROLL_JS, [selector, to, ms])
    time.sleep(ms / 1000 + 0.1)


def tab(page, name):
    click(page, page.locator(f'.tab[data-view="{name}"]'), settle=0.7)


def quiesce(page):
    """Stop the page refreshing itself mid-beat.

    The UI re-renders every 30s and would throw away an expanded record or a
    selected cluster part-way through a sentence. Re-applied after every
    navigation, because a fresh document starts its own timers.
    """
    page.evaluate("for (let i = 1; i < 10000; i++) clearInterval(i);")


def slide(page, n, settle=0.6):
    """Show one deck slide full-frame."""
    url = f"file://{SLIDES / 'view.html'}#{n}"
    if page.url.startswith("file://") and page.url.split("#")[0] == url.split("#")[0]:
        page.evaluate("n => { location.hash = String(n); }", n)
    else:
        page.goto(url, wait_until="load")
    page.wait_for_function("document.getElementById('s')?.complete === true", timeout=10000)
    time.sleep(settle)


def app(page, path="", wait=None):
    """Back to the running app, timers stopped.

    domcontentloaded, not networkidle: the app polls on a timer and MapLibre
    streams tiles, so the network is never idle and waiting for it times out.
    The explicit selector below is the real readiness signal.
    """
    page.goto(f"{UI}/{path}", wait_until="domcontentloaded")
    if wait:
        page.wait_for_selector(wait, timeout=25000)
    quiesce(page)
    page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)


# --- the beats ---------------------------------------------------------------
# Six, matching demo/narration.md. Each slide is followed by the thing on
# screen that proves it, which is the whole structure of the pitch.

def beat_intro(page, pace):
    """Slides 1 and 2 — the problem, and what we do about it."""
    slide(page, 1)
    pace.until(0.55)
    slide(page, 2)
    pace.done()


def beat_pipeline(page, pace):
    """Slide 3, then the collectors actually running."""
    slide(page, 3)
    pace.until(0.34)

    app(page, "#pipeline", wait="#pipeline-groups .pipeline-grid")
    pace.until(0.52)

    # Down through the collectors — the per-feed kept counts are the point —
    # then the enrichment jobs below them.
    scroll(page, None, 260, ms=1300)
    pace.until(0.74)
    scroll(page, None, 620, ms=1400)
    pace.until(0.92)
    scroll(page, None, 300, ms=900)
    pace.done()


def beat_raw(page, pace):
    """The whole store, then one record opened to show its evidence."""
    app(page, "#data", wait="#d-rows tr")
    pace.until(0.16)

    scroll(page, None, 120, ms=800)
    pace.until(0.26)

    # Narrow to a classified hazard. An unclassified row expands to a record
    # with no matched terms in it, which is the one thing this beat exists to
    # show.
    move_to(page, page.locator("#d-issue"))
    page.select_option("#d-issue", "flooding")
    page.wait_for_timeout(900)
    pace.until(0.38)

    # ...and prefer one that was actually placed, so geolocate has a phrase and
    # a precision note rather than a pair of nulls.
    rows = page.locator("#d-rows tr")
    row = rows.filter(has=page.locator("td .chip.grade")).filter(
        has_not=page.locator('td:has-text("not placed")')).first
    if row.count() == 0:
        row = rows.first
    click(page, row.locator("button[data-raw]"), settle=0.6)
    pace.until(0.50)

    # The record is capped and scrolls inside its own <pre>. Walk down it so
    # classify, geolocate, admiralty and the verification stamp each land.
    pre = 'tr[style*="table-row"] .raw-json'
    for frac, target in ((0.68, 230), (0.84, 470), (0.96, 720)):
        scroll(page, pre, target, ms=1300)
        pace.until(frac)
    pace.done()


def beat_map(page, pace):
    """Slide 4, then colour-is-corroboration on the map."""
    slide(page, 4)
    pace.until(0.26)

    app(page, "", wait="#map canvas")
    page.wait_for_timeout(1800)
    pace.until(0.38)

    # Strip it back to corroborated groups only — the dark dots are the point.
    move_to(page, page.locator("#f-min"))
    page.select_option("#f-min", "2")
    page.wait_for_timeout(1500)
    pace.until(0.50)

    hero = page.locator("#list .card", has_text=HERO).first
    if hero.count() == 0:
        print(f"  ! no '{HERO}' card under this filter — falling back to the top group")
        hero = page.locator("#list .card").first
    click(page, hero, settle=1.6)
    page.wait_for_selector("#detail.open", timeout=10000)
    pace.until(0.66)

    for frac, target in ((0.80, 280), (0.92, 640), (0.99, 980)):
        scroll(page, "#detail", target, ms=1300)
        pace.until(frac)
    pace.done()


def beat_human(page, pace):
    """Slide 5, then actually verify something.

    The one beat that changes state. It clicks a real confirm button against
    the real API — which is the point, since the claim is that confirmation is
    a human action rather than an inference.
    """
    slide(page, 5)
    pace.until(0.30)

    app(page, "review.html", wait=".card, .empty")
    pace.until(0.44)

    card = page.locator(".card").first
    if card.count() == 0:
        print("  ! review queue is empty — nothing to verify on camera")
        pace.done()
        return

    move_to(page, card)
    scroll(page, None, 140, ms=900)
    pace.until(0.62)

    confirm = card.locator('button[data-action="verify"]').first
    if confirm.count():
        click(page, confirm, settle=1.4)
    pace.until(0.80)

    # And the result: the only route to credibility grade 1 in the system.
    app(page, "verified.html", wait="body")
    page.wait_for_timeout(900)
    scroll(page, None, 160, ms=900)
    pace.done()


def beat_api(page, pace):
    """Slide 6, then the open endpoint it all comes out of."""
    slide(page, 6)
    pace.until(0.52)

    page.goto(f"{API}/clusters.geojson?min_sources=2", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    pace.until(0.78)
    scroll(page, None, 420, ms=1400)
    pace.done()


BEATS = [
    ("intro",    28.0, beat_intro),
    ("pipeline", 40.0, beat_pipeline),
    ("raw",      45.0, beat_raw),
    ("map",      55.0, beat_map),
    ("human",    50.0, beat_human),
    ("api",      24.0, beat_api),
]


def durations():
    """Real voiceover lengths where we have them, estimates where we don't."""
    out, measured = [], 0
    for i, (name, est, _) in enumerate(BEATS, start=1):
        mp3 = VO / f"beat-{i}.mp3"
        if mp3.exists() and shutil.which("ffprobe"):
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(mp3)],
                capture_output=True, text=True,
            )
            if probe.returncode == 0:
                secs = float(json.loads(probe.stdout)["format"]["duration"]) / SPEED
                # A breath at each end, so the cut never lands on a word.
                out.append(round(secs + 0.7, 2))
                measured += 1
                continue
        out.append(est / SPEED)
    print(f"Timing: {measured}/{len(BEATS)} beats from voiceover, "
          f"{len(BEATS) - measured} estimated. Speed x{SPEED:g}. "
          f"Total {sum(out):.1f}s.")
    return out


def main():
    # A deck that has moved on is exactly why the previous recording had to be
    # discarded, so this is a hard stop rather than a warning.
    missing = [n for n in range(1, len(BEATS) + 1) if not (SLIDES / f"slide-{n}.png").exists()]
    if missing or not (SLIDES / "view.html").exists():
        sys.exit(
            f"Missing slides {missing or '(view.html)'} in {SLIDES}.\n"
            "Run: demo/.venv/bin/python demo/slides.py"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.webm"):
        stale.unlink()

    budgets = durations()

    # render.sh pads each voiceover file out to the budget its beat was shot
    # against. Without that the audio creeps ahead by the padding, every beat.
    (OUT / "timing.json").write_text(json.dumps({
        "lead": LEAD,
        "speed": SPEED,
        "beats": [{"name": n, "seconds": s} for (n, _, _), s in zip(BEATS, budgets)],
    }, indent=2) + "\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])

        # Warm the app in a context that is NOT being recorded. Recording
        # starts the moment a recording context exists, so doing the first
        # load inside it put ten-odd seconds of the app booting at the head of
        # every take. The browser keeps its cache and compiled JS between
        # contexts, so the recorded load is quick.
        warm = browser.new_context(viewport=VIEWPORT)
        warm_page = warm.new_page()
        warm_page.goto(UI, wait_until="domcontentloaded")
        warm_page.wait_for_selector(".tab", timeout=25000)
        warm_page.wait_for_timeout(3000)
        warm_page.goto(f"file://{SLIDES / 'view.html'}#1", wait_until="load")
        warm.close()

        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=1,
            record_video_dir=str(OUT),
            record_video_size=VIEWPORT,
            reduced_motion="no-preference",
            color_scheme="light",
        )
        context.add_init_script(CURSOR_JS)
        t_ctx = time.monotonic()
        page = context.new_page()

        # Straight onto slide 1 — the recording opens on the deck, not on a
        # browser loading something.
        slide(page, 1, settle=0.2)
        page.mouse.move(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)
        head = time.monotonic() - t_ctx
        print(f"  (head before first beat: {head:.1f}s)")
        # A held frame before the voiceover starts. render.sh puts the matching
        # silence in front of the audio rather than cutting this out.
        time.sleep(LEAD)

        print("Recording:")
        for (name, _, fn), budget in zip(BEATS, budgets):
            fn(page, Pacer(name, budget))

        time.sleep(0.8)
        video = page.video
        context.close()
        browser.close()

        final = Path(video.path())
        target = OUT / "screen.webm"
        final.rename(target)
        print(f"\nWrote {target}")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"capture failed: {err}", file=sys.stderr)
        sys.exit(1)
