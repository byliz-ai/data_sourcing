"""Local source: reuse already-downloaded legacy geodata instead of downloading.

The AgWise ``Global_GeoData/Landing`` tree holds years of geodata the legacy
scripts downloaded, organized ``<Variable>/<Source>/<year>.nc`` (e.g.
``Rainfall/chirps/2020.nc``, ``TemperatureMax/AgEra/2020.nc``). When
``AGWISE_LOCAL_ROOT`` points at that tree, the daily drivers read the matching
file for a (variable, year) and clip it to the requested region — no network
request — then it flows through the normal harmonize + cache path, so the
cached file is byte-identical to one produced from the network source.

The file's declared catalog ``conversion`` is applied here, exactly as the
network drivers do: the legacy AgERA5 files carry the same raw units as CDS
(2 m temperature in K, solar radiation in J m-2 day-1, RH in %, wind in
m s-1), so the existing per-variable conversions map them correctly.

Opt-in and read-only: with ``local_root`` unset (the default) this module does
nothing and drivers download as before.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import xarray as xr

from ..catalog import variable_spec
from ..harmonize import apply_conversion, canonical_name
from ..spatial import subset_bbox

logger = logging.getLogger("agwise_data")

# Variables that are not real data (CRS placeholders / cell bounds).
_NON_DATA = ("crs", "spatial_ref")


def _local_block(entry: dict) -> Optional[dict]:
    for block in entry.get("access", []):
        if block.get("type") == "local":
            return block
    return None


def _relative_path(block: dict, variable: str, year: int) -> Optional[str]:
    """The landing-relative path for (variable, year), or None if not mapped."""
    if "path" in block:                       # single-variable source (CHIRPS)
        return block["path"].format(year=year)
    paths = block.get("paths") or {}          # per-variable source (AgERA5)
    rel = paths.get(canonical_name(variable))
    return rel.format(year=year) if rel else None


def local_file(config, entry: dict, variable: str, year: int) -> Optional[Path]:
    """The local landing file for (variable, year), or None if unavailable.

    Returns None when the feature is off (no ``local_root``), the source has no
    ``local`` access block, the variable is not mapped, or the file is absent —
    every case meaning "fall back to downloading".
    """
    root = getattr(config, "local_root", None)
    if not root:
        return None
    block = _local_block(entry)
    if not block:
        return None
    rel = _relative_path(block, variable, year)
    if not rel:
        return None
    path = Path(root) / rel
    return path if path.is_file() else None


def fetch_local_year(
    config, entry: dict, source_id: str, variable: str, year: int, domain: str
) -> Optional[Tuple[xr.DataArray, dict]]:
    """Read one year of a variable from the local landing, clipped to ``domain``.

    Returns ``(DataArray, fetch_meta)`` with the catalog ``conversion`` applied
    (harmonized units, source dim names — :func:`harmonize.standardize` finishes
    the job), or ``None`` to signal the caller to download instead.
    """
    path = local_file(config, entry, variable, year)
    if path is None:
        return None

    spec = variable_spec(source_id, variable)
    bbox = config.bbox_for(domain)
    with xr.open_dataset(path, chunks={"time": 40}) as ds:
        names = [
            v for v in ds.data_vars
            if v not in _NON_DATA and not str(v).endswith("_bnds")
        ]
        if len(names) != 1:
            raise ValueError(
                f"{path}: expected one data variable, found {list(ds.data_vars)}"
            )
        # Legacy AgERA5 files name the data variable after the year (e.g. 2020);
        # select it positionally, then clip to the region before loading so only
        # the small window is read into memory (the files are global).
        da = subset_bbox(ds[names[0]], bbox).load()

    # Legacy yearly files can store days out of order (some start mid-year);
    # sort so downstream resampling/slicing sees a monotonic time axis.
    da = da.sortby("time")
    da.name = spec["source_name"]
    da = apply_conversion(da, spec.get("conversion"))
    logger.info("Local source hit: %s (%s %s)", path, variable, year)
    return da, {"access": "local", "source_file": str(path)}
