"""Driver base class: fetch-once, harmonize, cache, record provenance."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List

import xarray as xr

from .. import cache
from ..config import Config
from ..harmonize import canonical_name, short_name, standardize

NC_ENCODING = {"zlib": True, "complevel": 4, "dtype": "float32"}


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

            with cache.atomic_write(dest) as tmp:
                da.to_netcdf(tmp, encoding={da.name: NC_ENCODING})
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
            paths, combine="by_coords", parallel=False, chunks={"time": 92}
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
