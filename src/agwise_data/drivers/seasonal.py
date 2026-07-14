"""SeasonalDriver base + SEAS5 driver: CDS seasonal-original-single-levels.

Implements Jemal's standardization proposal for the planting-date module:
one cached file per (variable, initialization month, year, domain) —
``Seasonal_<VAR>_i<MM>_<year>.nc`` with dims ``(member, time, lat, lon)``
where ``time`` is the *valid* date (initialization + lead, 24-hour steps)
— so the hindcast archive is append-only like the climate layer: adding a
year never refetches the others. Accumulated fields (precipitation, solar
radiation) are de-accumulated to daily values before unit conversion, and
units match the ``AGRO.*`` observation conventions so hindcast and
reference data pair up by variable name for bias correction and DSSAT.

The full SEAS5 lead range (24..5160 h, 215 days) is always fetched, so
any later lead subset is a cache hit.

Authentication: a free CDS account and a ``~/.cdsapirc`` file — see
HANDOFF.md. Never hardcode the token in scripts.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

from .. import cache
from ..catalog import primary_access, variable_spec
from ..config import Config
from ..harmonize import (
    apply_conversion,
    canonical_name,
    short_name,
    standardize_seasonal,
)
from . import register

# SEAS5 daily steps: 24 h .. 5160 h (215 days, the full lead range).
MAX_LEAD_DAYS = 215

SEASONAL_CHUNKS = {"member": 13, "time": 92, "lat": 128, "lon": 128}


def seasonal_nc_encoding(da: xr.DataArray) -> dict:
    chunks = tuple(
        min(size, da.sizes[dim])
        for dim, size in SEASONAL_CHUNKS.items()
        if dim in da.dims
    )
    return {"zlib": True, "complevel": 1, "dtype": "float32", "chunksizes": chunks}


def deaccumulate_leads(da: xr.DataArray, lead_dim: str) -> xr.DataArray:
    """Accumulated-from-step-0 lead totals → per-step (daily) increments.

    ECMWF accumulated fields (total precipitation, surface solar radiation)
    are running totals from forecast step zero; DSSAT needs the isolated
    24-hour value, i.e. ``step_n - step_(n-1)`` with a zero baseline for
    the first step. Tiny negative increments (float noise in the archive)
    are clipped to zero.
    """
    if lead_dim not in da.dims:
        raise ValueError(f"Cannot de-accumulate: missing dimension '{lead_dim}'")
    da = da.sortby(lead_dim)
    lead_values = da[lead_dim]
    baseline = xr.zeros_like(da.isel({lead_dim: [0]}))
    daily = xr.concat([baseline, da], dim=lead_dim).diff(dim=lead_dim)
    daily = daily.assign_coords({lead_dim: lead_values})
    daily = daily.where(daily >= 0, 0.0)
    daily.attrs.update(da.attrs)
    return daily


class SeasonalDriver:
    """Base class. Subclasses implement :meth:`_fetch_seasonal`."""

    def __init__(self, entry: dict, config: Config):
        self.entry = entry
        self.config = config

    @property
    def source_id(self) -> str:
        return self.entry["id"]

    # ------------------------------------------------------------------
    def ensure_seasonal(
        self, variable: str, init_month: int, year: int, domain: str
    ) -> Path:
        """Return the harmonized seasonal file for (variable, init, year).

        Downloads and harmonizes it on first request; afterwards it is a
        cache hit shared by every user of the data root.
        """
        short = short_name(variable)
        dest = self.config.seasonal_path(
            self.source_id, domain, short, init_month, year
        )
        if dest.exists():
            return dest

        with cache.locked(dest):
            if dest.exists():
                return dest

            da, fetch_meta = self._fetch_seasonal(variable, init_month, year, domain)
            da = standardize_seasonal(da, variable, self.source_id)
            da.attrs["init_month"] = int(init_month)
            da.attrs["init_year"] = int(year)

            with cache.atomic_write(dest) as tmp:
                with cache.NC_LOCK:
                    da.to_netcdf(tmp, encoding={da.name: seasonal_nc_encoding(da)})
            cache.write_manifest(
                dest,
                {
                    "source_id": self.source_id,
                    "variable": canonical_name(variable),
                    "init_month": int(init_month),
                    "year": year,
                    "members": int(da.sizes["member"]),
                    "domain": domain,
                    "domain_bbox": self.config.bbox_for(domain),
                    "catalog_version": self.entry.get("version"),
                    **fetch_meta,
                },
            )
        return dest

    def open_inits(
        self, variable: str, init_month: int, years, domain: str
    ) -> xr.DataArray:
        """Valid-time series for one init month across years (lazy).

        Years are concatenated along ``time`` (each year contributes its
        own ~7-month valid window). Member counts may differ between
        hindcast (25) and real-time (51) years; the extra members are NaN
        for the years that lack them (outer join).
        """
        paths = [
            self.ensure_seasonal(variable, init_month, y, domain) for y in years
        ]
        short = short_name(variable)
        parts = [
            xr.open_dataset(p, chunks=dict(SEASONAL_CHUNKS))[short] for p in paths
        ]
        return xr.concat(
            parts, dim="time", join="outer", combine_attrs="drop_conflicts"
        )

    # ------------------------------------------------------------------
    def _fetch_seasonal(self, variable: str, init_month: int, year: int, domain: str):
        """Fetch one (variable, init month, year) forecast for the domain.

        Returns ``(DataArray, fetch_meta)`` — dims may still carry source
        names (``number``, ``latitude``, ...) but the time axis must
        already be the valid date and units already converted;
        :func:`harmonize.standardize_seasonal` handles the rest.
        """
        raise NotImplementedError


@register("seas5")
class Seas5Driver(SeasonalDriver):
    def _fetch_seasonal(self, variable: str, init_month: int, year: int, domain: str):
        try:
            import cdsapi
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "SEAS5 downloads need the 'cdsapi' package: "
                "pip install 'agwise-data[cds]'"
            ) from exc

        access = primary_access(self.entry, "cds")
        spec = variable_spec(self.source_id, variable)
        w, s, e, n = self.config.bbox_for(domain)
        leads = [str(24 * k) for k in range(1, MAX_LEAD_DAYS + 1)]

        request = {
            "originating_centre": access["originating_centre"],
            "system": access["system"],
            "variable": [spec["source_name"]],
            "year": [str(year)],
            "month": [f"{int(init_month):02d}"],
            "day": ["01"],
            "leadtime_hour": leads,
            "data_format": "netcdf",
            "area": [n, w, s, e],  # CDS order: North, West, South, East
        }

        raw_dir = self.config.raw_dir(self.source_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / (
            f"{self.source_id}_{variable.replace('.', '_')}_i{init_month:02d}"
            f"_{year}_{domain}.nc"
        )

        client = cdsapi.Client()
        client.retrieve(access["dataset"], request, str(raw_path))

        with cache.NC_LOCK:
            with xr.open_dataset(raw_path) as ds:
                da = ds[spec["nc_var"]].load()
        da = self._to_valid_time(da, spec)
        da = apply_conversion(da, spec.get("conversion"))

        if not self.config.keep_raw:
            raw_path.unlink(missing_ok=True)

        return da, {
            "cds_dataset": access["dataset"],
            "cds_request": {
                k: v for k, v in request.items() if k != "leadtime_hour"
            },
            "leadtime_hours": f"{leads[0]}..{leads[-1]}",
        }

    @staticmethod
    def _to_valid_time(da: xr.DataArray, spec: dict) -> xr.DataArray:
        """Lead-time axis → valid-date axis (one initialization).

        De-accumulates accumulated fields first, then converts
        ``forecast_reference_time + forecast_period`` into a daily
        ``time`` coordinate.
        """
        lead_dim = "forecast_period"
        ref_dim = "forecast_reference_time"
        if spec.get("accumulated"):
            da = deaccumulate_leads(da, lead_dim)
        if ref_dim in da.dims:
            if da.sizes[ref_dim] != 1:
                raise ValueError(
                    f"Expected one initialization, found {da.sizes[ref_dim]}"
                )
            da = da.squeeze(ref_dim, drop=False)
        valid = np.asarray(da[ref_dim].values) + np.asarray(da[lead_dim].values)
        da = da.drop_vars([ref_dim, lead_dim, "valid_time"], errors="ignore")
        da = da.assign_coords({lead_dim: valid}).rename({lead_dim: "time"})
        return da
