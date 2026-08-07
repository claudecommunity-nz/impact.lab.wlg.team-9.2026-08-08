"""Enrichment job registry.

A job is a module exposing `VERSION: str` and `run(db) -> dict`. The version
string is stamped onto every document the job touches, which is how each job
finds its own backlog: anything whose stamp doesn't match the current version
needs (re)processing. Bump VERSION and the whole corpus is reprocessed on the
next tick — useful when you change the rules mid-hackathon.

Adding a job: drop a module in here and add it to ENRICHMENT_SCHEDULE.
"""

import importlib
import pkgutil
from pathlib import Path

_DIR = Path(__file__).parent
_PRIVATE = {"gazetteer"}  # helper modules, not schedulable jobs


def available() -> list[str]:
    return sorted(
        m.name
        for m in pkgutil.iter_modules([str(_DIR)])
        if not m.name.startswith("_") and m.name not in _PRIVATE
    )


def load(name: str):
    module = importlib.import_module(f"jobs.{name}")
    if not hasattr(module, "run"):
        raise AttributeError(f"job {name!r} does not define run(db)")
    return module
