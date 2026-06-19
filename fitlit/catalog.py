"""Read the machine-readable endpoint catalogue (data/fitbit_endpoints.yaml).

The catalogue is the single source of truth for *what data exists*.  We use it
to (a) enumerate the Google Health data types and (b) sanity-check that the
FETCHERS map in config.py covers every one of them exactly once.
"""
from __future__ import annotations

import functools

import yaml

from fitlit import config


@functools.lru_cache(maxsize=1)
def load() -> dict:
    """Parse and cache the YAML catalogue."""
    with config.CATALOG_PATH.open() as fh:
        return yaml.safe_load(fh)


def google_health_data_types() -> set[str]:
    """Flatten every Google Health data type across all scope groups."""
    groups = load()["google_health_api"]["data_types"]
    return {dt for members in groups.values() for dt in members}


def validate_coverage() -> None:
    """Fail fast if config.FETCHERS and the catalogue disagree.

    Guarantees every catalogued data type is owned by exactly one fetcher and
    that no fetcher references a data type the catalogue doesn't know about.
    """
    catalogued = google_health_data_types()

    assigned: list[str] = []
    for fetcher in config.FETCHERS.values():
        assigned.extend(fetcher.data_types)

    duplicates = {dt for dt in assigned if assigned.count(dt) > 1}
    if duplicates:
        raise ValueError(f"data types assigned to >1 fetcher: {sorted(duplicates)}")

    assigned_set = set(assigned)
    missing = catalogued - assigned_set
    if missing:
        raise ValueError(f"catalogued data types not assigned to any fetcher: {sorted(missing)}")

    unknown = assigned_set - catalogued
    if unknown:
        raise ValueError(f"fetchers reference unknown data types: {sorted(unknown)}")


# Validate on import so a drift between catalogue and config is impossible to miss.
validate_coverage()
