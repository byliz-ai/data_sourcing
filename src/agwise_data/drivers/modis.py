"""ModisDriver base + GEE driver: MODIS vegetation-index 16-day composites.

The MODIS NDVI/EVI products (MOD13Q1 = Terra, MYD13Q1 = Aqua, collection
6.1, ~250 m) are the input of the planting-date phenology workflow: each
satellite contributes 23 composites per year at fixed start days (Terra
DOY 1, 17, ...; Aqua offset by 8 days at DOY 9, 25, ...), and the module
interleaves both into the 46-images-per-year series it smooths.

Like the climate layer, the cache is per (variable, year, domain) and
append-only — ``Composite_<VAR>_<year>.nc`` with dims ``(time, lat, lon)``
where ``time`` holds the composite start dates. Values are scaled to the
physical index range (raw int16 * 1e-4), fill values and out-of-range
pixels are NaN, and pixels whose summary QA is not in the catalog's
``keep`` list are masked (the downstream Savitzky-Golay smoothing fills
those gaps; masking never fabricates values).

Authentication: personal Earth Engine credentials plus a Cloud project
registered for Earth Engine (``AGWISE_GEE_PROJECT`` env var or
``gee_project`` in ``~/.config/agwise_data.yaml``) — see
docs/credentials_setup.md. Never hardcode credentials in scripts.
"""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd
import xarray as xr

from .. import cache
from ..catalog import primary_access
from ..config import Config
from ..harmonize import (
    apply_conversion,
    rs_canonical_name,
    rs_short_name,
    standardize_composite,
)
from . import register

COMPOSITE_CHUNKS = {"time": 23, "lat": 256, "lon": 256}

# Keep each computePixels request well under the API's ~48 MB ceiling:
# 2048 px squared at 2 int16 bands is ~17 MB.
GEE_TILE_PX = 2048


def composite_nc_encoding(da: xr.DataArray) -> dict:
    chunks = tuple(
        min(size, da.sizes[dim])
        for dim, size in COMPOSITE_CHUNKS.items()
        if dim in da.dims
    )
    return {"zlib": True, "complevel": 1, "dtype": "float32", "chunksizes": chunks}


def plan_tiles(width: int, height: int, tile: int = GEE_TILE_PX) -> List[tuple]:
    """Split a raster window into <= tile x tile pixel blocks.

    Returns ``[(x_off, y_off, block_w, block_h), ...]`` covering the full
    window — the request plan for the per-composite GEE pixel pulls.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Empty window: {width} x {height}")
    return [
        (x, y, min(tile, width - x), min(tile, height - y))
        for y in range(0, height, tile)
        for x in range(0, width, tile)
    ]


def mask_invalid(
    values: np.ndarray,
    spec: dict,
    qa: Optional[np.ndarray] = None,
    qa_keep: Optional[list] = None,
) -> np.ndarray:
    """Raw integer band -> float with fill/out-of-range/QA-rejected as NaN.

    Masking only removes values, it never replaces them: the downstream
    smoothing interpolates the gaps.
    """
    out = values.astype("float32")
    bad = np.zeros(out.shape, dtype=bool)
    if spec.get("fill_value") is not None:
        bad |= values == spec["fill_value"]
    if spec.get("valid_range"):
        lo, hi = spec["valid_range"]
        bad |= (values < lo) | (values > hi)
    if qa is not None and qa_keep is not None:
        bad |= ~np.isin(qa, qa_keep)
    out[bad] = np.nan
    return out


class ModisDriver:
    """Base class for composite-stack sources. Subclasses implement
    :meth:`_fetch_year`."""

    def __init__(self, entry: dict, config: Config):
        self.entry = entry
        self.config = config

    @property
    def source_id(self) -> str:
        return self.entry["id"]

    # ------------------------------------------------------------------
    def ensure_composite_year(self, variable: str, year: int, domain: str) -> Path:
        """Return the harmonized composite file for (variable, year, domain).

        Downloads and harmonizes it on first request; afterwards it is a
        cache hit shared by every user of the data root. The current year
        is partial and refreshed when older than
        ``config.refresh_partial_days``.
        """
        variable = rs_canonical_name(variable)
        short = rs_short_name(variable)
        dest = self.config.composite_path(self.source_id, domain, short, year)
        partial = year >= date.today().year

        if dest.exists() and not cache.is_stale_partial(
            dest, self.config.refresh_partial_days
        ):
            return dest

        with cache.locked(dest):
            if dest.exists() and not cache.is_stale_partial(
                dest, self.config.refresh_partial_days
            ):
                return dest

            da, fetch_meta = self._fetch_year(variable, year, domain)
            da = standardize_composite(da, variable, self.source_id)

            # A fully covered past year must have all its composites: caching
            # a silently truncated year would corrupt the 46-images-per-year
            # series the phenology module checks for.
            expected = int(self.entry.get("composites_per_year", 23))
            start = str(
                self.entry.get("extent", {}).get("temporal", {}).get("start") or ""
            )
            covered = bool(start) and year > int(start[:4])
            if not partial and covered and da.sizes["time"] != expected:
                raise RuntimeError(
                    f"{self.source_id} {variable} {year}: fetched "
                    f"{da.sizes['time']} composites, expected {expected} — "
                    "refusing to cache an incomplete past year"
                )

            with cache.atomic_write(dest) as tmp:
                with cache.NC_LOCK:
                    da.to_netcdf(tmp, encoding={da.name: composite_nc_encoding(da)})
            cache.write_manifest(
                dest,
                {
                    "source_id": self.source_id,
                    "variable": variable,
                    "year": year,
                    "n_composites": int(da.sizes["time"]),
                    "domain": domain,
                    "domain_bbox": self.config.bbox_for(domain),
                    "partial": bool(partial),
                    "catalog_version": self.entry.get("version"),
                    **fetch_meta,
                },
            )
        return dest

    def open_years(
        self, variable: str, years: List[int], domain: str
    ) -> xr.DataArray:
        """Open the harmonized composite series for several years (lazy)."""
        paths = [self.ensure_composite_year(variable, y, domain) for y in years]
        ds = xr.open_mfdataset(
            paths, combine="by_coords", parallel=False, chunks=dict(COMPOSITE_CHUNKS)
        )
        return ds[rs_short_name(variable)]

    # ------------------------------------------------------------------
    def _fetch_year(self, variable: str, year: int, domain: str):
        """Fetch one year of composites for the domain.

        Returns ``(DataArray, fetch_meta)`` — dims may still carry source
        names but values must already be scaled to the physical index range
        with invalid pixels as NaN; :func:`harmonize.standardize_composite`
        handles the rest.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
