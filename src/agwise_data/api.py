"""Public API of the AgWise data access layer.

Three calls cover the access patterns the AgWise modules use today:

* :func:`get_climate` — harmonized gridded cubes for a country/region
  (daily or monthly), cached as NetCDF/GeoTIFF products. Replaces the
  per-module download-and-stack scripts.
* :func:`extract_points` — time series at point locations.
* :func:`extract_growing_season` — per-trial monthly values between
  planting and harvest dates (plus rainfall totals and rainy-day counts),
  matching the fertilizer ML pipeline's expected columns.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
import xarray as xr

from . import boundaries, catalog, drivers
from .cache import write_manifest
from .config import Config
from .drivers.base import NC_ENCODING
from .harmonize import (
    canonical_name,
    legacy_name,
    rainy_days,
    short_name,
    time_labels,
    to_monthly,
)
from .spatial import clip_geometry, points_bbox, subset_bbox

logger = logging.getLogger("agwise_data")

_LON_CANDIDATES = ["lon", "longitude", "long", "long2", "x"]
_LAT_CANDIDATES = ["lat", "latitude", "lat2", "y"]


# ---------------------------------------------------------------------------
def _as_variables(variables: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(variables, str):
        variables = [v for v in variables.split(",") if v.strip()]
    return [canonical_name(v) for v in variables]


def _as_years(years: Union[int, Sequence[int]]) -> List[int]:
    if isinstance(years, int):
        return [years]
    out = sorted(int(y) for y in years)
    if not out:
        raise ValueError("No years requested")
    return out


def _driver_for(variable: str, source: Optional[str], config: Config):
    source_id = catalog.source_for(variable, source)
    entry = catalog.get_entry(source_id)
    return drivers.get_driver(entry, config), source_id


def _resolve_region(
    config: Config,
    country: Optional[str],
    bbox: Optional[Sequence[float]],
    admin_level: int,
    admin_name: Optional[str],
):
    """Returns (gdf_or_None, bbox, region_tag)."""
    if country:
        gdf = boundaries.load_geometry(config, country, admin_level, admin_name)
        return (
            gdf,
            boundaries.geometry_bbox(gdf),
            boundaries.region_tag(country, admin_level, admin_name),
        )
    if bbox is not None:
        bbox = tuple(float(v) for v in bbox)
        if len(bbox) != 4:
            raise ValueError("bbox must be (west, south, east, north)")
        return None, bbox, boundaries.region_tag(bbox=bbox)
    raise ValueError("Provide either country=... or bbox=(w, s, e, n)")


# ---------------------------------------------------------------------------
def get_climate(
    variables: Union[str, Sequence[str]],
    years: Union[int, Sequence[int]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    freq: str = "daily",
    source: Optional[str] = None,
    domain: Optional[str] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Fetch, harmonize and cache climate cubes for a region.

    Returns ``{canonical_variable: {"nc": Path, "tif": Path|None,
    "data": xr.DataArray}}``. The NetCDF product is always written (it is
    the cache); ``out_format`` controls the additional GeoTIFF export.
    Products are only recomputed with ``overwrite=True``.
    """
    config = config or Config.load()
    variables = _as_variables(variables)
    years = _as_years(years)
    if freq not in ("daily", "monthly"):
        raise ValueError("freq must be 'daily' or 'monthly'")
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats

    gdf, region_bbox, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name
    )
    domain = domain or config.choose_domain(region_bbox)

    out_root = Path(out_dir) if out_dir else config.products_dir(tag)
    results: Dict[str, dict] = {}

    for var in variables:
        driver, source_id = _driver_for(var, source, config)
        short = short_name(var)
        stem = f"{freq.capitalize()}_{short}_{years[0]}_{years[-1]}"
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None

        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())

        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = xr.open_dataarray(nc_path)
        else:
            da = driver.open_years(var, years, domain)
            da = subset_bbox(da, region_bbox, buffer=0.05)
            if gdf is not None:
                da = clip_geometry(da, gdf)
            if freq == "monthly":
                da = to_monthly(da, var)
            da = da.load()

            meta = {
                "source_id": source_id,
                "variable": var,
                "region": tag,
                "years": [years[0], years[-1]],
                "freq": freq,
                "domain": domain,
            }
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                da.to_netcdf(nc_path, encoding={da.name: NC_ENCODING})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                write_geotiff(da, tif_path, labels=time_labels(da, freq))
                write_manifest(tif_path, meta)

        results[var] = {
            "short": short,
            "source": source_id,
            "nc": nc_path if nc_path.exists() else None,
            "tif": tif_path if (tif_path and tif_path.exists()) else None,
            "data": da,
        }
    return results


