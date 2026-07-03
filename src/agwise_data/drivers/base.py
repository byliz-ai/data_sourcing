"""Driver base class: fetch-once, harmonize, cache, record provenance."""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import List

import xarray as xr

from .. import cache
from ..config import Config
from ..harmonize import canonical_name, short_name, standardize

# Storage chunks sized for the two real access patterns (bbox map cubes and
# point time series); complevel 1 writes ~2-3x faster than 4 for a few
# percent more disk, and these files are written once.
STORAGE_CHUNKS = {"time": 92, "lat": 128, "lon": 128}


def nc_encoding(da: xr.DataArray) -> dict:
    chunks = tuple(
        min(size, da.sizes[dim]) for dim, size in STORAGE_CHUNKS.items()
    )
    return {"zlib": True, "complevel": 1, "dtype": "float32", "chunksizes": chunks}


class Driver:
    """Base class. Subclasses implement :meth:`_fetch_year`."""

    def __init__(self, entry: dict, config: Config):
        self.entry = entry
        self.config = config

    @property
    def source_id(self) -> str:
        return self.entry["id"]

    # ------------------------------------------------------------------
    def ensure_daily_year(self, variable: str, year: int, domain: str) -> Path:
        """Return the harmonized daily file for (variable, year, domain).

        Downloads and harmonizes it on first request; afterwards it is a
        cache hit shared by every user of the data root. Partial files for
        the current year are refreshed when older than
        ``config.refresh_partial_days``.
        """
        short = short_name(variable)
        dest = self.config.harmonized_path(self.source_id, domain, short, year)
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
            da = standardize(da, variable, self.source_id)

            # A past year must be complete: caching a silently truncated
            # year would poison every downstream product that reuses it.
            if not partial:
                expected = 366 if calendar.isleap(year) else 365
                if da.sizes["time"] != expected:
                    raise RuntimeError(
                        f"{self.source_id} {variable} {year}: fetched "
                        f"{da.sizes['time']} days, expected {expected} — "
                        "refusing to cache an incomplete past year"
                    )

            with cache.atomic_write(dest) as tmp:
                da.to_netcdf(tmp, encoding={da.name: nc_encoding(da)})
            cache.write_manifest(
                dest,
                {
                    "source_id": self.source_id,
                    "variable": canonical_name(variable),
                    "year": year,
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
        """Open the harmonized daily series for several years (lazy)."""
        paths = [self.ensure_daily_year(variable, y, domain) for y in years]
        ds = xr.open_mfdataset(
            paths, combine="by_coords", parallel=False, chunks=dict(STORAGE_CHUNKS)
        )
        return ds[short_name(variable)]

    # ------------------------------------------------------------------
    def _fetch_year(self, variable: str, year: int, domain: str):
        """Fetch one year of daily data.

        Returns ``(DataArray, fetch_meta)`` where the DataArray already has
        harmonized *units* (catalog ``conversion`` applied) but may still
        have source dim names — :func:`harmonize.standardize` handles those.
        ``fetch_meta`` documents where the data came from (URL/request).
        """
        raise NotImplementedError
