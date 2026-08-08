"""A clock shifted into the past, for replaying a historical corpus live.

The synthetic Reddit corpus covers a period in April. Pointing a scraper at it
with real timestamps would return nothing, because nothing in it happened
today. So this offsets the clock: real now maps to a simulated now inside the
corpus, and because the offset is a fixed interval rather than a fixed
timestamp, simulated time advances exactly as fast as real time. Leave it
running and it steps through the April event hour by hour, in step with the
demo.

Two anchors define the offset, both fixed and both explicit:

    REAL_ANCHOR  2026-08-08T00:00:00Z   an instant in real time
    SIM_ANCHOR   2026-04-20T00:00:00Z   the corpus instant it maps to
    offset       = REAL_ANCHOR - SIM_ANCHOR   (110 days)

Anchors rather than a bare "110 days": the day count is a consequence, and
writing it directly is how you end up with an off-by-one nobody can explain.
Fixed rather than "offset from first run" so a container restart does not
silently move the window somewhere else.

Everything derived from this is *simulated* time. It is deliberately not called
`now`, and every signal collected through it records both clocks — a
timestamp from a corpus replay must never be mistaken for something that just
happened.
"""

import os
from datetime import datetime, timedelta, timezone


def _parse(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}={value!r} is not an ISO 8601 timestamp") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


REAL_ANCHOR = _parse(os.getenv("SIM_REAL_ANCHOR", "2026-08-08T00:00:00Z"), "SIM_REAL_ANCHOR")
SIM_ANCHOR = _parse(os.getenv("SIM_ANCHOR", "2026-04-20T00:00:00Z"), "SIM_ANCHOR")

OFFSET = REAL_ANCHOR - SIM_ANCHOR


def enabled() -> bool:
    return OFFSET != timedelta(0)


def sim_now() -> datetime:
    """The corpus instant corresponding to right now."""
    return datetime.now(timezone.utc) - OFFSET


def sim_window(minutes: int) -> tuple[datetime, datetime]:
    """The last `minutes` of simulated time, as (start, end)."""
    end = sim_now()
    return end - timedelta(minutes=minutes), end


def to_real(sim_dt: datetime) -> datetime:
    """Map a corpus timestamp back onto the real clock.

    Used so replayed items sort and window correctly alongside genuinely live
    signals: a post from the simulated last hour should behave like something
    from the real last hour, or it drops off every time-filtered view in the
    interface.
    """
    if sim_dt.tzinfo is None:
        sim_dt = sim_dt.replace(tzinfo=timezone.utc)
    return sim_dt + OFFSET


def describe() -> dict:
    """Shown on the pipeline dashboard, so the shift is visible rather than a trap."""
    now = sim_now()
    days = OFFSET.days + OFFSET.seconds / 86400
    return {
        "simulated_now": now.isoformat(),
        "real_now": datetime.now(timezone.utc).isoformat(),
        "offset_days": round(days, 3),
        "real_anchor": REAL_ANCHOR.isoformat(),
        "sim_anchor": SIM_ANCHOR.isoformat(),
        "summary": f"replaying {now:%d %b %Y %H:%M} UTC — {round(days)} days behind real time",
    }
