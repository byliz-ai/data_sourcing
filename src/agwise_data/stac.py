"""Serialize catalog entries to STAC Collections.

The Climate Data Hub catalogs datasets as STAC / OGC Records so they are
discoverable by search engines and AI assistants. This module turns an
agwise-data catalog YAML into a STAC Collection dict — the handoff format
when a dataset (or a reusable intermediate product) graduates to the Hub.
"""

from __future__ import annotations

import json

from .catalog import get_entry
from .harmonize import CANONICAL_VARS, RS_VARS, STATIC_VARS

STAC_VERSION = "1.0.0"


def _iso(date_str):
    """'1981-01-01' → '1981-01-01T00:00:00Z'; None stays None (open interval)."""
    if not date_str:
        return None
    return f"{date_str}T00:00:00Z"


def to_stac_collection(source_id: str) -> dict:
    entry = get_entry(source_id)
    extent = entry.get("extent", {})
    spatial = extent.get("spatial", {}).get("bbox", [-180, -90, 180, 90])
    temporal = extent.get("temporal", {})

    variables = {}
    for name, spec in entry.get("variables", {}).items():
        meta = (
            CANONICAL_VARS.get(name)
            or STATIC_VARS.get(name)
            or RS_VARS.get(name)
            or {}
        )
        variables[name] = {
            "description": meta.get("long_name", name),
            "unit": meta.get("units"),
            "source_name": spec.get("source_name"),
        }

    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": entry["id"],
        "title": entry.get("title"),
        "description": entry.get("description", "").strip(),
        "license": entry.get("license"),
        "version": entry.get("version"),
        "providers": entry.get("providers", []),
        "extent": {
            "spatial": {"bbox": [spatial]},
            "temporal": {
                "interval": [
                    [
                        _iso(temporal.get("start")),
                        _iso(temporal.get("end")),
                    ]
                ]
            },
        },
        "summaries": {
            "variables": variables,
            "spatial_resolution_deg": entry.get("spatial_resolution_deg"),
            "temporal_resolution": entry.get("temporal_resolution"),
            "citation": entry.get("citation", "").strip() or None,
        },
        "links": [],
    }


def to_stac_json(source_id: str, indent: int = 2) -> str:
    return json.dumps(to_stac_collection(source_id), indent=indent)
