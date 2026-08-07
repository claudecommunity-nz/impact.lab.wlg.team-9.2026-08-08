"""Coarse relevance filter, applied before anything reaches the store.

This is deliberately blunt — it exists to stop national news feeds filling the
database with items about Auckland traffic, not to make a judgement about
whether something is real. Anything that survives here is still unverified.

The default rule: keep an item if it mentions somewhere in the Wellington
region AND something hazard-shaped. Sources flagged `local=True` (a Wellington
publication, or a feed already filtered by area) only need the hazard term.

Set FILTER_MODE=off to keep everything — useful when you want to see what's
being thrown away.
"""

import os
import re

REGION_TERMS = [
    "wellington", "wgtn", "te whanganui-a-tara", "welly",
    "hutt", "lower hutt", "upper hutt", "petone", "wainuiomata",
    "porirua", "tawa", "johnsonville", "newlands", "churton park",
    "karori", "kelburn", "thorndon", "te aro", "newtown", "berhampore",
    "island bay", "owhiro bay", "brooklyn", "aro valley", "mount victoria",
    "mt victoria", "mount cook", "hataitai", "kilbirnie", "lyall bay",
    "miramar", "seatoun", "strathmore", "rongotai", "melrose", "houghton bay",
    "ngaio", "khandallah", "wadestown", "wilton", "northland", "crofton downs",
    "oriental bay", "roseneath", "eastbourne", "days bay", "makara",
    "ngauranga", "terrace tunnel", "mount victoria tunnel", "basin reserve",
    "cuba street", "lambton quay", "courtenay place", "kapiti", "paraparaumu",
    "cook strait", "wairarapa", "featherston", "martinborough",
]

HAZARD_TERMS = [
    "flood", "flooding", "flooded", "surface water", "inundation", "storm",
    "slip", "slips", "landslide", "rockfall", "subsidence", "debris",
    "road closed", "closure", "closed", "detour", "cordon", "impassable",
    "power cut", "power out", "outage", "lines down", "no power",
    "burst main", "water main", "boil water", "no water", "wastewater",
    "gale", "wind", "gusts", "trees down", "tree down", "damage",
    "earthquake", "quake", "aftershock", "shaking", "magnitude",
    "tsunami", "evacuat", "warning", "emergency", "civil defence",
    "fire", "smoke", "fenz", "rescue", "injur", "collapse",
    "cancelled", "delays", "disrupt", "metlink", "ferry", "stranded",
    "rain", "hail", "snow", "swell", "high tide", "king tide",
]


def _compile(terms):
    # Word-ish boundaries so "wind" doesn't match "winding".
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(t) for t in terms) + r")", re.I)


_REGION_RE = _compile(REGION_TERMS)
_HAZARD_RE = _compile(HAZARD_TERMS)


def looks_relevant(text: str, local: bool = False) -> tuple[bool, dict]:
    """Return (keep, reasons). `reasons` is recorded so the call is auditable."""
    mode = os.getenv("FILTER_MODE", "both").lower()
    region = sorted({m.lower() for m in _REGION_RE.findall(text or "")})
    hazard = sorted({m.lower() for m in _HAZARD_RE.findall(text or "")})
    reasons = {"region_terms": region, "hazard_terms": hazard, "source_is_local": local}

    if mode == "off":
        return True, reasons
    if mode == "region":
        return bool(region) or local, reasons
    if mode == "hazard":
        return bool(hazard), reasons
    return bool(hazard) and (bool(region) or local), reasons