# ---------------------------------------------------------------------------
def _read_points(points, lon_col: Optional[str], lat_col: Optional[str]):
    if isinstance(points, (str, Path)):
        df = pd.read_csv(points)
    else:
        df = points.copy()

    def find(candidates, given, kind):
        if given:
            if given not in df.columns:
                raise ValueError(f"Column '{given}' not in points data")
            return given
        for c in candidates:
            for col in df.columns:
                if col.lower() == c:
                    return col
        raise ValueError(
            f"Could not find a {kind} column (tried {candidates}); "
            f"pass {kind}_col explicitly. Columns: {list(df.columns)}"
        )

    return df, find(_LON_CANDIDATES, lon_col, "lon"), find(_LAT_CANDIDATES, lat_col, "lat")


def _point_series(
    da: xr.DataArray, lons: np.ndarray, lats: np.ndarray
) -> xr.DataArray:
    """Vectorized nearest-neighbour extraction → dims (time, point)."""
    ilon = xr.DataArray(lons, dims="point")
    ilat = xr.DataArray(lats, dims="point")
    return da.sel(lon=ilon, lat=ilat, method="nearest").transpose("time", "point")


def extract_points(
    points,
    variables: Union[str, Sequence[str]],
    start: str,
    end: str,
    freq: str = "daily",
    source: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Long-format time series at point locations between two dates."""
    config = config or Config.load()
    variables = _as_variables(variables)
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    years = list(range(start_ts.year, end_ts.year + 1))

    lons = df[lon_col].to_numpy(dtype=float)
    lats = df[lat_col].to_numpy(dtype=float)
    bbox = points_bbox(lons, lats)
    domain = config.choose_domain(bbox)

    # A mid-month start must not drop that month's aggregate.
    sel_start = start_ts.to_period("M").to_timestamp() if freq == "monthly" else start_ts

    frames = []
    for var in variables:
        driver, _ = _driver_for(var, source, config)
        da = driver.open_years(var, years, domain)
        da = subset_bbox(da, bbox)
        if freq == "monthly":
            da = to_monthly(da, var)
        da = da.sel(time=slice(sel_start, end_ts))
        series = _point_series(da, lons, lats).load()

        long = series.to_pandas()  # index=time, columns=point position
        long.columns = df.index
        long = long.reset_index().melt(
            id_vars="time", var_name="point", value_name="value"
        )
        long["variable"] = var
        frames.append(long)

    out = pd.concat(frames, ignore_index=True)
    out = out.merge(
        df[[lon_col, lat_col]], left_on="point", right_index=True, how="left"
    )
    return out[["point", lon_col, lat_col, "time", "variable", "value"]]


# ---------------------------------------------------------------------------
def extract_growing_season(
    points,
    variables: Union[str, Sequence[str]],
    planting_col: str,
    harvest_col: str,
    legacy_names: bool = True,
    source: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Per-row growing-season climate for trial data (fertilizer ML format).

    For each row, extracts monthly values from the planting month through
    the harvest month into wide columns ``<VAR>_m1..mN``. For rainfall it
    also computes ``totalRF`` (sum of daily rainfall between the exact
    planting and harvest dates) and ``nrRainyDays`` (days >= 2 mm), exactly
    as the legacy fertilizer pipeline did. Cross-year seasons are handled
    naturally by the continuous time axis.

    With ``legacy_names=True`` columns use the pre-2026 names
    (``Precipitation_m1``, ``TemperatureMax_m1``, ...) so existing ML code
    keeps working; otherwise the AgWise short names (``PRCP_m1``, ...).
    """
    config = config or Config.load()
    variables = _as_variables(variables)
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)

    pl = pd.to_datetime(df[planting_col], errors="coerce")
    hv = pd.to_datetime(df[harvest_col], errors="coerce")
    valid = (
        pl.notna()
        & hv.notna()
        & (pl <= hv)
        & df[lon_col].notna()
        & df[lat_col].notna()
    )
    n_invalid = int((~valid).sum())
    if n_invalid:
        warnings.warn(
            f"{n_invalid}/{len(df)} rows skipped (unparseable dates, "
            "planting after harvest, or missing coordinates); their new "
            "columns are left as NaN."
        )
    if valid.sum() == 0:
        raise ValueError("No valid rows to extract")

    sub = df[valid]
    lons = sub[lon_col].to_numpy(dtype=float)
    lats = sub[lat_col].to_numpy(dtype=float)
    pl_v, hv_v = pl[valid], hv[valid]

    years = list(range(int(pl_v.dt.year.min()), int(hv_v.dt.year.max()) + 1))
    bbox = points_bbox(lons, lats)
    domain = config.choose_domain(bbox)

    pl_month = pl_v.dt.to_period("M").dt.to_timestamp()
    hv_month = hv_v.dt.to_period("M").dt.to_timestamp()
    n_months = (
        (hv_v.dt.year - pl_v.dt.year) * 12 + (hv_v.dt.month - pl_v.dt.month) + 1
    ).to_numpy()
    k_max = int(n_months.max())

    out = df.copy()
    new_cols: Dict[str, np.ndarray] = {}

    for var in variables:
        prefix = legacy_name(var) if legacy_names else short_name(var)
        driver, _ = _driver_for(var, source, config)
        daily = driver.open_years(var, years, domain)
        daily = subset_bbox(daily, bbox)

        monthly_pts = _point_series(to_monthly(daily, var), lons, lats).load()
        m_times = pd.DatetimeIndex(monthly_pts["time"].values)
        m_vals = monthly_pts.values  # (time, point)

        cols = np.full((len(sub), k_max), np.nan, dtype="float32")
        i0 = m_times.searchsorted(pl_month.to_numpy())
        i1 = m_times.searchsorted(hv_month.to_numpy())
        for p in range(len(sub)):
            window = m_vals[i0[p] : i1[p] + 1, p]
            cols[p, : len(window)] = window[:k_max]
        for m in range(k_max):
            new_cols[f"{prefix}_m{m + 1}"] = cols[:, m]

        if short_name(var) == "PRCP":
            daily_pts = _point_series(daily, lons, lats).load()
            d_times = pd.DatetimeIndex(daily_pts["time"].values)
            d_vals = daily_pts.values
            total = np.full(len(sub), np.nan, dtype="float32")
            wet = np.full(len(sub), np.nan, dtype="float32")
            j0 = d_times.searchsorted(pl_v.dt.normalize().to_numpy())
            j1 = d_times.searchsorted(hv_v.dt.normalize().to_numpy(), side="right")
            for p in range(len(sub)):
                window = d_vals[j0[p] : j1[p], p]
                if window.size and not np.all(np.isnan(window)):
                    total[p] = np.nansum(window)
                    wet[p] = np.sum(window >= 2.0)
            new_cols["totalRF"] = total
            new_cols["nrRainyDays"] = wet

    for name, values in new_cols.items():
        col = pd.Series(np.nan, index=df.index, dtype="float64")
        col.loc[sub.index] = values
        out[name] = col
    return out


__all__ = ["get_climate", "extract_points", "extract_growing_season", "rainy_days"]
