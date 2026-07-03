"""Dataset catalog: one YAML file per source, Hub-compatible metadata.

The catalog is the contract between AgWise and the CGIAR data hubs: each
entry carries the metadata core the Climate Data Hub expects (id, title,
license, providers, extent, version) plus the access recipes this library
needs today. When a dataset graduates to the Hub, the same YAML is the
submission — see :mod:`agwise_data.stac` for the STAC serialization.
"""

from __future__ import annotations

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


def source_for(variable: str, source: Optional[str] = None) -> str:
    """Resolve which source serves ``variable`` (honouring an override)."""
    canonical = canonical_name(variable)
    source_id = source or DEFAULT_SOURCE[canonical]
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
    source_id = source or DEFAULT_STATIC_SOURCE[canonical]
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
