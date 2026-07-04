"""StaticDriver base: the :class:`~.base.Driver` contract for layers with
no time axis (DEM, soil properties).

``ensure_static(variable, domain)`` mirrors ``ensure_daily_year`` — fetch
once, harmonize, cache under the shared data root with a provenance
manifest — but produces a single ``Static_<VAR>.nc`` per (source, domain,
variable) instead of one file per year. Derived variables (slope, aspect,
TPI, TRI) are computed from their parent's cached file, so elevation is
fetched once however many derivatives are requested.
"""

from __future__ import annotations

from pathlib import Path

import xarray as xr

from .. import cache
from ..config import Config
from ..harmonize import (
    standardize_static,
    static_canonical_name,
    static_derived_from,
    static_short_name,
)
from .. import terrain

STATIC_CHUNKS = {"depth": 6, "lat": 512, "lon": 512}


def static_nc_encoding(da: xr.DataArray) -> dict:
    chunks = tuple(
        min(size, da.sizes[dim]) for dim, size in STATIC_CHUNKS.items()
        if dim in da.dims
    )
    return {"zlib": True, "complevel": 1, "dtype": "float32", "chunksizes": chunks}


class StaticDriver:
    """Base class. Subclasses implement :meth:`_fetch_static`."""

    def __init__(self, entry: dict, config: Config):
        self.entry = entry
        self.config = config

    @property
    def source_id(self) -> str:
        return self.entry["id"]

    # ------------------------------------------------------------------
    def ensure_static(self, variable: str, domain: str) -> Path:
        """Return the harmonized static file for (variable, domain).

        Downloads (or derives) and harmonizes it on first request;
        afterwards it is a cache hit shared by every user of the data root.
        """
        canonical = static_canonical_name(variable)
        short = static_short_name(canonical)
        dest = self.config.static_path(self.source_id, domain, short)

        if dest.exists():
            return dest

        with cache.locked(dest):
            if dest.exists():
                return dest

            parent = static_derived_from(canonical)
            if parent:
                da, fetch_meta = self._derive(canonical, parent, domain)
            else:
                da, fetch_meta = self._fetch_static(canonical, domain)
            da = standardize_static(da, canonical, self.source_id)

            with cache.atomic_write(dest) as tmp:
                with cache.NC_WRITE_LOCK:
                    da.to_netcdf(tmp, encoding={da.name: static_nc_encoding(da)})
            cache.write_manifest(
                dest,
                {
                    "source_id": self.source_id,
                    "variable": canonical,
                    "domain": domain,
                    "domain_bbox": self.config.bbox_for(domain),
                    "catalog_version": self.entry.get("version"),
                    **fetch_meta,
                },
            )
        return dest

    def open_static(self, variable: str, domain: str) -> xr.DataArray:
        """Open the harmonized static layer (lazy)."""
        path = self.ensure_static(variable, domain)
        da = xr.open_dataset(path)[static_short_name(variable)]
        return da.chunk({d: s for d, s in STATIC_CHUNKS.items() if d in da.dims})

    # ------------------------------------------------------------------
    def _derive(self, variable: str, parent: str, domain: str):
        """Compute a derived variable (slope/aspect/...) from its parent."""
        parent_path = self.ensure_static(parent, domain)
        with xr.open_dataset(parent_path) as ds:
            elev = ds[static_short_name(parent)].load()
        da = terrain.DERIVATIVES[variable](elev)
        da.attrs.clear()
        return da, {
            "access": "derived",
            "derived_from": parent,
            "parent_file": parent_path.name,
        }

    def _fetch_static(self, variable: str, domain: str):
        """Fetch one static layer for the domain's bbox.

        Returns ``(DataArray, fetch_meta)`` — the DataArray already has
        harmonized *units* (catalog ``conversion`` applied) but may still
        have source dim names; :func:`harmonize.standardize_static` handles
        those. ``fetch_meta`` documents where the data came from.
        """
        raise NotImplementedError
