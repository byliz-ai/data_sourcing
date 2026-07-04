"""CHIRPS driver with two access paths, chosen by requested area.

* **Small regions (a country, a trial-site box)** — daily cloud-optimized
  GeoTIFFs (COGs): only the lat/lon window actually needed is fetched via
  paced parallel HTTP range requests. A Rwanda year moves tens of MB
  instead of the 1.1 GB global file.
* **Large domains (e.g. all of Africa)** — the yearly global NetCDF
  (~1.1 GB compressed), downloaded over several parallel range connections,
  then cropped. One file serves every country in the domain afterwards.

Operational note: data.chc.ucsb.edu rate-limits aggressive clients (it
answers HTTP 403 and may temporarily ban the IP). The COG path is
deliberately paced, treats 403 as "server is blocking us" — not as a
missing day — and falls back to the single-request yearly NetCDF.

Either way the result is the same harmonized ``Daily_PRCP_<year>.nc`` in
the shared cache; raw downloads are deleted unless ``keep_raw`` is set.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from .. import cache
from ..catalog import primary_access, variable_spec
from ..harmonize import apply_conversion
from ..spatial import subset_bbox
from . import register
from .base import Driver

logger = logging.getLogger("agwise_data")

# Final CHIRPS lags real time; don't hammer the server with 404s for days
# that cannot exist yet.
_FINAL_LAG_DAYS = 30
# Politeness: pause per COG read (each read is a burst of a few HTTP
# range requests) and give up on the COG path after this many 403s.
_COG_PACE_SECONDS = 0.25
_COG_BLOCK_LIMIT = 3

_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".cog,.tif,.tiff",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


class CogUnavailable(RuntimeError):
    """The COG access path cannot produce a trustworthy year."""


def _bbox_area_deg2(bbox) -> float:
    w, s, e, n = bbox
    return max(0.0, e - w) * max(0.0, n - s)


def _cog_access(entry):
    for block in entry.get("access", []):
        if block.get("type") == "https-cog":
            return block
    return None


@register("chirps")
class ChirpsDriver(Driver):
    def _fetch_year(self, variable: str, year: int, domain: str):
        bbox = self.config.bbox_for(domain)
        cog = _cog_access(self.entry)
        if cog and _bbox_area_deg2(bbox) <= self.config.region_max_area_deg2:
            try:
                return self._fetch_year_cog(variable, year, bbox, cog)
            except ImportError:
                logger.info(
                    "rasterio not installed — falling back to the yearly "
                    "NetCDF (pip install 'agwise-data[geo]' for windowed reads)"
                )
            except CogUnavailable as exc:
                logger.warning(
                    "CHIRPS COG path unavailable for %s (%s) — falling back "
                    "to the yearly NetCDF",
                    year,
                    exc,
                )
        return self._fetch_year_netcdf(variable, year, domain)

    # ------------------------------------------------------------------
    def _fetch_year_netcdf(self, variable: str, year: int, domain: str):
        access = primary_access(self.entry, "https")
        urls = access["urls"]
        url_key = domain if domain in urls else "global"
        url = urls[url_key].format(year=year)

        raw = self.config.raw_dir(self.source_id) / url.rsplit("/", 1)[-1]
        # The current year's file grows as UCSB publishes new months, so a
        # kept raw copy must not satisfy a partial-year refresh.
        cache.download_file(
            url,
            raw,
            skip_if_exists=year < date.today().year,
            parts=self.config.download_parts,
        )

        spec = variable_spec(self.source_id, variable)
        with cache.NC_LOCK:
            with xr.open_dataset(raw, chunks={"time": 92}) as ds:
                da = ds[spec["source_name"]]
                da = subset_bbox(da, self.config.bbox_for(domain))
                da = apply_conversion(da, spec.get("conversion"))
                da = da.load()

        if not self.config.keep_raw:
            raw.unlink(missing_ok=True)

        return da, {"access": "netcdf", "source_url": url}

    # ------------------------------------------------------------------
    def _fetch_year_cog(self, variable: str, year: int, bbox, access: dict):
        import rasterio
        from rasterio.errors import RasterioIOError
        from rasterio.windows import from_bounds
        from rasterio.windows import transform as window_transform

        pattern = access["url_pattern"]
        w, s, e, n = bbox
        is_past_year = year < date.today().year

        end = date(year, 12, 31)
        cutoff = date.today() - timedelta(days=_FINAL_LAG_DAYS)
        if end > cutoff:
            end = cutoff
        if end < date(year, 1, 1):
            raise CogUnavailable(f"no final CHIRPS published yet for {year}")
        days = pd.date_range(f"{year}-01-01", end, freq="D")

        grid: dict = {}
        blocked = threading.Event()
        blocked_count = [0]
        count_lock = threading.Lock()

        def read_day(d):
            """(status, array) with status in ok|missing|blocked."""
            if blocked.is_set():
                return "blocked", None
            time.sleep(_COG_PACE_SECONDS)  # politeness pacing per read
            url = pattern.format(year=d.year, month=d.month, day=d.day)
            try:
                with rasterio.Env(**_GDAL_ENV):
                    with rasterio.open(f"/vsicurl/{url}") as src:
                        win = (
                            from_bounds(w, s, e, n, src.transform)
                            .round_offsets()
                            .round_lengths()
                        )
                        arr = src.read(1, window=win).astype("float32")
                        nodata = src.nodata if src.nodata is not None else -9999.0
                        arr[arr == nodata] = np.nan
                        arr[arr < 0] = np.nan  # precipitation is non-negative
                        if "lats" not in grid:
                            t = window_transform(win, src.transform)
                            h, wd = arr.shape
                            grid["lons"] = t.c + t.a * (np.arange(wd) + 0.5)
                            grid["lats"] = t.f + t.e * (np.arange(h) + 0.5)
                        return "ok", arr
            except RasterioIOError as exc:
                msg = str(exc)
                if "403" in msg:
                    with count_lock:
                        blocked_count[0] += 1
                        if blocked_count[0] >= _COG_BLOCK_LIMIT:
                            blocked.set()
                    logger.debug("COG blocked (403) %s", url)
                    return "blocked", None
                logger.debug("COG unreadable %s: %s", url, exc)
                return "missing", None

        # Read serially until the first success (establishes the grid), then
        # fan out over the remaining days.
        results: list = [("missing", None)] * len(days)
        i0 = 0
        while i0 < len(days):
            results[i0] = read_day(days[i0])
            if results[i0][0] == "ok":
                break
            if blocked.is_set():
                raise CogUnavailable("server is rate-limiting us (HTTP 403)")
            i0 += 1
        if i0 == len(days):
            raise CogUnavailable(f"no readable CHIRPS COG for {year}")

        rest = list(range(i0 + 1, len(days)))
        with ThreadPoolExecutor(max_workers=self.config.cog_workers) as ex:
            for k, res in zip(rest, ex.map(lambda k: read_day(days[k]), rest)):
                status, arr = res
                if arr is not None and arr.shape != results[i0][1].shape:
                    logger.warning("COG %s has unexpected shape — skipped", days[k])
                    res = ("missing", None)
                results[k] = res

        if blocked.is_set():
            raise CogUnavailable("server is rate-limiting us (HTTP 403)")

        # A past year must be complete BEFORE any trimming — a missing tail
        # there is data loss, not "not published yet".
        n_bad = sum(1 for status, _ in results if status != "ok")
        if is_past_year and n_bad > 0.15 * len(days):
            raise CogUnavailable(
                f"{n_bad}/{len(days)} days unreadable for {year}"
            )
        if not is_past_year:
            # The tail of the current year simply doesn't exist yet.
            last = max(
                (k for k, (status, _) in enumerate(results) if status == "ok"),
                default=None,
            )
            if last is None:
                raise CogUnavailable(f"no readable CHIRPS COG for {year}")
            days, results = days[: last + 1], results[: last + 1]

        missing = [
            str(days[k].date()) for k, (status, _) in enumerate(results)
            if status != "ok"
        ]
        shape = results[i0][1].shape
        stack = np.full((len(days),) + shape, np.nan, dtype="float32")
        for k, (status, arr) in enumerate(results):
            if arr is not None:
                stack[k] = arr

        da = xr.DataArray(
            stack,
            coords={"time": days, "lat": grid["lats"], "lon": grid["lons"]},
            dims=("time", "lat", "lon"),
            name="precip",
        )
        spec = variable_spec(self.source_id, variable)
        da = apply_conversion(da, spec.get("conversion"))

        meta = {
            "access": "cog",
            "source_url_pattern": pattern,
            "n_days": len(days),
            "n_missing": len(missing),
        }
        if missing:
            meta["missing_dates"] = missing[:20]
        return da, meta
