"""Public API of the AgWise data access layer.

Three calls cover the access patterns the AgWise modules use today:

* :func:`get_climate` — harmonized gridded cubes for a country/region
  (daily or monthly), cached as NetCDF/GeoTIFF products. Replaces the
  per-module download-and-stack scripts.
* :func:`extract_points` — time series at point locations.
* :func:`extract_growing_season` — per-trial monthly values between
  planting and harvest dates (plus rainfall totals and rainy-day counts),
  matching the fertilizer ML pipeline's expected columns.

Performance notes: all (variable, year) fetches run in a thread pool
(``config.max_workers``) so downloads and CDS queue waits overlap, and
small requests get a *region-scoped* cache (see ``config.fetch_scope``)
so a one-country run fetches only that country's window instead of a
whole continental domain.
"""

from __future__ import annotations

import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
import xarray as xr

from . import boundaries, catalog, drivers
from .cache import write_manifest
from .config import Config, region_domain_name, round_region_bbox
from .drivers.base import nc_encoding
from .harmonize import (
    canonical_name,
    legacy_name,
    rainy_days,
    short_name,
    static_canonical_name,
    static_has_depth,
    static_short_name,
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
def _bbox_area(bbox) -> float:
    w, s, e, n = bbox
    return max(0.0, e - w) * max(0.0, n - s)


def _harmonized_complete(
    config: Config, source_id: str, variable: str, years: List[int], domain: str
) -> bool:
    short = short_name(variable)
    return all(
        config.harmonized_path(source_id, domain, short, y).exists() for y in years
    )


def _effective_domain(
    config: Config,
    source_id: str,
    variable: str,
    years: List[int],
    region_bbox,
    override: Optional[str],
) -> str:
    """Pick the cache domain for a request.

    Priority: an explicit override; any containing domain whose cache is
    already complete for these years (free reuse, smallest first); a
    region-scoped domain when the request is small (fetch only what is
    needed); the smallest containing domain otherwise.
    """
    if override:
        return override
    include_regions = config.fetch_scope != "domain"
    containing = config.containing_domains(region_bbox, include_regions)
    for name in containing:
        if _harmonized_complete(config, source_id, variable, years, name):
            return name
    base = containing[0] if containing else "global"
    if config.fetch_scope == "domain":
        return base
    rbox = round_region_bbox(region_bbox)
    if _bbox_area(rbox) > config.region_max_area_deg2:
        return base
    name = region_domain_name(rbox)
    if name not in config.domains:
        config.register_domain(name, rbox)
    return name


def _prefetch(config: Config, tasks: List[tuple]) -> None:
    """Ensure many (driver, variable, year, domain) files, in parallel.

    Downloads and CDS queue waits are I/O bound, so threads overlap them;
    the per-file locks in the cache make duplicate tasks harmless.
    """
    if config.max_workers <= 1 or len(tasks) <= 1:
        for drv, var, year, dom in tasks:
            drv.ensure_daily_year(var, year, dom)
        return
    with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
        futures = [
            ex.submit(drv.ensure_daily_year, var, year, dom)
            for drv, var, year, dom in tasks
        ]
        for fut in futures:
            fut.result()  # propagate the first failure


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
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)

    # Plan every variable first, then fetch everything in parallel.
    plans = []
    tasks = []
    for var in variables:
        driver, source_id = _driver_for(var, source, config)
        var_domain = _effective_domain(
            config, source_id, var, years, region_bbox, domain
        )
        short = short_name(var)
        stem = f"{freq.capitalize()}_{short}_{years[0]}_{years[-1]}"
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None
        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())
        plans.append(
            (var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif)
        )
        if need_nc or need_tif:
            tasks.extend((driver, var, y, var_domain) for y in years)
    _prefetch(config, tasks)

    results: Dict[str, dict] = {}
    for var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif in plans:
        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = xr.open_dataarray(nc_path)
        else:
            da = driver.open_years(var, years, var_domain)
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
                "domain": var_domain,
            }
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                da.to_netcdf(nc_path, encoding={da.name: nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                write_geotiff(da, tif_path, labels=time_labels(da, freq))
                write_manifest(tif_path, meta)

        results[var] = {
            "short": short_name(var),
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


def _plan_extraction(
    config: Config,
    variables: List[str],
    years: List[int],
    bbox,
    source: Optional[str],
):
    """Resolve drivers/domains per variable and prefetch all years in parallel."""
    plans = []
    for var in variables:
        driver, source_id = _driver_for(var, source, config)
        dom = _effective_domain(config, source_id, var, years, bbox, None)
        plans.append((var, driver, dom))
    _prefetch(
        config, [(drv, var, y, dom) for var, drv, dom in plans for y in years]
    )
    return plans


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
    plans = _plan_extraction(config, variables, years, bbox, source)

    # A mid-month start must not drop that month's aggregate.
    sel_start = start_ts.to_period("M").to_timestamp() if freq == "monthly" else start_ts

    frames = []
    for var, driver, dom in plans:
        da = driver.open_years(var, years, dom)
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
    plans = _plan_extraction(config, variables, years, bbox, source)

    pl_month = pl_v.dt.to_period("M").dt.to_timestamp()
    hv_month = hv_v.dt.to_period("M").dt.to_timestamp()
    n_months = (
        (hv_v.dt.year - pl_v.dt.year) * 12 + (hv_v.dt.month - pl_v.dt.month) + 1
    ).to_numpy()
    k_max = int(n_months.max())

    out = df.copy()
    new_cols: Dict[str, np.ndarray] = {}

    for var, driver, dom in plans:
        prefix = legacy_name(var) if legacy_names else short_name(var)
        daily = driver.open_years(var, years, dom)
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


# ---------------------------------------------------------------------------
# Static layers (soil, DEM): same fetch-once/cache/manifest pattern as the
# climate cubes, but with no time axis (see drivers/static.py).

DEM_DEFAULT_VARS = ["TOPO.ELEV", "TOPO.SLOPE", "TOPO.ASPECT", "TOPO.TPI", "TOPO.TRI"]
SOIL_DEFAULT_VARS = [
    "SOIL.CLAY", "SOIL.SAND", "SOIL.SILT", "SOIL.PH", "SOIL.SOC",
    "SOIL.NITROGEN", "SOIL.CEC", "SOIL.BDOD", "SOIL.CFVO",
]


def _as_static_variables(variables: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(variables, str):
        variables = [v for v in variables.split(",") if v.strip()]
    return [static_canonical_name(v) for v in variables]


def _static_driver_for(variable: str, source: Optional[str], config: Config):
    source_id = catalog.static_source_for(variable, source)
    entry = catalog.get_entry(source_id)
    return drivers.get_driver(entry, config), source_id


def _static_domain(
    config: Config,
    source_id: str,
    variable: str,
    region_bbox,
    override: Optional[str],
) -> str:
    """Pick the cache domain for a static request (no year axis).

    Same priorities as :func:`_effective_domain`: explicit override, then
    any containing domain whose static cache already exists, then a
    region-scoped domain for small requests.
    """
    if override:
        return override
    short = static_short_name(variable)
    include_regions = config.fetch_scope != "domain"
    containing = config.containing_domains(region_bbox, include_regions)
    for name in containing:
        if config.static_path(source_id, name, short).exists():
            return name
    base = containing[0] if containing else "global"
    if config.fetch_scope == "domain":
        return base
    rbox = round_region_bbox(region_bbox)
    if _bbox_area(rbox) > config.region_max_area_deg2:
        return base
    name = region_domain_name(rbox)
    if name not in config.domains:
        config.register_domain(name, rbox)
    return name


def _prefetch_static(config: Config, tasks: List[tuple]) -> None:
    """Ensure many (driver, variable, domain) static files, in parallel."""
    if config.max_workers <= 1 or len(tasks) <= 1:
        for drv, var, dom in tasks:
            drv.ensure_static(var, dom)
        return
    with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
        futures = [
            ex.submit(drv.ensure_static, var, dom) for drv, var, dom in tasks
        ]
        for fut in futures:
            fut.result()  # propagate the first failure


def _depth_tag(depths) -> str:
    """Filesystem tag for a depth subset ('' = all depths)."""
    if depths is None:
        return ""
    depths = [depths] if isinstance(depths, str) else list(depths)
    return "_" + "_".join(d.replace("-", "to").replace("cm", "") for d in depths)


def _subset_depths(da: xr.DataArray, depths) -> xr.DataArray:
    if depths is None or "depth" not in da.dims:
        return da
    depths = [depths] if isinstance(depths, str) else list(depths)
    available = [str(d) for d in da["depth"].values]
    unknown = [d for d in depths if d not in available]
    if unknown:
        raise ValueError(f"Unknown depths {unknown}. Available: {available}")
    return da.sel(depth=depths)


def get_static(
    variables: Union[str, Sequence[str]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    depths: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    domain: Optional[str] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Fetch, harmonize and cache static layers (soil, DEM) for a region.

    Returns ``{canonical_variable: {"nc": Path, "tif": Path|None,
    "data": xr.DataArray}}`` like :func:`get_climate`. Soil layers carry a
    ``depth`` dimension (all six SoilGrids depths are cached; ``depths``
    subsets the returned product). ``TOPO.SLOPE``/``ASPECT``/``TPI``/``TRI``
    are derived from the cached elevation, fetched once.
    """
    config = config or Config.load()
    variables = _as_static_variables(variables)
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats

    gdf, region_bbox, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)

    plans = []
    tasks = []
    for var in variables:
        driver, source_id = _static_driver_for(var, source, config)
        var_domain = _static_domain(config, source_id, var, region_bbox, domain)
        short = static_short_name(var)
        stem = f"Static_{short}"
        if static_has_depth(var):
            stem += _depth_tag(depths)
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None
        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())
        plans.append(
            (var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif)
        )
        if need_nc or need_tif:
            tasks.append((driver, var, var_domain))
    _prefetch_static(config, tasks)

    from .drivers.static import static_nc_encoding

    results: Dict[str, dict] = {}
    for var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif in plans:
        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = xr.open_dataarray(nc_path)
        else:
            da = driver.open_static(var, var_domain)
            da = subset_bbox(da, region_bbox, buffer=0.05)
            da = _subset_depths(da, depths)
            if gdf is not None:
                da = clip_geometry(da, gdf)
            da = da.load()

            meta = {
                "source_id": source_id,
                "variable": var,
                "region": tag,
                "domain": var_domain,
            }
            if "depth" in da.dims:
                meta["depths"] = [str(d) for d in da["depth"].values]
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                da.to_netcdf(nc_path, encoding={da.name: static_nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                labels = (
                    [str(d) for d in da["depth"].values]
                    if "depth" in da.dims
                    else [static_short_name(var)]
                )
                write_geotiff(da, tif_path, labels=labels)
                write_manifest(tif_path, meta)

        results[var] = {
            "short": static_short_name(var),
            "source": source_id,
            "nc": nc_path if nc_path.exists() else None,
            "tif": tif_path if (tif_path and tif_path.exists()) else None,
            "data": da,
        }
    return results


def get_dem(
    variables: Union[str, Sequence[str], None] = None, **kwargs
) -> Dict[str, dict]:
    """Elevation and terrain derivatives (defaults: ELEV, SLOPE, ASPECT, TPI, TRI)."""
    return get_static(variables or DEM_DEFAULT_VARS, **kwargs)


def get_soil(
    variables: Union[str, Sequence[str], None] = None, **kwargs
) -> Dict[str, dict]:
    """SoilGrids soil properties (default: the fertilizer-module set)."""
    return get_static(variables or SOIL_DEFAULT_VARS, **kwargs)


# ---------------------------------------------------------------------------
# One trial-point extraction covers a small area, but a national trial set
# can span a whole country; fetching one static window for all of it could
# blow past memory at 30 m. Points are therefore grouped: one window when
# the rounded bbox is small, else per 1x1-degree cell (each cell's window
# is cached and reused by later extractions).
_EXTRACT_ONE_WINDOW_DEG2 = 4.0


def _static_point_cells(lons: np.ndarray, lats: np.ndarray):
    """Group point indices by bbox: one group for a tight cluster, else 1° cells."""
    bbox = round_region_bbox(points_bbox(lons, lats, buffer=0.0), pad=0.0)
    if _bbox_area(bbox) <= _EXTRACT_ONE_WINDOW_DEG2:
        return {tuple(bbox): np.arange(len(lons))}
    cells: Dict[tuple, list] = {}
    for i, (x, y) in enumerate(zip(lons, lats)):
        cell = (float(np.floor(x)), float(np.floor(y)))
        cells.setdefault(
            (cell[0], cell[1], cell[0] + 1.0, cell[1] + 1.0), []
        ).append(i)
    return {box: np.asarray(idx) for box, idx in cells.items()}


def extract_static_points(
    points,
    variables: Union[str, Sequence[str]],
    depths: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Soil/topography values at point locations (wide format).

    Returns the input data plus one column per static variable —
    ``ELEV``, ``SLOPE``, ... for topography and ``CLAY_0_5cm``,
    ``CLAY_5_15cm``, ... for soil properties (one column per depth).
    This is the static counterpart of :func:`extract_growing_season`:
    the fertilizer module extracts soil and terrain at trial points.
    """
    config = config or Config.load()
    variables = _as_static_variables(variables)
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)
    valid = df[lon_col].notna() & df[lat_col].notna()
    if valid.sum() == 0:
        raise ValueError("No rows with valid coordinates")

    sub = df[valid]
    lons = sub[lon_col].to_numpy(dtype=float)
    lats = sub[lat_col].to_numpy(dtype=float)
    cells = _static_point_cells(lons, lats)

    # Resolve (driver, domain) per (variable, cell) and prefetch in parallel.
    plans: Dict[tuple, tuple] = {}
    tasks = []
    for var in variables:
        driver, source_id = _static_driver_for(var, source, config)
        for box in cells:
            dom = _static_domain(config, source_id, var, list(box), None)
            plans[(var, box)] = (driver, dom)
            tasks.append((driver, var, dom))
    _prefetch_static(config, [t for t in dict.fromkeys(tasks)])

    new_cols: Dict[str, np.ndarray] = {}
    for var in variables:
        short = static_short_name(var)
        depth_labels = None
        collected: Dict[str, np.ndarray] = {}
        for box, idx in cells.items():
            driver, dom = plans[(var, box)]
            da = driver.open_static(var, dom)
            da = _subset_depths(da, depths)
            ilon = xr.DataArray(lons[idx], dims="point")
            ilat = xr.DataArray(lats[idx], dims="point")
            vals = da.sel(lon=ilon, lat=ilat, method="nearest").load()
            if "depth" in vals.dims:
                labels = [str(d) for d in vals["depth"].values]
                if depth_labels is None:
                    depth_labels = labels
                for d, label in enumerate(labels):
                    key = f"{short}_{label.replace('-', '_')}"
                    col = collected.setdefault(
                        key, np.full(len(sub), np.nan, dtype="float32")
                    )
                    col[idx] = vals.isel(depth=d).values
            else:
                col = collected.setdefault(
                    short, np.full(len(sub), np.nan, dtype="float32")
                )
                col[idx] = vals.values
        new_cols.update(collected)

    out = df.copy()
    for name, values in new_cols.items():
        col = pd.Series(np.nan, index=df.index, dtype="float64")
        col.loc[sub.index] = values
        out[name] = col
    return out


__all__ = [
    "get_climate",
    "extract_points",
    "extract_growing_season",
    "rainy_days",
    "get_static",
    "get_dem",
    "get_soil",
    "extract_static_points",
]
