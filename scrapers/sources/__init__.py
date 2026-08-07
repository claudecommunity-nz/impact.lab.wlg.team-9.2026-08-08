"""Collector registry.

Adding a scraper: create `sources/<name>/__init__.py` exposing
`collect() -> list[dict]`, then add a service to docker-compose.yml with
`SOURCES=<name>`. Nothing else needs to change.
"""

import importlib
import logging
import pkgutil
from pathlib import Path

log = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent


def available() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules([str(_PACKAGE_DIR)]) if m.ispkg)


def load(name: str):
    """Import a collector package and return its `collect` callable."""
    module = importlib.import_module(f"sources.{name}")
    if not hasattr(module, "collect"):
        raise AttributeError(f"source {name!r} does not define collect()")
    return module.collect
