"""SeasonalDriver base + SEAS5 driver: CDS seasonal-original-single-levels.

Implements Jemal's standardization proposal for the planting-date module:
one cached file per (variable, initialization month, year, domain) —
``Seasonal_<VAR>_i<MM>_<year>.nc`` with dims ``(member, time, lat, lon)``
— so the hindcast archive is append-only like the climate layer: adding a
year never refetches the others. Accumulated fields (precipitation, solar
radiation) are de-accumulated to daily values before unit conversion, and
units match the ``AGRO.*`` observation conventions so hindcast and
reference data pair up by variable name for bias correction and DSSAT.

``time`` labels each daily step with the calendar day the value
describes — the START of its 24-hour window, so ``leadtime_hour=24``
([init, init+24h)) is the initialization date itself, exactly how the
observations label a day. Files cached before v0.31 carried the window
END (init + lead, one day late); they are detected by the missing
``time_label`` attribute and migrated in place — a local rewrite, never
a re-download.

The full SEAS5 lead range (24..5160 h, 215 days) is always fetched, so
any later lead subset is a cache hit.

Authentication: a free CDS account and a ``~/.cdsapirc`` file — see
REFERENCE.md. Never hardcode the token in scripts.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# SEAS5 daily steps: 24 h .. 5160 h (215 days, the full lead range).
MAX_LEAD_DAYS = 215

# Daily-series time convention stamped on every cached file: each step is
# labeled with the calendar day it describes (its 24-hour window START).
# A cached file without this attribute predates v0.31 and still carries
# window-END labels — one day late.
TIME_LABEL = "window_start"

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
            self._ensure_window_start(dest)
            return dest

        with cache.locked(dest):
            if dest.exists():
                self._ensure_window_start(dest)
                return dest

            da, fetch_meta = self._fetch_seasonal(variable, init_month, year, domain)
            da = standardize_seasonal(da, variable, self.source_id)
            da.attrs["init_month"] = int(init_month)
            da.attrs["init_year"] = int(year)
            da.attrs["time_label"] = TIME_LABEL

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
                    "time_label": TIME_LABEL,
                    **fetch_meta,
                },
            )
        return dest

    @staticmethod
    def _time_label(dest: Path):
        """The ``time_label`` attribute of a cached file (None = pre-v0.31)."""
        with cache.NC_LOCK:
            with xr.open_dataset(dest) as ds:
                for v in ds.data_vars:
                    if v not in ("spatial_ref", "crs"):
                        return ds[v].attrs.get("time_label")
        return None

    def _ensure_window_start(self, dest: Path) -> None:
        """One-time in-place migration of a pre-v0.31 cached year file.

        Older files labeled each daily step with its window END (init +
        lead), so the first forecast day carried the init+1 date. Shift the
        axis back one day and stamp ``time_label`` — a cheap local rewrite
        under the cache lock, shared by every process; no re-download.
        """
        if self._time_label(dest) == TIME_LABEL:
            return
        with cache.locked(dest):
            if self._time_label(dest) == TIME_LABEL:  # a peer migrated it
                return
            with cache.NC_LOCK:
                with xr.open_dataset(dest) as ds:
                    name = next(
                        v for v in ds.data_vars if v not in ("spatial_ref", "crs")
                    )
                    da = ds[name].load()
            da = da.assign_coords(time=da["time"].values - np.timedelta64(1, "D"))
            da.attrs["time_label"] = TIME_LABEL
            with cache.atomic_write(dest) as tmp:
                with cache.NC_LOCK:
                    da.to_netcdf(tmp, encoding={da.name: seasonal_nc_encoding(da)})
            meta = cache.read_manifest(dest)
            meta["time_label"] = TIME_LABEL
            cache.write_manifest(dest, meta)
            logger.info(
                "Migrated %s to window-start daily labels (one day earlier)",
                dest.name,
            )

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

        from .. import cds

        cds.retrieve(
            access["dataset"], request, raw_path,
            attempts=self.config.cds_retries,
        )

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
        """Lead-time axis → daily calendar axis (one initialization).

        De-accumulates accumulated fields first, then labels each 24-hour
        step with the calendar day it DESCRIBES — the window start,
        ``init + lead - 24h`` — so ``leadtime_hour=24`` ([init, init+24h))
        is the initialization date, matching how the observations label a
        day. This holds for every SEAS5 daily variable: tp/ssrd accumulate
        and mx2t24/mn2t24 aggregate over that same window; the instantaneous
        t2m (valid at the window's end) is assigned to the same day so the
        variables stay paired on one axis.
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
        valid = (
            np.asarray(da[ref_dim].values)
            + np.asarray(da[lead_dim].values)
            - np.timedelta64(24, "h")
        )
        da = da.drop_vars([ref_dim, lead_dim, "valid_time"], errors="ignore")
        da = da.assign_coords({lead_dim: valid}).rename({lead_dim: "time"})
        return da