_EE_LOCK = threading.Lock()
_EE_READY = False


def _ee_init(project: Optional[str]):
    """Initialize the Earth Engine client once per process.

    Uses the high-volume endpoint, the one Google asks programmatic
    pixel-pull workloads to use.
    """
    global _EE_READY
    try:
        import ee
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "MODIS downloads need the 'earthengine-api' package: "
            "pip install earthengine-api"
        ) from exc
    with _EE_LOCK:
        if not _EE_READY:
            ee.Initialize(
                project=project or None,
                url="https://earthengine-highvolume.googleapis.com",
            )
            _EE_READY = True
    return ee


@register("modis_gee")
class ModisGeeDriver(ModisDriver):
    def _fetch_year(self, variable: str, year: int, domain: str):
        ee = _ee_init(self.config.gee_project)

        access = primary_access(self.entry, "gee")
        spec = self.entry["variables"][variable]
        collection = access["collection"]
        qa_cfg = access.get("qa") or {}
        qa_band = qa_cfg.get("band")
        qa_keep = qa_cfg.get("keep")
        bands = [spec["source_name"]] + ([qa_band] if qa_band else [])

        w, s, e, n = self.config.bbox_for(domain)
        res = float(access.get("scale_deg", 1.0 / 480.0))
        width = int(math.ceil((e - w) / res))
        height = int(math.ceil((n - s) / res))

        col = (
            ee.ImageCollection(collection)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .sort("system:time_start")
        )
        listing = col.reduceColumns(
            ee.Reducer.toList(2), ["system:index", "system:time_start"]
        ).get("list").getInfo()
        if not listing:
            raise RuntimeError(
                f"{self.source_id}: no {collection} composites for {year}"
            )

        tiles = plan_tiles(width, height)

        def fetch_composite(item):
            index, t0 = item
            img = ee.Image(f"{collection}/{index}").select(bands)
            raw = np.empty((height, width), dtype="int16")
            qa = np.empty((height, width), dtype="int16") if qa_band else None
            for x0, y0, bw, bh in tiles:
                block = ee.data.computePixels(
                    {
                        "expression": img,
                        "fileFormat": "NUMPY_NDARRAY",
                        "grid": {
                            "dimensions": {"width": bw, "height": bh},
                            "affineTransform": {
                                "scaleX": res,
                                "shearX": 0,
                                "translateX": w + x0 * res,
                                "shearY": 0,
                                "scaleY": -res,
                                "translateY": n - y0 * res,
                            },
                            "crsCode": "EPSG:4326",
                        },
                    }
                )
                raw[y0 : y0 + bh, x0 : x0 + bw] = block[spec["source_name"]]
                if qa is not None:
                    qa[y0 : y0 + bh, x0 : x0 + bw] = block[qa_band]
            values = mask_invalid(raw, spec, qa, qa_keep)
            return pd.Timestamp(t0, unit="ms"), values

        workers = max(1, int(self.config.cog_workers))
        if workers > 1 and len(listing) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(fetch_composite, listing))
        else:
            results = [fetch_composite(item) for item in listing]

        times = [t for t, _ in results]
        stack = np.stack([v for _, v in results])
        # pixel-center coordinates, top row first (standardize sorts lat)
        lats = n - res * (np.arange(height) + 0.5)
        lons = w + res * (np.arange(width) + 0.5)
        da = xr.DataArray(
            stack,
            coords={"time": times, "lat": lats, "lon": lons},
            dims=("time", "lat", "lon"),
            name=spec["source_name"],
        )
        da = apply_conversion(da, spec.get("conversion"))

        return da, {
            "gee_collection": collection,
            "scale_deg": res,
            "qa_band": qa_band,
            "qa_keep": qa_keep,
            "fill_value": spec.get("fill_value"),
            "valid_range": spec.get("valid_range"),
        }
