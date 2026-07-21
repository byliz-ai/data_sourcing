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

import numpy as np
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
    # Open WITHOUT dask chunks: these legacy files are global (up to ~24 GB/yr)
    # and a dask chunk that spans the whole lat/lon plane would read/decompress
    # the entire globe per time-block just to keep the region window. The
    # netCDF4 backend instead reads an indexed hyperslab of exactly the window
    # (subset_bbox below runs before .load()), which is both far lighter and
    # much faster (verified: seconds vs minutes on the 24 GB CHIRPS v3 files).
    with xr.open_dataset(path) as ds:
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


def _read_tif_window(path, bbox):
    """Read only the ``bbox`` window of a GeoTIFF (global tifs would OOM if
    read whole). Returns ``(array, lats, lons, nodata)`` on the window grid."""
    import rasterio
    from rasterio.windows import from_bounds
    from rasterio.windows import transform as window_transform

    w, s, e, n = bbox
    with rasterio.open(path) as src:
        win = from_bounds(w, s, e, n, src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win).astype("float32")
        t = window_transform(win, src.transform)
        h, wd = arr.shape
        lons = t.c + t.a * (np.arange(wd) + 0.5)
        lats = t.f + t.e * (np.arange(h) + 0.5)
        return arr, lats, lons, src.nodata


def fetch_local_static(
    config, entry: dict, source_id: str, variable: str, domain: str
) -> Optional[Tuple[xr.DataArray, dict]]:
    """Read one static (soil) variable's depth stack from local GeoTIFFs.

    The ``local`` block's ``path`` carries ``{var}`` and ``{depth}`` (e.g.
    ``Soil/soilGrids/profile/{var}_{depth}_mean_30s.tif``); one file per catalog
    depth is windowed to the region and stacked into a ``(depth, lat, lon)``
    cube with the catalog ``conversion`` applied. Returns ``None`` (fall back to
    download) if the feature is off, the block has no per-depth path, or any
    depth file is missing.
    """
    root = getattr(config, "local_root", None)
    if not root:
        return None
    block = _local_block(entry)
    if not block or "{depth}" not in (block.get("path") or ""):
        return None
    depths = list(entry.get("depths", []))
    if not depths:
        return None

    # Static (soil) vars are keyed by their canonical name in the entry; use
    # the entry directly (variable_spec resolves via the climate namespace).
    spec = entry["variables"][variable]
    prop = spec["source_name"]
    nodata = spec.get("nodata")  # None -> rely on the tif's own nodata only
    bbox = config.bbox_for(domain)

    cube, lats, lons = None, None, None
    for di, depth in enumerate(depths):
        path = Path(root) / block["path"].format(var=prop, depth=depth)
        if not path.is_file():
            return None  # need every depth; otherwise download the whole set
        z, la, lo, tif_nodata = _read_tif_window(path, bbox)
        if z.size == 0:
            return None
        if tif_nodata is not None and not np.isnan(tif_nodata):
            z[z == tif_nodata] = np.nan
        if nodata is not None:
            z[z == nodata] = np.nan
        if lats is None:
            lats, lons = la, lo
            cube = np.empty((len(depths), *z.shape), dtype=z.dtype)
        cube[di] = z  # fill preallocated cube (no list + np.stack transient)

    da = xr.DataArray(
        cube,
        coords={"depth": depths, "lat": lats, "lon": lons},
        dims=("depth", "lat", "lon"),
        name=prop,
    )
    # These profile rasters are already in physical units (unlike the raw WCS
    # integers), so skip the catalog conversion when the block says so.
    if not block.get("preconverted"):
        da = apply_conversion(da, spec.get("conversion"))
    logger.info("Local source hit (soil): %s (%s, %d depths)", prop, variable, len(depths))
    return da, {"access": "local", "source_dir": str(Path(root))}


def fetch_local_composite(
    config, entry: dict, source_id: str, variable: str, year: int, domain: str
) -> Optional[Tuple[xr.DataArray, dict]]:
    """Assemble a MODIS composite year from per-composite local GeoTIFFs.

    The ``local`` block's ``composite_path`` carries ``{domain}``, ``{short}``,
    ``{year}`` and a ``{doy}`` wildcard, e.g.
    ``modis/{domain}/{short}_{year}_{doy}.tif``. **The path is domain-tagged on
    purpose**: MODIS composites are region-specific, so keying on the domain
    prevents ever serving one region's tiles for another region's request.
    Files whose names don't carry the domain simply aren't matched (fall back to
    Earth Engine). Returns ``(time, lat, lon)`` scaled to the physical index, or
    ``None``.
    """
    import glob
    import re

    from ..harmonize import rs_short_name

    root = getattr(config, "local_root", None)
    if not root:
        return None
    block = _local_block(entry)
    tmpl = block.get("composite_path") if block else None
    if not tmpl:
        return None

    short = rs_short_name(variable)
    spec = entry["variables"][variable]
    pattern = str(Path(root) / tmpl.format(domain=domain, short=short, year=year, doy="*"))
    files = sorted(glob.glob(pattern))
    if not files:
        return None

    bbox = config.bbox_for(domain)
    fill = spec.get("fill_value")
    lo_hi = spec.get("valid_range")
    times, layers, lats, lons = [], [], None, None
    doy_re = re.compile(r"_(\d{1,3})(?:\.[a-zA-Z]+)?$")
    for f in files:
        m = doy_re.search(Path(f).stem + Path(f).suffix.replace(".tif", ""))
        if not m:
            m = re.search(r"(\d{1,3})$", Path(f).stem)
        if not m:
            continue
        doy = int(m.group(1))
        z, la, lo, tif_nodata = _read_tif_window(f, bbox)
        if z.size == 0:
            continue
        if tif_nodata is not None:
            z[z == tif_nodata] = np.nan
        if fill is not None:
            z[z == fill] = np.nan
        if lo_hi:
            z[(z < lo_hi[0]) | (z > lo_hi[1])] = np.nan
        if lats is None:
            lats, lons = la, lo
        times.append(np.datetime64(f"{year}-01-01") + np.timedelta64(doy - 1, "D"))
        layers.append(z)

    if not layers:
        return None
    order = np.argsort(times)
    # Fill a preallocated cube in time order rather than np.stack-ing a second
    # reordered list (which would hold another full copy of the year).
    cube = np.empty((len(layers), *layers[0].shape), dtype=layers[0].dtype)
    for k, i in enumerate(order):
        cube[k] = layers[i]
    da = xr.DataArray(
        cube,
        coords={"time": [times[i] for i in order], "lat": lats, "lon": lons},
        dims=("time", "lat", "lon"),
        name=spec["source_name"],
    )
    da = apply_conversion(da, spec.get("conversion"))
    logger.info("Local source hit (composite): %s %s (%d composites)", variable, year, len(layers))
    return da, {"access": "local", "n_composites": len(layers)}
