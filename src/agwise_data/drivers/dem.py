"""Copernicus DEM GLO-30 driver: per-tile COGs on AWS Open Data.

The DEM is published as one cloud-optimized GeoTIFF per 1x1 degree tile in
EPSG:4326, so a bbox request opens only the intersecting tiles and reads
only the needed window from each (verified end-to-end on CGLabs
2026-07-03: sub-second windowed reads, no full-tile downloads). Ocean
tiles simply do not exist — a missing tile becomes NaN, not an error.

When the catalog entry carries a ``local`` access block (and local reuse is
on), each tile is resolved against the staged copy first — CGLabs keeps the
full-Africa GLO-30 tile set on disk — and only tiles not staged (or
unreadable) fall through to the AWS URL. Same product, same grid, no
network for staged regions.

Slope/aspect/TPI/TRI are *derived* variables: the StaticDriver base
computes them from the cached elevation (see ``terrain.py``), so only
elevation is ever fetched.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import xarray as xr

from .. import progress
from ..catalog import primary_access
from ..harmonize import apply_conversion
from . import register
from .static import StaticDriver

logger = logging.getLogger("agwise_data")

_GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
}

# At 30 m a degree is ~3600 px; this caps one product at ~1.7 GB of float32
# (e.g. ~10x12 degrees). Bigger requests should use a smaller bbox or an
# aggregated DEM source rather than a memory blowup.
MAX_PIXELS = 450_000_000


def _tile_url(pattern: str, lat_sw: int, lon_sw: int) -> str:
    ns = "N" if lat_sw >= 0 else "S"
    ew = "E" if lon_sw >= 0 else "W"
    return pattern.format(ns=ns, lat=abs(lat_sw), ew=ew, lon=abs(lon_sw))


@register("cop_dem30")
class CopDem30Driver(StaticDriver):
    def _local_tiles(self):
        """(dir, filename_pattern) of the staged tile set, or None.

        Local reuse must be on (``local_root`` set — the same switch every
        other local source uses) and the staged directory must exist; the
        tile set itself lives outside the Landing tree, at the absolute
        ``tile_root`` the catalog block names.
        """
        if not getattr(self.config, "local_root", None):
            return None
        block = next(
            (b for b in self.entry.get("access", []) if b.get("type") == "local"),
            None,
        )
        if not block:
            return None
        root = Path(block["tile_root"])
        return (root, block["tile_pattern"]) if root.is_dir() else None

    def _fetch_static(self, variable: str, domain: str):
        import rasterio
        from rasterio.errors import RasterioIOError
        from rasterio.merge import merge

        bbox = self.config.bbox_for(domain)
        w, s, e, n = bbox
        access = primary_access(self.entry, "https-cog")
        pattern = access["url_pattern"]
        local = self._local_tiles()

        res = 1.0 / 3600.0  # GLO-30 native spacing below 50 deg latitude
        est = ((e - w) / res) * ((n - s) / res)
        if est > MAX_PIXELS:
            raise ValueError(
                f"DEM request of ~{est/1e6:.0f} Mpx exceeds the "
                f"{MAX_PIXELS/1e6:.0f} Mpx limit — use a smaller bbox "
                "(the cache is region-scoped, so per-country or per-site "
                "requests are the intended pattern)"
            )

        coords = [
            (lat, lon)
            for lat in range(math.floor(s), math.ceil(n))
            for lon in range(math.floor(w), math.ceil(e))
        ]

        datasets = []
        missing = []
        n_local = 0
        try:
            with rasterio.Env(**_GDAL_ENV):
                for lat, lon in progress.track(
                    coords, desc=f"DEM tiles ({len(coords)})"
                ):
                    src = None
                    if local:
                        path = local[0] / _tile_url(local[1], lat, lon)
                        if path.is_file():
                            try:
                                src = rasterio.open(path)
                                n_local += 1
                            except RasterioIOError:
                                # corrupt staged tile: fall through to AWS
                                logger.warning(
                                    "Staged DEM tile %s is unreadable — "
                                    "fetching it from AWS instead", path.name,
                                )
                                src = None
                    if src is None:
                        url = _tile_url(pattern, lat, lon)
                        try:
                            src = rasterio.open("/vsicurl/" + url)
                        except RasterioIOError:
                            missing.append(url.rsplit("/", 1)[-1])  # ocean tile
                            continue
                    datasets.append(src)
                if not datasets:
                    raise RuntimeError(
                        f"No Copernicus DEM tiles exist for bbox {bbox}"
                    )
                arr, transform = merge(
                    datasets, bounds=(w, s, e, n), nodata=np.nan, dtype="float32"
                )
        finally:
            for ds in datasets:
                ds.close()

        z = arr[0]
        h, wd = z.shape
        lons = transform.c + transform.a * (np.arange(wd) + 0.5)
        lats = transform.f + transform.e * (np.arange(h) + 0.5)
        da = xr.DataArray(
            z, coords={"lat": lats, "lon": lons}, dims=("lat", "lon"), name="dem"
        )
        spec = self.entry["variables"][variable]
        da = apply_conversion(da, spec.get("conversion"))

        meta = {
            "access": "local+cog" if n_local else "cog",
            "source_url_pattern": pattern,
            "n_tiles": len(coords),
            "n_local_tiles": n_local,
            "n_missing_tiles": len(missing),
        }
        if missing:
            meta["missing_tiles"] = missing[:20]
        return da, meta
