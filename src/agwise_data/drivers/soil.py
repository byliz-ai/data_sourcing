"""SoilGrids 2.0 driver: ISRIC WCS GetCoverage, subset in EPSG:4326.

The WCS endpoint returns a GeoTIFF already subset and in 4326, so no
reprojection is needed (the alternative COG/VRT files are in Goode
Homolosine — deliberately avoided). Verified on CGLabs 2026-07-03:
sub-second responses for country-scale boxes at the native ~250 m.

One cached file per property holds **all six standard depths** as a
``depth`` dimension, so any later depth subset is a cache hit. Requests
wider than ``_CHUNK_DEG`` are split into aligned chunks and mosaicked —
the WCS truncates very large coverages instead of failing.
"""

from __future__ import annotations

import logging

import numpy as np
import xarray as xr

from ..catalog import primary_access
from ..harmonize import apply_conversion
from . import register
from .static import StaticDriver

logger = logging.getLogger("agwise_data")

_CHUNK_DEG = 2.0  # max WCS request side; ~880 px at 250 m stays well inside limits
_TIMEOUT = 300  # ISRIC can be slow on big subsets


@register("soilgrids")
class SoilGridsDriver(StaticDriver):
    def _fetch_static(self, variable: str, domain: str):
        from rasterio.io import MemoryFile
        from rasterio.merge import merge

        bbox = self.config.bbox_for(domain)
        spec = self.entry["variables"][variable]
        prop = spec["source_name"]
        depths = list(self.entry.get("depths", []))
        if not depths:
            raise ValueError(
                f"Catalog entry '{self.source_id}' declares no 'depths'"
            )
        access = primary_access(self.entry, "wcs")
        url = access["url"].format(property=prop)
        nodata = spec.get("nodata", 0)

        # Fill a preallocated (depth, lat, lon) cube instead of appending to a
        # list and np.stack-ing it (which briefly holds two full copies).
        cube = None
        grid: dict = {}
        for di, depth in enumerate(depths):
            coverage = f"{prop}_{depth}_{spec.get('statistic', 'mean')}"
            parts = [
                self._get_coverage(url, coverage, chunk)
                for chunk in _chunks(bbox, _CHUNK_DEG)
            ]
            datasets = [MemoryFile(body).open() for body in parts]
            try:
                arr, transform = merge(
                    datasets, bounds=tuple(bbox), nodata=np.nan, dtype="float32"
                )
            finally:
                for ds in datasets:
                    ds.close()
            z = arr[0]
            z[z == nodata] = np.nan
            if "lats" not in grid:
                h, w = z.shape
                grid["lons"] = transform.c + transform.a * (np.arange(w) + 0.5)
                grid["lats"] = transform.f + transform.e * (np.arange(h) + 0.5)
                grid["shape"] = z.shape
                cube = np.empty((len(depths), h, w), dtype=z.dtype)
            if z.shape != grid["shape"]:
                raise RuntimeError(
                    f"SoilGrids depth {depth} of {prop} came back on a "
                    f"different grid ({z.shape} vs {grid['shape']})"
                )
            cube[di] = z

        da = xr.DataArray(
            cube,
            coords={"depth": depths, "lat": grid["lats"], "lon": grid["lons"]},
            dims=("depth", "lat", "lon"),
            name=prop,
        )
        da = apply_conversion(da, spec.get("conversion"))

        return da, {
            "access": "wcs",
            "source_url": url,
            "coverage_stat": spec.get("statistic", "mean"),
            "depths": depths,
        }

    # ------------------------------------------------------------------
    def _get_coverage(self, url: str, coverage: str, bbox) -> bytes:
        import requests

        w, s, e, n = bbox
        params = {
            "SERVICE": "WCS",
            "VERSION": "2.0.1",
            "REQUEST": "GetCoverage",
            "COVERAGEID": coverage,
            "FORMAT": "image/tiff",
            "SUBSET": [f"X({w},{e})", f"Y({s},{n})"],
            "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
            "OUTPUTCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        }
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "tiff" not in ctype.lower():
            raise RuntimeError(
                f"SoilGrids WCS returned {ctype} instead of a GeoTIFF for "
                f"{coverage} {bbox}: {resp.content[:300]!r}"
            )
        return resp.content


def _chunks(bbox, step: float):
    """Split a bbox into <= step-sized chunks (row-major, west→east)."""
    w, s, e, n = bbox
    ys = _splits(s, n, step)
    xs = _splits(w, e, step)
    return [
        (x0, y0, x1, y1)
        for y0, y1 in zip(ys[:-1], ys[1:])
        for x0, x1 in zip(xs[:-1], xs[1:])
    ]


def _splits(a: float, b: float, step: float):
    out = [a]
    while out[-1] + step < b:
        out.append(out[-1] + step)
    out.append(b)
    return out
