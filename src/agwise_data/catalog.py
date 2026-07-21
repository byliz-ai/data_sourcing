"""Dataset catalog: one YAML file per source, Hub-compatible metadata.

The catalog is the contract between AgWise and the CGIAR data hubs: each
entry carries the metadata core the Climate Data Hub expects (id, title,
license, providers, extent, version) plus the access recipes this library
needs today. When a dataset graduates to the Hub, the same YAML is the
submission — see :mod:`agwise_data.stac` for the STAC serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Dict, Optional

import yaml

from .harmonize import (
    DEFAULT_SOURCE,
    DEFAULT_STATIC_SOURCE,
    canonical_name,
    static_canonical_name,
)

_CATALOG_DIR = Path(__file__).parent / "catalog"

# Entries registered at runtime (tests, user extensions) take precedence.
_runtime_entries: Dict[str, dict] = {}
_file_entries: Optional[Dict[str, dict]] = None


def _load_file_entries() -> Dict[str, dict]:
    global _file_entries
    if _file_entries is None:
        _file_entries = {}
        for path in sorted(_CATALOG_DIR.glob("*.yaml")):
            with open(path) as fh:
                entry = yaml.safe_load(fh)
            if not isinstance(entry, dict) or "id" not in entry:
                raise ValueError(f"Invalid catalog file (missing 'id'): {path}")
            _file_entries[entry["id"]] = entry
    return _file_entries


def list_sources() -> list:
    entries = {**_load_file_entries(), **_runtime_entries}
    return sorted(entries)


def get_entry(source_id: str) -> dict:
    entries = {**_load_file_entries(), **_runtime_entries}
    try:
        return entries[source_id]
    except KeyError:
        raise KeyError(
            f"Unknown source '{source_id}'. Available: {sorted(entries)}"
        )


def register_entry(entry: dict) -> None:
    """Register a catalog entry at runtime (used by tests and extensions)."""
    if "id" not in entry:
        raise ValueError("Catalog entry needs an 'id'")
    _runtime_entries[entry["id"]] = entry


def _short(name: str) -> str:
    """The bare variable name, without namespace prefix, upper-cased.

    ``"AGRO.PRCP"`` / ``"agro.prcp"`` / ``"PRCP"`` all -> ``"PRCP"`` so a
    user's mapping key matches the canonical regardless of how it is written.
    """
    return str(name).split(".")[-1].strip().upper()


def _source_override(source, canonical: str) -> Optional[str]:
    """The forced source for ``canonical``, honouring a per-variable mapping.

    ``source`` may be a plain source id (forced for every variable) or a
    ``{variable: source_id}`` mapping — the latter lets one call mix sources,
    e.g. rainfall from a local ``chirps_v3`` and temperature from ``agera5``.
    A variable absent from the mapping keeps its catalog default (returns
    None here, so the caller falls back to the default source).
    """
    if isinstance(source, Mapping):
        target = _short(canonical)
        for key, val in source.items():
            if _short(key) == target:
                return val
        return None
    return source


def source_for(variable: str, source=None) -> str:
    """Resolve which source serves ``variable`` (honouring an override).

    ``source`` is a source id forced for the variable, a ``{variable: source}``
    mapping, or None (catalog default).
    """
    canonical = canonical_name(variable)
    source_id = _source_override(source, canonical) or DEFAULT_SOURCE[canonical]
    entry = get_entry(source_id)
    if canonical not in entry.get("variables", {}):
        raise ValueError(
            f"Source '{source_id}' does not provide {canonical}. "
            f"It provides: {sorted(entry.get('variables', {}))}"
        )
    return source_id


def static_source_for(variable: str, source: Optional[str] = None) -> str:
    """Resolve which source serves a *static* variable (honouring an override).

    Derived variables (slope, aspect, ...) are served by the source of the
    variable they are derived from, so a catalog entry only needs to list
    what it actually fetches.
    """
    from .harmonize import static_derived_from

    canonical = static_canonical_name(variable)
    source_id = _source_override(source, canonical) or DEFAULT_STATIC_SOURCE[canonical]
    entry = get_entry(source_id)
    lookup = static_derived_from(canonical) or canonical
    if lookup not in entry.get("variables", {}):
        raise ValueError(
            f"Source '{source_id}' does not provide {canonical}. "
            f"It provides: {sorted(entry.get('variables', {}))}"
        )
    return source_id


def variable_spec(source_id: str, variable: str) -> dict:
    """The per-source recipe (source_name, statistic, conversion) for a variable."""
    entry = get_entry(source_id)
    return entry["variables"][canonical_name(variable)]


def primary_access(entry: dict, access_type: Optional[str] = None) -> dict:
    """The access block the driver should use (role: primary by default)."""
    blocks = entry.get("access", [])
    if access_type:
        matches = [b for b in blocks if b.get("type") == access_type]
        primary = [b for b in matches if b.get("role") == "primary"]
        matches = primary or matches
    else:
        matches = [b for b in blocks if b.get("role") == "primary"] or blocks
    if not matches:
        raise ValueError(f"Catalog entry '{entry.get('id')}' has no usable access block")
    return matches[0]
