"""Public API of the AgWise data-sourcing layer.

Grouped by what they return (see REFERENCE.md for the full per-function
reference with parameters, outputs and examples):

* **Gridded cubes** → ``{canonical_var: {"nc", "tif", "data"}}``:
  :func:`get_climate`, :func:`get_static` (+ :func:`get_dem`/:func:`get_soil`),
  :func:`get_seasonal`, :func:`get_modis` (+ :func:`get_ndvi`),
  :func:`get_cropmask`, :func:`get_season`, :func:`smooth_ndvi`.
* **Point extraction** → ``pandas.DataFrame``: :func:`extract_points`,
  :func:`extract_growing_season`, :func:`extract_static_points`.
* **Crop-model input files** → ``list`` of written files: :func:`to_dssat`,
  :func:`to_apsim`, :func:`to_wofost`, :func:`to_oryza`,
  :func:`forecast_to_dssat`.
* **Spatial scaffolding** → ``pandas.DataFrame``: :func:`make_grid`,
  :func:`tag_admin`.
* **Seasonal-forecast bias correction** → corrected cubes:
  :func:`bias_correct`.

Performance: all (variable, year) fetches run in a thread pool
(``config.max_workers``) so downloads and CDS queue waits overlap, and small
requests get a *region-scoped* cache (see ``config.fetch_scope``) so a
one-country run fetches only that country's window, not a whole continent.
"""

from __future__ import annotations

import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd
import xarray as xr

from . import boundaries, catalog, drivers, progress
from .cache import atomic_write, write_manifest
from .config import Config, region_domain_name, round_region_bbox
from .drivers.base import nc_encoding
from .harmonize import (
    canonical_name,
    legacy_name,
    rainy_days,
    rs_canonical_name,
    rs_short_name,
    short_name,
    static_canonical_name,
    static_derived_from,
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


def _source_covers_years(source_id: str, years) -> bool:
    """Does ``source_id``'s catalog temporal extent cover every year in ``years``?

    Used to decide whether a preferred local rainfall source can serve the
    request; if ``years`` is unknown (None/empty) we cannot promise coverage,
    so the caller keeps the safe catalog default.
    """
    if not years:
        return False
    temporal = catalog.get_entry(source_id).get("extent", {}).get("temporal", {})
    start, end = temporal.get("start"), temporal.get("end")
    lo = int(str(start)[:4]) if start else None
    hi = int(str(end)[:4]) if end else None
    ys = [int(y) for y in years]
    if lo is not None and min(ys) < lo:
        return False
    if hi is not None and max(ys) > hi:
        return False
    return True


def _effective_source(variable: str, source, config: Config, years=None):
    """Resolve the source for ``variable``, applying the rainfall preference.

    An explicit ``source`` (a source id, or a mapping that names this variable)
    always wins. Otherwise, for rainfall (``PRCP``) only, honour
    ``config.rainfall_source`` (e.g. local CHIRPS v3 on CGLabs) when it covers
    all requested years — falling back to the catalog default (CHIRPS v2) for
    uncovered years or off-CGLabs. Returns a source id or ``None`` (= default).
    """
    forced = catalog._source_override(source, canonical_name(variable))
    if forced is not None:
        return forced
    pref = getattr(config, "rainfall_source", None)
    if (
        pref
        and canonical_name(variable) == canonical_name("PRCP")
        and _source_covers_years(pref, years)
    ):
        return pref
    return None


def _driver_for(variable: str, source, config: Config, years=None):
    source_id = catalog.source_for(
        variable, _effective_source(variable, source, config, years)
    )
    entry = catalog.get_entry(source_id)
    return drivers.get_driver(entry, config), source_id


def _resolve_region(
    config: Config,
    country: Optional[str],
    bbox: Optional[Sequence[float]],
    admin_level: int,
    admin_name: Optional[str],
    geometry=None,
):
    """Returns (gdf_or_None, bbox, region_tag).

    Region priority: an uploaded ``geometry`` (AOI), then ``country``
    (optionally an admin unit), then ``bbox``.
    """
    if geometry is not None:
        gdf = boundaries.load_aoi(geometry)
        return gdf, boundaries.geometry_bbox(gdf), boundaries.aoi_tag(gdf, geometry)
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
    raise ValueError("Provide geometry=..., country=... or bbox=(w, s, e, n)")


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
    complete_fn=None,
) -> str:
    """Pick the cache domain for a request.

    Priority: an explicit override; any containing domain whose cache is
    already complete for these years (free reuse, smallest first); a
    region-scoped domain when the request is small (fetch only what is
    needed); the smallest containing domain otherwise. ``complete_fn``
    overrides the cache-completeness test (seasonal files are keyed
    differently from the daily climate files).
    """
    if override:
        return override
    complete = complete_fn or (
        lambda name: _harmonized_complete(config, source_id, variable, years, name)
    )
    include_regions = config.fetch_scope != "domain"
    containing = config.containing_domains(region_bbox, include_regions)
    for name in containing:
        if complete(name):
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

    When this runs several fetches at once, the inner per-fetch fan-out
    (``cog_workers``, used by the CHIRPS COG/GEE paths) is pinned to 1 for the
    duration so peak concurrency is ``max_workers`` — never the
    ``max_workers x cog_workers`` product that could put 30+ window/tile reads
    and several year-arrays in flight at once. A single-task (serial) prefetch
    keeps the full inner fan-out.
    """
    if config.max_workers <= 1 or len(tasks) <= 1:
        for drv, var, year, dom in progress.track(tasks, desc="Fetching climate"):
            drv.ensure_daily_year(var, year, dom)
        return
    with _pinned_cog_workers(config, 1):
        with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
            futures = [
                ex.submit(drv.ensure_daily_year, var, year, dom)
                for drv, var, year, dom in tasks
            ]
            progress.drain_futures(futures, desc="Fetching climate")


@contextmanager
def _pinned_cog_workers(config: Config, value: int):
    """Temporarily set ``config.cog_workers`` (restore on exit).

    Used to break the nested outer-pool x inner-fan-out multiplier: while an
    outer prefetch pool is active, inner fetches should not each spawn their
    own ``cog_workers`` threads.
    """
    saved = config.cog_workers
    config.cog_workers = value
    try:
        yield
    finally:
        config.cog_workers = saved


def _write_nc_product(da: xr.DataArray, path, encoding: dict) -> None:
    """Write a product NetCDF atomically (temp file + rename).

    A crash mid-write must not leave a half-written or zero-variable ``.nc``
    that a later run treats as a cache hit and fails to open — the failure
    surfaced in a QA run where a broken write poisoned every subsequent call.
    """
    with atomic_write(Path(path)) as tmp:
        da.to_netcdf(tmp, encoding=encoding)


def _write_tif_product(da: xr.DataArray, path, labels) -> None:
    """Write a product GeoTIFF atomically, preserving the ``.tif`` suffix.

    rioxarray infers the driver from the extension, so the temp file keeps
    ``.tif`` (unlike the NetCDF path, which is format-sniffed by content).
    """
    from .spatial import write_geotiff

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        write_geotiff(da, tmp, labels=labels)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _open_product_da(nc_path) -> xr.DataArray:
    """Open a cached product's single data variable.

    A country-clipped product carries a ``spatial_ref`` CRS variable (written
    by rioxarray during the geometry clip), so the NetCDF has more than one
    variable and ``xr.open_dataarray`` would reject it. Pick the one real data
    variable, ignoring the CRS placeholder.
    """
    ds = xr.open_dataset(nc_path)
    names = [v for v in ds.data_vars if v not in ("spatial_ref", "crs")]
    if len(names) != 1:
        raise ValueError(
            f"{nc_path}: expected one data variable, found {list(ds.data_vars)}"
        )
    return ds[names[0]]


# ---------------------------------------------------------------------------
def get_climate(
    variables: Union[str, Sequence[str]],
    years: Union[int, Sequence[int]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
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
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)

    # Plan every variable first, then fetch everything in parallel.
    plans = []
    tasks = []
    for var in variables:
        driver, source_id = _driver_for(var, source, config, years)
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
            da = _open_product_da(nc_path)
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
                _write_nc_product(da, nc_path, {da.name: nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                _write_tif_product(da, tif_path, labels=time_labels(da, freq))
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
        driver, source_id = _driver_for(var, source, config, years)
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
    """Long-format climate time series at point locations between two dates.

    ``points`` is a CSV path or DataFrame with longitude/latitude columns
    (auto-detected, or pass ``lon_col``/``lat_col``). ``variables`` are
    climate short/canonical names; ``start``/``end`` are ISO dates; ``freq``
    is ``"daily"`` or ``"monthly"``. Returns a long DataFrame with columns
    ``point, <lon_col>, <lat_col>, time, variable, value`` (one row per point
    x time x variable).
    """
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
    """Ensure many (driver, variable, domain) static files.

    Network-bound layers (soil, DEM elevation) run in parallel. Derived terrain
    variables (slope/aspect/TPI/TRI) are pure local compute off one shared
    elevation cache — running them concurrently only multiplies peak memory
    (each holds a full derivative array) with no I/O to overlap, so they run
    serially, after the parallel batch has populated any parent elevation.
    """
    def _is_derived(var):
        return bool(static_derived_from(static_canonical_name(var)))

    base = [t for t in tasks if not _is_derived(t[1])]
    derived = [t for t in tasks if _is_derived(t[1])]

    if config.max_workers <= 1 or len(base) <= 1:
        for drv, var, dom in progress.track(base, desc="Fetching soil/terrain"):
            drv.ensure_static(var, dom)
    elif base:
        with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
            futures = [
                ex.submit(drv.ensure_static, var, dom) for drv, var, dom in base
            ]
            progress.drain_futures(futures, desc="Fetching soil/terrain")

    for drv, var, dom in progress.track(derived, desc="Deriving terrain"):
        drv.ensure_static(var, dom)


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
    geometry=None,
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
        config, country, bbox, admin_level, admin_name, geometry
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
            da = _open_product_da(nc_path)
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
                _write_nc_product(da, nc_path, {da.name: static_nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                labels = (
                    [str(d) for d in da["depth"].values]
                    if "depth" in da.dims
                    else [static_short_name(var)]
                )
                _write_tif_product(da, tif_path, labels=labels)
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


def get_cropmask(**kwargs) -> Dict[str, dict]:
    """ESA WorldCover cropland mask on the MODIS grid (1 = cropland, NaN else).

    A static layer (no time axis) aligned pixel-for-pixel with the MODIS
    NDVI/EVI composites, so the phenology workflow can mask non-cropland by
    multiplying the composite stack by it. Returns the same
    ``{canonical_variable: {...}}`` structure as :func:`get_static`.
    """
    return get_static("LC.CROPLAND", **kwargs)


# ---------------------------------------------------------------------------
# Seasonal forecasts/hindcasts (SEAS5): Jemal's standardization proposal.

_DEFAULT_SEASONAL_SOURCE = "seas5"


def _seasonal_driver_for(variable: str, source: Optional[str], config: Config):
    source_id = source or _DEFAULT_SEASONAL_SOURCE
    entry = catalog.get_entry(source_id)
    if canonical_name(variable) not in entry.get("variables", {}):
        raise ValueError(
            f"Source '{source_id}' does not provide {canonical_name(variable)}. "
            f"It provides: {sorted(entry.get('variables', {}))}"
        )
    return drivers.get_driver(entry, config), source_id


def _seasonal_complete(
    config: Config,
    source_id: str,
    variable: str,
    init_month: int,
    years: List[int],
    domain: str,
) -> bool:
    short = short_name(variable)
    return all(
        config.seasonal_path(source_id, domain, short, init_month, y).exists()
        for y in years
    )


def _prefetch_seasonal(config: Config, tasks: List[tuple]) -> None:
    """Ensure many (driver, variable, init_month, year, domain) files in
    parallel — CDS queue waits overlap, like the climate prefetch."""
    tasks = list(dict.fromkeys(tasks))
    if config.max_workers <= 1 or len(tasks) <= 1:
        for drv, var, init, year, dom in tasks:
            drv.ensure_seasonal(var, init, year, dom)
        return
    with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
        futures = [
            ex.submit(drv.ensure_seasonal, var, init, year, dom)
            for drv, var, init, year, dom in tasks
        ]
        for fut in futures:
            fut.result()  # propagate the first failure


def get_seasonal(
    variables: Union[str, Sequence[str]],
    init_month: int,
    years: Union[int, Sequence[int]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    ensemble: str = "members",
    source: Optional[str] = None,
    domain: Optional[str] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Fetch, harmonize and cache seasonal forecast/hindcast cubes.

    One initialization month across ``years`` (hindcast range and/or
    real-time years) — the input the planting-date module bias-corrects
    against the :func:`get_climate` observations. Returns
    ``{canonical_variable: {"nc": Path, "tif": Path|None, "data":
    xr.DataArray}}`` where the data has dims ``(member, time, lat, lon)``
    and ``time`` is the valid date (init + lead, daily steps, ~7 months
    per year). ``ensemble="mean"``/``"median"`` reduces the member axis
    (required for GeoTIFF export); the default keeps all members.
    """
    config = config or Config.load()
    variables = _as_variables(variables)
    years = _as_years(years)
    init_month = int(init_month)
    if not 1 <= init_month <= 12:
        raise ValueError(f"init_month must be 1..12, got {init_month}")
    if ensemble not in ("members", "mean", "median"):
        raise ValueError("ensemble must be 'members', 'mean' or 'median'")
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats
    if write_tif and ensemble == "members":
        raise ValueError(
            "GeoTIFF export needs a reduced ensemble: use ensemble='mean' "
            "or 'median' (a members × time cube does not fit raster bands)"
        )

    gdf, region_bbox, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)

    from .drivers.seasonal import seasonal_nc_encoding

    plans = []
    tasks = []
    for var in variables:
        driver, source_id = _seasonal_driver_for(var, source, config)
        var_domain = _effective_domain(
            config, source_id, var, years, region_bbox, domain,
            complete_fn=lambda name, s=source_id, v=var: _seasonal_complete(
                config, s, v, init_month, years, name
            ),
        )
        short = short_name(var)
        stem = f"Seasonal_{short}_i{init_month:02d}_{years[0]}_{years[-1]}"
        if ensemble != "members":
            stem += f"_{ensemble}"
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None
        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())
        plans.append(
            (var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif)
        )
        if need_nc or need_tif:
            tasks.extend((driver, var, init_month, y, var_domain) for y in years)
    _prefetch_seasonal(config, tasks)

    results: Dict[str, dict] = {}
    for var, driver, source_id, var_domain, nc_path, tif_path, need_nc, need_tif in plans:
        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = _open_product_da(nc_path)
        else:
            da = driver.open_inits(var, init_month, years, var_domain)
            da = subset_bbox(da, region_bbox, buffer=0.05)
            if gdf is not None:
                da = clip_geometry(da, gdf)
            if ensemble != "members":
                da = getattr(da, ensemble)(dim="member", keep_attrs=True)
            da = da.load()

            meta = {
                "source_id": source_id,
                "variable": var,
                "region": tag,
                "init_month": init_month,
                "years": [years[0], years[-1]],
                "ensemble": ensemble,
                "domain": var_domain,
            }
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                _write_nc_product(da, nc_path, {da.name: seasonal_nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                _write_tif_product(da, tif_path, labels=time_labels(da, "daily"))
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
# MODIS vegetation-index composites (Terra + Aqua interleaved): the input
# of the planting-date phenology workflow.

_MODIS_SATELLITE_SOURCES = {"terra": "mod13q1", "aqua": "myd13q1"}


def _as_rs_variables(variables: Union[str, Sequence[str]]) -> List[str]:
    if isinstance(variables, str):
        variables = [v for v in variables.split(",") if v.strip()]
    return [rs_canonical_name(v) for v in variables]


def _modis_driver_for(variable: str, source_id: str, config: Config):
    entry = catalog.get_entry(source_id)
    if rs_canonical_name(variable) not in entry.get("variables", {}):
        raise ValueError(
            f"Source '{source_id}' does not provide {rs_canonical_name(variable)}. "
            f"It provides: {sorted(entry.get('variables', {}))}"
        )
    return drivers.get_driver(entry, config)


def _modis_complete(
    config: Config,
    source_id: str,
    variable: str,
    years: List[int],
    domain: str,
) -> bool:
    short = rs_short_name(variable)
    return all(
        config.composite_path(source_id, domain, short, y).exists() for y in years
    )


def _prefetch_modis(config: Config, tasks: List[tuple]) -> None:
    """Ensure many (driver, variable, year, domain) composite files.

    Runs serially: each yearly fetch already parallelizes its per-composite
    GEE pixel pulls (``config.cog_workers``), and Earth Engine rate-limits
    aggressive clients."""
    for drv, var, year, dom in dict.fromkeys(tasks):
        drv.ensure_composite_year(var, year, dom)


def get_modis(
    variables: Union[str, Sequence[str]],
    years: Union[int, Sequence[int]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    satellite: str = "both",
    source: Union[str, Sequence[str], None] = None,
    domain: Optional[str] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Fetch, harmonize and cache MODIS vegetation-index composite stacks.

    Returns ``{canonical_variable: {"nc": Path, "tif": Path|None, "data":
    xr.DataArray}}`` where the data has dims ``(time, lat, lon)`` and
    ``time`` holds the composite start dates. The default
    ``satellite="both"`` interleaves Terra (MOD13Q1) and Aqua (MYD13Q1)
    into the 46-composites-per-year series the planting-date phenology
    workflow expects; ``"terra"``/``"aqua"`` keep a single satellite
    (23 per year). GeoTIFF band labels carry the composite date
    (``2021_01_17``), so year-based layer selection keeps working.
    """
    config = config or Config.load()
    variables = _as_rs_variables(variables)
    years = _as_years(years)
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats

    if source:
        source_ids = [source] if isinstance(source, str) else list(source)
        suffix = "_" + "-".join(source_ids)
    elif satellite == "both":
        source_ids = list(_MODIS_SATELLITE_SOURCES.values())
        suffix = ""
    elif satellite in _MODIS_SATELLITE_SOURCES:
        source_ids = [_MODIS_SATELLITE_SOURCES[satellite]]
        suffix = f"_{satellite}"
    else:
        raise ValueError(
            f"satellite must be 'both', 'terra' or 'aqua', got '{satellite}'"
        )

    gdf, region_bbox, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)

    from .drivers.modis import composite_nc_encoding

    plans = []
    tasks = []
    for var in variables:
        parts = []
        for sid in source_ids:
            driver = _modis_driver_for(var, sid, config)
            var_domain = _effective_domain(
                config, sid, var, years, region_bbox, domain,
                complete_fn=lambda name, s=sid, v=var: _modis_complete(
                    config, s, v, years, name
                ),
            )
            parts.append((sid, driver, var_domain))
        short = rs_short_name(var)
        stem = f"Composite_{short}_{years[0]}_{years[-1]}{suffix}"
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None
        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())
        plans.append((var, parts, nc_path, tif_path, need_nc, need_tif))
        if need_nc or need_tif:
            tasks.extend(
                (driver, var, y, dom) for _, driver, dom in parts for y in years
            )
    _prefetch_modis(config, tasks)

    results: Dict[str, dict] = {}
    for var, parts, nc_path, tif_path, need_nc, need_tif in plans:
        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = _open_product_da(nc_path)
        else:
            stacks = [
                driver.open_years(var, years, dom) for _, driver, dom in parts
            ]
            da = (
                stacks[0]
                if len(stacks) == 1
                else xr.concat(
                    stacks, dim="time", join="outer",
                    combine_attrs="drop_conflicts",
                )
            )
            da = da.sortby("time")
            da = subset_bbox(da, region_bbox, buffer=0.05)
            if gdf is not None:
                da = clip_geometry(da, gdf)
            da = da.load()

            meta = {
                "source_ids": [sid for sid, _, _ in parts],
                "satellite": "custom" if source else satellite,
                "variable": var,
                "region": tag,
                "years": [years[0], years[-1]],
                "n_composites": int(da.sizes["time"]),
                "domains": {sid: dom for sid, _, dom in parts},
            }
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                _write_nc_product(da, nc_path, {da.name: composite_nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                _write_tif_product(da, tif_path, labels=time_labels(da, "daily"))
                write_manifest(tif_path, meta)

        results[var] = {
            "short": rs_short_name(var),
            "source": ",".join(sid for sid, _, _ in parts),
            "nc": nc_path if nc_path.exists() else None,
            "tif": tif_path if (tif_path and tif_path.exists()) else None,
            "data": da,
        }
    return results


def get_ndvi(**kwargs) -> Dict[str, dict]:
    """MODIS NDVI composites (Terra + Aqua interleaved by default)."""
    return get_modis(variables=["RS.NDVI"], **kwargs)


def smooth_ndvi(
    years: Union[int, Sequence[int]],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    satellite: str = "both",
    source: Union[str, Sequence[str], None] = None,
    domain: Optional[str] = None,
    cropmask: bool = True,
    cropmask_source: Optional[str] = None,
    window: int = 9,
    polyorder: int = 3,
    gapfill: str = "linear",
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Gap-fill and Savitzky-Golay smooth the MODIS NDVI composite stack.

    Turns the raw NDVI composites — with cloud/QA gaps left as NaN by the
    drivers — into the analysis-ready smoothed time series the planting-date
    phenology workflow needs; the port of the legacy ``get_MODISts_PreProc.R``.
    Per pixel, NaN gaps are filled (``gapfill="linear"`` interpolates along the
    time axis, the default; ``"mean"`` reproduces the legacy per-pixel mean),
    then a Savitzky-Golay filter (``window``/``polyorder``, MODIS defaults 9/3)
    runs along time. With ``cropmask=True`` (default) the ESA WorldCover
    cropland mask (:func:`get_cropmask`) is aligned to the NDVI grid by nearest
    neighbour and non-cropland pixels are set to NaN before smoothing;
    ``cropmask_source`` overrides the cropland source.

    Returns ``{"RS.NDVI": {"short", "source", "nc", "tif", "data"}}`` like
    :func:`get_modis`, with ``data`` the smoothed ``(time, lat, lon)`` cube
    written as a ``Smoothed_NDVI_<y0>_<y1>[_sat]_SG`` product. Non-default
    ``window``/``polyorder`` and a ``"mean"`` gap-fill are appended to the
    product name so they never collide with a default-smoothed cache entry.
    """
    from .smoothing import apply_cropmask, smooth_stack

    config = config or Config.load()
    years = _as_years(years)
    canon = rs_canonical_name("NDVI")
    short = rs_short_name(canon)
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats

    if source:
        source_ids = [source] if isinstance(source, str) else list(source)
        suffix = "_" + "-".join(source_ids)
    elif satellite == "both":
        suffix = ""
    elif satellite in _MODIS_SATELLITE_SOURCES:
        suffix = f"_{satellite}"
    else:
        raise ValueError(
            f"satellite must be 'both', 'terra' or 'aqua', got '{satellite}'"
        )
    if (window, polyorder) != (9, 3):
        suffix += f"_w{window}p{polyorder}"
    if gapfill != "linear":
        suffix += f"_{gapfill}"

    _, _, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)
    stem = f"Smoothed_{short}_{years[0]}_{years[-1]}{suffix}_SG"
    nc_path = out_root / f"{stem}.nc"
    tif_path = out_root / f"{stem}.tif" if write_tif else None
    need_nc = overwrite or not nc_path.exists()
    need_tif = write_tif and (overwrite or not tif_path.exists())

    if not need_nc and not need_tif:
        logger.info("Product cache hit: %s", nc_path)
        da = _open_product_da(nc_path)
    else:
        region_kwargs = dict(
            country=country, bbox=bbox, admin_level=admin_level,
            admin_name=admin_name, geometry=geometry, out_dir=out_dir,
            config=config,
        )
        raw = get_modis(
            variables=[canon], years=years, satellite=satellite, source=source,
            domain=domain, **region_kwargs,
        )[canon]["data"]
        if cropmask:
            cm = get_cropmask(source=cropmask_source, domain=domain, **region_kwargs)
            mask = next(iter(cm.values()))["data"]
            raw = apply_cropmask(raw, mask)
        da = smooth_stack(
            raw, window=window, polyorder=polyorder, gapfill=gapfill
        ).load()

        meta = {
            "variable": canon,
            "region": tag,
            "years": [years[0], years[-1]],
            "satellite": "custom" if source else satellite,
            "cropmask": bool(cropmask),
            "smoothing": {
                "method": "savitzky_golay",
                "window": int(window),
                "polyorder": int(polyorder),
                "gapfill": gapfill,
            },
            "n_composites": int(da.sizes["time"]),
        }
        if need_nc:
            nc_path.parent.mkdir(parents=True, exist_ok=True)
            _write_nc_product(da, nc_path, {da.name: nc_encoding(da)})
            write_manifest(nc_path, meta)
        if need_tif:
            from .spatial import write_geotiff

            _write_tif_product(da, tif_path, labels=time_labels(da, "daily"))
            write_manifest(tif_path, meta)

    return {
        canon: {
            "short": short,
            "source": ",".join(
                [source] if isinstance(source, str) else list(source)
            ) if source else satellite,
            "nc": nc_path if nc_path.exists() else None,
            "tif": tif_path if (tif_path and tif_path.exists()) else None,
            "data": da,
        }
    }


# ---------------------------------------------------------------------------
# Season-ready delivery: climate + NDVI already sliced to a planting -> harvest
# window (scope map #2). Not to be confused with get_seasonal (the SEAS5
# forecast). A "season" here is any date range, including one that crosses the
# calendar year (e.g. Rwanda season B, Sep -> Feb): because the underlying time
# axis is continuous, a cross-year slice is just slice(planting, harvest).


def _classify_season_var(variable: str):
    """Return ('rs', canonical) for NDVI/EVI, else ('climate', canonical)."""
    try:
        return "rs", rs_canonical_name(variable)
    except ValueError:
        return "climate", canonical_name(variable)


def _season_dates(planting_date, harvest_date):
    pl = pd.Timestamp(planting_date)
    hv = pd.Timestamp(harvest_date)
    if pd.isna(pl) or pd.isna(hv):
        raise ValueError("planting_date and harvest_date must be valid dates")
    if pl > hv:
        raise ValueError(
            f"planting_date ({pl.date()}) is after harvest_date ({hv.date()})"
        )
    return pl, hv


def _modis_stack_driverlevel(config, var, years, bbox, satellite, source):
    """(time, lat, lon) NDVI/EVI stack from the MODIS drivers, no product write.

    Mirrors the source selection and Terra+Aqua interleave of
    :func:`get_modis` but stays at driver level so point-mode season slices
    do not persist a whole-region composite product.
    """
    if source:
        source_ids = [source] if isinstance(source, str) else list(source)
    elif satellite == "both":
        source_ids = list(_MODIS_SATELLITE_SOURCES.values())
    elif satellite in _MODIS_SATELLITE_SOURCES:
        source_ids = [_MODIS_SATELLITE_SOURCES[satellite]]
    else:
        raise ValueError(
            f"satellite must be 'both', 'terra' or 'aqua', got '{satellite}'"
        )
    parts = []
    tasks = []
    for sid in source_ids:
        driver = _modis_driver_for(var, sid, config)
        dom = _effective_domain(
            config, sid, var, years, bbox, None,
            complete_fn=lambda name, s=sid: _modis_complete(
                config, s, var, years, name
            ),
        )
        parts.append((driver, dom))
        tasks.extend((driver, var, y, dom) for y in years)
    _prefetch_modis(config, tasks)
    stacks = [driver.open_years(var, years, dom) for driver, dom in parts]
    da = stacks[0] if len(stacks) == 1 else xr.concat(
        stacks, dim="time", join="outer", combine_attrs="drop_conflicts"
    )
    return da.sortby("time")


def _season_long_points(
    config, variables, df, lon_col, lat_col, pl_v, hv_v, freq, satellite, source
):
    """Per-point season slice -> long DataFrame (point, lon, lat, time, variable, value).

    ``pl_v``/``hv_v`` are per-row planting/harvest Timestamps (already
    aligned to ``df``); each point keeps only the rows inside its own window,
    so different rows can have different (even cross-year) seasons.
    """
    lons = df[lon_col].to_numpy(dtype=float)
    lats = df[lat_col].to_numpy(dtype=float)
    bbox = points_bbox(lons, lats)
    years = list(range(int(pl_v.dt.year.min()), int(hv_v.dt.year.max()) + 1))
    pl_np = pl_v.dt.normalize().to_numpy()
    hv_np = hv_v.dt.normalize().to_numpy()

    frames = []
    for var in variables:
        kind, canon = _classify_season_var(var)
        if kind == "rs":
            da = _modis_stack_driverlevel(
                config, canon, years, bbox, satellite, source
            )
            da = subset_bbox(da, bbox, buffer=0.05)
            label = rs_short_name(canon)
        else:
            driver, source_id = _driver_for(canon, source, config, years)
            dom = _effective_domain(config, source_id, canon, years, bbox, None)
            _prefetch(config, [(driver, canon, y, dom) for y in years])
            da = driver.open_years(canon, years, dom)
            da = subset_bbox(da, bbox)
            if freq == "monthly":
                da = to_monthly(da, canon)
            label = short_name(canon)

        series = _point_series(da, lons, lats).load()  # (time, point)
        s_times = pd.DatetimeIndex(series["time"].values).normalize().to_numpy()
        vals = series.values  # (time, point)
        for p in range(len(df)):
            in_window = (s_times >= pl_np[p]) & (s_times <= hv_np[p])
            if not in_window.any():
                continue
            times_p = series["time"].values[in_window]
            frames.append(
                pd.DataFrame({
                    "point": df.index[p],
                    lon_col: lons[p],
                    lat_col: lats[p],
                    "time": times_p,
                    "variable": label,
                    "value": vals[in_window, p],
                })
            )
    if not frames:
        raise ValueError(
            "No data fell inside any point's season window; check the "
            "planting/harvest dates against the available years."
        )
    out = pd.concat(frames, ignore_index=True)
    return out[["point", lon_col, lat_col, "time", "variable", "value"]]


def get_season(
    variables: Union[str, Sequence[str]],
    planting_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    points=None,
    planting_col: Optional[str] = None,
    harvest_col: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    freq: str = "daily",
    satellite: str = "both",
    source: Union[str, Sequence[str], None] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir: Optional[Path] = None,
    overwrite: bool = False,
    config: Optional[Config] = None,
):
    """Climate and/or NDVI already sliced to a growing season.

    The scope-map deliverable so no module fetches whole years and slices
    afterwards. ``variables`` may mix climate names (``PRCP``, ``TMAX``, ...)
    and remote-sensing names (``NDVI``, ``EVI``); each is routed to the right
    source automatically. Seasons that cross the calendar year (Sep -> Feb)
    are handled naturally by the continuous time axis.

    Two modes:

    * **Region** (``country=`` or ``bbox=``): returns
      ``{canonical_variable: {"nc": Path, "tif": Path|None, "data": DataArray}}``
      with each cube sliced to ``[planting_date, harvest_date]`` and written
      as a ``Season_<SHORT>_<plYYYYMMDD>_<hvYYYYMMDD>`` product.
    * **Points** (``points=``): returns a long ``DataFrame`` (``point``,
      lon, lat, ``time``, ``variable``, ``value``) restricted to the season.
      With ``planting_col``/``harvest_col`` each row uses its own dates
      (per-trial seasons); otherwise the scalar ``planting_date``/
      ``harvest_date`` apply to every point.

    ``freq`` (``"daily"``/``"monthly"``) aggregates the climate variables;
    it does not affect the NDVI/EVI composite cadence. This is distinct from
    :func:`get_seasonal`, which fetches SEAS5 seasonal *forecasts*.
    """
    config = config or Config.load()
    if isinstance(variables, str):
        variables = [v for v in variables.split(",") if v.strip()]
    variables = list(variables)
    if not variables:
        raise ValueError("Provide at least one variable")
    if freq not in ("daily", "monthly"):
        raise ValueError("freq must be 'daily' or 'monthly'")

    # ---- Point mode -------------------------------------------------------
    if points is not None:
        df, lon_col, lat_col = _read_points(points, lon_col, lat_col)
        if planting_col or harvest_col:
            if not (planting_col and harvest_col):
                raise ValueError(
                    "Provide both planting_col and harvest_col, or neither"
                )
            pl_v = pd.to_datetime(df[planting_col], errors="coerce")
            hv_v = pd.to_datetime(df[harvest_col], errors="coerce")
        else:
            pl, hv = _season_dates(planting_date, harvest_date)
            pl_v = pd.Series(pl, index=df.index)
            hv_v = pd.Series(hv, index=df.index)
        bad = (
            pl_v.isna() | hv_v.isna() | (pl_v > hv_v)
            | df[lon_col].isna() | df[lat_col].isna()
        )
        if bad.all():
            raise ValueError(
                "No valid rows (unparseable dates, planting after harvest, "
                "or missing coordinates)"
            )
        if bad.any():
            warnings.warn(
                f"{int(bad.sum())}/{len(df)} rows skipped (unparseable dates, "
                "planting after harvest, or missing coordinates)."
            )
        sub = df[~bad]
        return _season_long_points(
            config, variables, sub, lon_col, lat_col,
            pl_v[~bad], hv_v[~bad], freq, satellite, source,
        )

    # ---- Region mode ------------------------------------------------------
    pl, hv = _season_dates(planting_date, harvest_date)
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc", "tif"):
            raise ValueError(f"Unknown output format '{f}' (use 'nc' and/or 'tif')")
    write_tif = "tif" in formats

    _, _, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)
    years = list(range(pl.year, hv.year + 1))
    tif_labels_freq = "monthly" if freq == "monthly" else "daily"
    region_kwargs = dict(
        country=country, bbox=bbox, admin_level=admin_level,
        admin_name=admin_name, geometry=geometry, out_dir=out_dir,
        config=config,
    )

    results: Dict[str, dict] = {}
    for var in variables:
        kind, canon = _classify_season_var(var)
        short = rs_short_name(canon) if kind == "rs" else short_name(canon)
        stem = f"Season_{short}_{pl:%Y%m%d}_{hv:%Y%m%d}"
        nc_path = out_root / f"{stem}.nc"
        tif_path = out_root / f"{stem}.tif" if write_tif else None
        need_nc = overwrite or not nc_path.exists()
        need_tif = write_tif and (overwrite or not tif_path.exists())

        if not need_nc and not need_tif:
            logger.info("Product cache hit: %s", nc_path)
            da = _open_product_da(nc_path)
        else:
            # Fetch (and cache) the whole-year cubes once via the existing
            # region calls, then slice to the season here.
            if kind == "rs":
                full = get_modis(
                    variables=[canon], years=years, satellite=satellite,
                    source=source, **region_kwargs,
                )[canon]["data"]
            else:
                full = get_climate(
                    variables=[canon], years=years, freq=freq,
                    source=source, **region_kwargs,
                )[canon]["data"]
            da = full.sel(time=slice(pl, hv)).load()
            if da.sizes.get("time", 0) == 0:
                raise ValueError(
                    f"No {short} time steps fell inside "
                    f"{pl.date()}..{hv.date()}"
                )
            meta = {
                "variable": canon,
                "region": tag,
                "planting_date": str(pl.date()),
                "harvest_date": str(hv.date()),
                "freq": freq if kind == "climate" else "composite",
                "n_steps": int(da.sizes["time"]),
            }
            if need_nc:
                nc_path.parent.mkdir(parents=True, exist_ok=True)
                _write_nc_product(da, nc_path, {da.name: nc_encoding(da)})
                write_manifest(nc_path, meta)
            if need_tif:
                from .spatial import write_geotiff

                _write_tif_product(da, tif_path, labels=time_labels(da, tif_labels_freq))
                write_manifest(tif_path, meta)

        results[canon] = {
            "short": short,
            "kind": kind,
            "nc": nc_path if nc_path.exists() else None,
            "tif": tif_path if (tif_path and tif_path.exists()) else None,
            "data": da,
        }
    return results


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


# SoilGrids masks urban areas and water bodies (NoData → NaN), so trial
# points in towns land on masked pixels. Decision (Lizeth, 2026-07-04):
# fill those from the nearest unmasked pixel within a bounded search
# radius, and record the donor distance so the fill is traceable.
_M_PER_DEG = 111_320.0


def _nearest_valid_fill(da: xr.DataArray, lon: float, lat: float, max_m: float):
    """Value(s) of the nearest non-NaN pixel within ``max_m`` meters.

    Returns ``(values, distance_m)`` — values is a per-depth vector when
    the layer has a depth dim — or ``None`` if no valid pixel is in range.
    A pixel only counts as valid when it is finite at *all* depths, so
    every depth column of a filled point comes from the same donor pixel.
    """
    dlat = max_m / _M_PER_DEG
    dlon = max_m / (_M_PER_DEG * max(np.cos(np.radians(lat)), 0.01))
    win = subset_bbox(da, (lon - dlon, lat - dlat, lon + dlon, lat + dlat))
    if win.sizes["lat"] == 0 or win.sizes["lon"] == 0:
        return None
    win = win.transpose(*(d for d in ("depth", "lat", "lon") if d in win.dims))
    win = win.load()
    finite = np.isfinite(win.values)
    valid = finite.all(axis=0) if "depth" in win.dims else finite
    if not valid.any():
        return None
    dy = (win["lat"].values[:, None] - lat) * _M_PER_DEG
    dx = (win["lon"].values[None, :] - lon) * _M_PER_DEG * np.cos(np.radians(lat))
    dist = np.where(valid, np.hypot(dx, dy), np.inf)
    j, i = np.unravel_index(np.argmin(dist), dist.shape)
    if dist[j, i] > max_m:
        return None
    donor = win.isel(lat=j, lon=i).values
    return donor, float(dist[j, i])


_DERIVE_BASE_VARS = {
    "hydraulics": ["SOIL.CLAY", "SOIL.SAND", "SOIL.SOC"],
    "olsen_p": ["SOIL.EXTP"],
}


def extract_static_points(
    points,
    variables: Union[str, Sequence[str]],
    depths: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    fill_nearest_m: Optional[float] = 1000.0,
    derive: Optional[Union[str, Sequence[str]]] = None,
    calcareous: bool = False,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Soil/topography values at point locations (wide format).

    Returns the input data plus one column per static variable —
    ``ELEV``, ``SLOPE``, ... for topography and ``CLAY_0_5cm``,
    ``CLAY_5_15cm``, ... for soil properties (one column per depth).
    This is the static counterpart of :func:`extract_growing_season`:
    the fertilizer module extracts soil and terrain at trial points.

    Points on masked pixels (SoilGrids NoData over urban areas/water) are
    filled from the nearest valid pixel within ``fill_nearest_m`` meters
    (default 1 km; pass ``None`` or 0 to disable). When enabled, each
    variable gets a ``<VAR>_fill_m`` column: 0 where the point's own pixel
    was valid, the donor-pixel distance in meters where it was filled, and
    NaN where no valid pixel was in range (the value stays NaN too).

    ``derive`` adds pedotransfer-derived columns (a name or list of names);
    it pulls in the base variables it needs (fetched if not already asked
    for). Supported:

    * ``"hydraulics"`` — Saxton & Rawls (2006) from CLAY/SAND/SOC: per depth
      ``PWP_<d>``, ``FC_<d>``, ``SAT_<d>`` (cm3/cm3) and ``KS_<d>`` (mm/h).
    * ``"olsen_p"`` — Olsen P (mg/kg) per depth ``OLSENP_<d>`` from Mehlich-3
      ``EXTP`` (``source="isda"``), via ``mehlich3_to_olsen`` (``calcareous``
      selects the calcareous regression).
    """
    from .writers.soil import saxton_rawls, mehlich3_to_olsen

    config = config or Config.load()
    variables = _as_static_variables(variables)
    derive_kinds = (
        [derive] if isinstance(derive, str)
        else list(derive) if derive else []
    )
    for kind in derive_kinds:
        if kind not in _DERIVE_BASE_VARS:
            raise ValueError(
                f"Unknown derive={kind!r}; supported: "
                f"{sorted(_DERIVE_BASE_VARS)}"
            )
        for base in _DERIVE_BASE_VARS[kind]:
            if base not in variables:
                variables.append(base)
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

    fill_enabled = bool(fill_nearest_m)
    new_cols: Dict[str, np.ndarray] = {}
    for var in variables:
        short = static_short_name(var)
        depth_labels = None
        collected: Dict[str, np.ndarray] = {}
        fill_m = np.full(len(sub), np.nan, dtype="float32")
        for box, idx in cells.items():
            driver, dom = plans[(var, box)]
            da = driver.open_static(var, dom)
            da = _subset_depths(da, depths)
            ilon = xr.DataArray(lons[idx], dims="point")
            ilat = xr.DataArray(lats[idx], dims="point")
            vals = da.sel(lon=ilon, lat=ilat, method="nearest").load()
            if "depth" in vals.dims:
                vals = vals.transpose("depth", "point")
            arr = np.asarray(vals.values, dtype="float32")
            if fill_enabled:
                bad = np.isnan(arr).any(axis=0) if arr.ndim == 2 else np.isnan(arr)
                dist = np.where(bad, np.nan, 0.0).astype("float32")
                for k in np.flatnonzero(bad):
                    hit = _nearest_valid_fill(
                        da, lons[idx][k], lats[idx][k], float(fill_nearest_m)
                    )
                    if hit is not None:
                        donor, d = hit
                        if arr.ndim == 2:
                            arr[:, k] = donor
                        else:
                            arr[k] = donor
                        dist[k] = d
                fill_m[idx] = dist
            if "depth" in vals.dims:
                labels = [str(d) for d in vals["depth"].values]
                if depth_labels is None:
                    depth_labels = labels
                for d, label in enumerate(labels):
                    key = f"{short}_{label.replace('-', '_')}"
                    col = collected.setdefault(
                        key, np.full(len(sub), np.nan, dtype="float32")
                    )
                    col[idx] = arr[d]
            else:
                col = collected.setdefault(
                    short, np.full(len(sub), np.nan, dtype="float32")
                )
                col[idx] = arr
        if fill_enabled:
            collected[f"{short}_fill_m"] = fill_m
        new_cols.update(collected)

    out = df.copy()
    for name, values in new_cols.items():
        col = pd.Series(np.nan, index=df.index, dtype="float64")
        col.loc[sub.index] = values
        out[name] = col

    def _depth_suffixes(short: str) -> list:
        # depth-tagged columns for a variable, excluding the <short>_fill_m col
        pref = f"{short}_"
        return [
            c[len(pref):] for c in out.columns
            if c.startswith(pref) and c != f"{short}_fill_m"
        ]

    if "hydraulics" in derive_kinds:
        for d in _depth_suffixes("CLAY"):
            if f"SAND_{d}" not in out or f"SOC_{d}" not in out:
                continue
            som = (out[f"SOC_{d}"].to_numpy() / 10.0) * 2.0  # SOC g/kg -> SOM %
            pwp, fc, sat, ks = saxton_rawls(
                out[f"CLAY_{d}"].to_numpy(), out[f"SAND_{d}"].to_numpy(), som
            )
            out[f"PWP_{d}"] = pwp
            out[f"FC_{d}"] = fc
            out[f"SAT_{d}"] = sat
            out[f"KS_{d}"] = ks
    if "olsen_p" in derive_kinds:
        for d in _depth_suffixes("EXTP"):
            out[f"OLSENP_{d}"] = mehlich3_to_olsen(
                out[f"EXTP_{d}"].to_numpy(), calcareous=calcareous
            )
    return out


# ---------------------------------------------------------------------------
# Crop-model input assembly (scope map #1): the "last mile" that writes the
# files a crop model reads, so the modules stop re-implementing readGeo_CM.
# The layer already produces the ingredients; these orchestrate season-sliced
# weather (extract) + soil-at-points (extract) + the writers into the engine's
# per-point folder layout.
_CM_WEATHER_VARS = ["TMAX", "TMIN", "SRAD", "PRCP"]
# WOFOST additionally needs relative humidity (-> vapour pressure) and wind.
_WOFOST_WEATHER_VARS = ["TMAX", "TMIN", "SRAD", "PRCP", "RHUM", "WIND"]
_CM_SOIL_VARS = ["CLAY", "SAND", "SILT", "SOC", "NITROGEN", "PH", "CEC", "BDOD"]


def _cm_inputs(
    points, planting_date, harvest_date, planting_col, harvest_col,
    lon_col, lat_col, weather, soil, weather_source, soil_source, config,
    weather_vars=None, need_elev=False,
):
    """Resolve the per-point weather (long) and soil (wide) inputs for a writer.

    Fetches them from the layer when not supplied, so a caller can either let
    ``to_dssat``/``to_apsim``/``to_wofost`` source everything or pass
    pre-extracted frames. ``weather_vars`` overrides the weather set fetched
    (WOFOST needs RHUM + WIND on top of the DSSAT/APSIM four). When
    ``need_elev`` is set, elevation at each point is also returned (indexed
    like ``df``) for the writers whose weather header carries it (DSSAT,
    ORYZA); it is best-effort — a fetch failure logs a warning and yields no
    elevation rather than blocking the file generation.
    """
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)
    sourcing_statics = soil is None  # user is letting us pull from the layer
    if weather is None:
        weather = get_season(
            weather_vars or _CM_WEATHER_VARS, planting_date=planting_date,
            harvest_date=harvest_date, points=df,
            planting_col=planting_col, harvest_col=harvest_col,
            lon_col=lon_col, lat_col=lat_col, source=weather_source, config=config,
        )
    if soil is None:
        soil = extract_static_points(
            df, _CM_SOIL_VARS, lon_col=lon_col, lat_col=lat_col,
            source=soil_source, config=config,
        )
    # Elevation enriches the DSSAT/ORYZA weather header. Only fetch it when we
    # are already sourcing point statics from the layer (soil not supplied) —
    # a caller who injected their own soil is in offline/reuse mode and should
    # not trigger a surprise DEM fetch. Best-effort either way.
    elev = None
    if need_elev and sourcing_statics:
        try:
            elev = extract_static_points(
                df, ["ELEV"], lon_col=lon_col, lat_col=lat_col, config=config,
            )["ELEV"]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch elevation for the weather header (%s) — "
                "writing it as missing", exc,
            )
    return df, lon_col, lat_col, weather, soil, elev


def _point_elev(elev, idx):
    """One point's elevation (float) from the ``_cm_inputs`` elevation series."""
    if elev is None:
        return None
    try:
        val = float(elev.loc[idx])
    except (KeyError, TypeError, ValueError):
        return None
    return None if pd.isna(val) else val


def _point_weather_wide(weather_long: pd.DataFrame, point_id) -> pd.DataFrame:
    """One point's long weather rows -> a wide daily DATE/TMAX/TMIN/SRAD/PRCP frame."""
    grp = weather_long[weather_long["point"] == point_id]
    if grp.empty:
        return grp
    wide = grp.pivot_table(index="time", columns="variable", values="value")
    wide = wide.reset_index().rename(columns={"time": "DATE"})
    wide.columns.name = None
    return wide


def to_dssat(
    points,
    planting_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    out_dir=None,
    planting_col: Optional[str] = None,
    harvest_col: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    id_col: Optional[str] = None,
    station_col: Optional[str] = None,
    country: str = "-99",
    weather: Optional[pd.DataFrame] = None,
    soil: Optional[pd.DataFrame] = None,
    weather_source: Optional[str] = None,
    soil_source: Optional[str] = None,
    calcareous: bool = False,
    config: Optional[Config] = None,
) -> list:
    """Write DSSAT weather + soil files for every point (retires readGeo_CM).

    For each row of ``points`` (a CSV/DataFrame with lon/lat), writes
    ``<out_dir>/EXTE<n>/WHTE<n>.WTH`` and ``<out_dir>/EXTE<n>/SOIL.SOL``.
    Weather is the season slice ``[planting_date, harvest_date]`` (or per-row
    ``planting_col``/``harvest_col``); soil is SoilGrids at the point plus the
    Saxton-Rawls hydraulics. Pass ``weather``/``soil`` to reuse frames you have
    already extracted instead of re-fetching. Returns a list of
    ``{"point", "dir", "wth", "sol"}`` for the files written.

    If the ``soil`` frame carries Mehlich-3 ``EXTP_<depth>`` columns (e.g. from
    ``extract_static_points(..., ["EXTP"], source="isda")``), the DSSAT P block
    (``SLPX`` = Olsen P) is written too; ``calcareous`` picks the calcareous
    Mehlich-3->Olsen regression. Otherwise the P block is omitted.
    """
    from .writers import dssat as dssat_w
    from .writers import soil as soil_w
    from .writers._common import station_code

    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "DSSAT"
    df, lon_col, lat_col, weather, soil, elev = _cm_inputs(
        points, planting_date, harvest_date, planting_col, harvest_col,
        lon_col, lat_col, weather, soil, weather_source, soil_source, config,
        need_elev=True,
    )

    written = []
    for n, (idx, prow) in enumerate(
        progress.track(df.iterrows(), total=len(df), desc="Writing crop-model files"),
        start=1,
    ):
        wide = _point_weather_wide(weather, idx)
        if wide.empty:
            logger.warning("Point %s has no weather in season; skipped", idx)
            continue
        name = str(prow[station_col]) if station_col else (
            str(prow[id_col]) if id_col else f"P{n:04d}"
        )
        insi = station_code(name)
        d = out_dir / f"EXTE{n:04d}"
        wth = dssat_w.write_wth(
            wide, lat=float(prow[lat_col]), lon=float(prow[lon_col]),
            path=d / f"WHTE{n:04d}.WTH", station=name,
            elev=_point_elev(elev, idx),
        )
        sol = soil_w.write_sol(
            soil.loc[idx], lat=float(prow[lat_col]), lon=float(prow[lon_col]),
            path=d / "SOIL.SOL", pedon=f"{insi}{n:05d}", site=name, country=country,
            calcareous=calcareous,
        )
        written.append({"point": idx, "dir": d, "wth": wth, "sol": sol})
    return written


def to_apsim(
    points,
    planting_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    out_dir=None,
    planting_col: Optional[str] = None,
    harvest_col: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    id_col: Optional[str] = None,
    station_col: Optional[str] = None,
    weather: Optional[pd.DataFrame] = None,
    soil: Optional[pd.DataFrame] = None,
    weather_source: Optional[str] = None,
    soil_source: Optional[str] = None,
    config: Optional[Config] = None,
) -> list:
    """Write APSIM weather (.met) + soil-layer table for every point.

    For each row of ``points`` writes ``<out_dir>/EXTE<n>/wth_loc_<n>.met`` and
    ``<out_dir>/EXTE<n>/soil_<n>.csv`` (the per-layer LL15/DUL/SAT/AirDry/KS/
    BD/Carbon/clay/silt/N/PH/CEC table, with Salb/CN2Bare in a header comment)
    — the values ``01_readGeo_CM_zone_APSIM.R`` injects into its apsimx soil
    template. Same weather/soil sourcing and reuse options as :func:`to_dssat`.
    Returns a list of ``{"point", "dir", "met", "soil"}``.
    """
    from .writers import apsim as apsim_w
    from .writers import soil as soil_w

    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "APSIM"
    df, lon_col, lat_col, weather, soil, _elev = _cm_inputs(
        points, planting_date, harvest_date, planting_col, harvest_col,
        lon_col, lat_col, weather, soil, weather_source, soil_source, config,
    )

    written = []
    for n, (idx, prow) in enumerate(
        progress.track(df.iterrows(), total=len(df), desc="Writing crop-model files"),
        start=1,
    ):
        wide = _point_weather_wide(weather, idx)
        if wide.empty:
            logger.warning("Point %s has no weather in season; skipped", idx)
            continue
        name = str(prow[station_col]) if station_col else (
            str(prow[id_col]) if id_col else f"P{n:04d}"
        )
        d = out_dir / f"EXTE{n:04d}"
        met = apsim_w.write_met(
            wide, lat=float(prow[lat_col]), lon=float(prow[lon_col]),
            path=d / f"wth_loc_{n}.met", site=name,
        )
        table = soil_w.apsim_soil_table(soil.loc[idx])
        soil_csv = d / f"soil_{n}.csv"
        soil_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(soil_csv, "w") as fh:
            fh.write(
                f"# Salb={table.attrs['Salb']} CN2Bare={table.attrs['CN2Bare']} "
                f"texture={table.attrs['texture']}\n"
            )
            table.to_csv(fh, index=False)
        written.append({"point": idx, "dir": d, "met": met, "soil": soil_csv})
    return written


def to_wofost(
    points,
    planting_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    out_dir=None,
    planting_col: Optional[str] = None,
    harvest_col: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    id_col: Optional[str] = None,
    station_col: Optional[str] = None,
    weather: Optional[pd.DataFrame] = None,
    soil: Optional[pd.DataFrame] = None,
    weather_source: Optional[str] = None,
    soil_source: Optional[str] = None,
    config: Optional[Config] = None,
) -> list:
    """Write WOFOST weather + soil-parameter CSVs for every point.

    For each row of ``points`` writes ``<out_dir>/EXTE<n>/weather_<n>.csv`` (the
    WOFOST columns ``date, srad, tmin, tmax, vapr, wind, prec`` with SRAD in
    kJ m-2 day-1 and ``vapr`` the actual vapour pressure in kPa) and
    ``<out_dir>/EXTE<n>/soil_<n>.csv`` (SMW/SMFCF/SM0/K0 from the Saxton-Rawls
    hydraulics averaged over the top metre, plus the WOFOST soil defaults). WOFOST
    reads its weather/soil as R lists, so these tidy CSVs are the deliverable —
    they retire ``WOFOST/grid/5a_prepare_list_weather.r`` + ``5c_prepare_list_soil.r``.
    Weather is sourced with RHUM + WIND on top of the crop-model four. Same
    per-row/scalar season and reuse options as :func:`to_dssat`. Returns a list
    of ``{"point", "dir", "weather", "soil"}``.
    """
    from .writers import wofost as wofost_w

    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "WOFOST"
    df, lon_col, lat_col, weather, soil, _elev = _cm_inputs(
        points, planting_date, harvest_date, planting_col, harvest_col,
        lon_col, lat_col, weather, soil, weather_source, soil_source, config,
        weather_vars=_WOFOST_WEATHER_VARS,
    )

    written = []
    for n, (idx, prow) in enumerate(
        progress.track(df.iterrows(), total=len(df), desc="Writing crop-model files"),
        start=1,
    ):
        wide = _point_weather_wide(weather, idx)
        if wide.empty:
            logger.warning("Point %s has no weather in season; skipped", idx)
            continue
        d = out_dir / f"EXTE{n:04d}"
        wth = wofost_w.write_weather(wide, path=d / f"weather_{n}.csv")
        sol = wofost_w.write_soil(soil.loc[idx], path=d / f"soil_{n}.csv")
        written.append({"point": idx, "dir": d, "weather": wth, "soil": sol})
    return written


def to_oryza(
    points,
    planting_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    out_dir=None,
    planting_col: Optional[str] = None,
    harvest_col: Optional[str] = None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    id_col: Optional[str] = None,
    station_col: Optional[str] = None,
    weather: Optional[pd.DataFrame] = None,
    soil: Optional[pd.DataFrame] = None,
    weather_source: Optional[str] = None,
    soil_source: Optional[str] = None,
    config: Optional[Config] = None,
) -> list:
    """Write ORYZA v3 weather + PADDY soil files for every point.

    For each row of ``points`` writes, under ``<out_dir>/EXTE<n>/``, the CABO
    weather files ``<code><n>.<yyy>`` (one per calendar year the season spans —
    columns ``station, year, day, srad[kJ], tmin, tmax, vapr[kPa], wind, rain``)
    and the 8-layer PADDY ``soil_<n>.sol`` (SoilGrids remapped to ORYZA's fixed
    layers with the Saxton-Rawls hydraulics). Retires ``Oryza/OryzaDataFiles.R``.
    Sources relative humidity + wind on top of the crop-model four. Same per-row/
    scalar season and reuse options as :func:`to_dssat`. Returns a list of
    ``{"point", "dir", "weather": [paths], "soil"}``.
    """
    from .writers import oryza as oryza_w
    from .writers._common import station_code

    config = config or Config.load()
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "ORYZA"
    df, lon_col, lat_col, weather, soil, elev = _cm_inputs(
        points, planting_date, harvest_date, planting_col, harvest_col,
        lon_col, lat_col, weather, soil, weather_source, soil_source, config,
        weather_vars=_WOFOST_WEATHER_VARS, need_elev=True,
    )

    written = []
    for n, (idx, prow) in enumerate(
        progress.track(df.iterrows(), total=len(df), desc="Writing crop-model files"),
        start=1,
    ):
        wide = _point_weather_wide(weather, idx)
        if wide.empty:
            logger.warning("Point %s has no weather in season; skipped", idx)
            continue
        name = str(prow[station_col]) if station_col else (
            str(prow[id_col]) if id_col else f"P{n:04d}"
        )
        d = out_dir / f"EXTE{n:04d}"
        wth = oryza_w.write_weather(
            wide, lat=float(prow[lat_col]), lon=float(prow[lon_col]),
            out_dir=d, id_name=name, stn=n, elev=_point_elev(elev, idx) or 0.0,
        )
        sol = oryza_w.write_soil(
            soil.loc[idx], path=d / f"soil_{n}.sol", id_name=station_code(name),
        )
        written.append({"point": idx, "dir": d, "weather": wth, "soil": sol})
    return written


# ---------------------------------------------------------------------------
# Seasonal-forecast bias correction (scope-map #3): QDM the raw SEAS5 forecast
# against the hindcast-vs-observation bias, producing analysis-ready fields.
# Both input halves already exist (get_seasonal + get_climate); this adds the
# correction. The QDM maths live in forecast.py.
def bias_correct(
    variables: Union[str, Sequence[str]],
    init_month: int,
    forecast_year: int,
    calib_years: Sequence[int],
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    window_days: Optional[int] = None,
    obs: Optional[dict] = None,
    hind: Optional[dict] = None,
    fcst: Optional[dict] = None,
    source: Optional[str] = None,
    out_format: Union[str, Sequence[str]] = "nc",
    out_dir=None,
    overwrite: bool = False,
    config: Optional[Config] = None,
) -> Dict[str, dict]:
    """Bias-correct a SEAS5 seasonal forecast (QDM) — scope-map #3.

    Learns the model bias from the **hindcast vs observations** over
    ``calib_years`` and applies Quantile Delta Mapping to the ``forecast_year``
    forecast (init month ``init_month``), per variable (additive for
    temperatures, multiplicative for PRCP/SRAD). Returns
    ``{canonical_variable: {"short", "kind", "nc", "data"}}`` with the
    corrected cube ``(member, time, lat, lon)`` on the observation grid, written
    as ``Seasonal_<SHORT>_i<MM>_<fy>_BC``.

    Fetches the three inputs itself (``get_climate`` for obs, ``get_seasonal``
    for hindcast + forecast) unless ``obs``/``hind``/``fcst`` dicts (keyed by
    canonical variable) are supplied — the latter is how it is tested offline.
    ``window_days`` restricts QDM calibration to +/- that many days-of-year of
    each step (``None`` pools the whole season).
    """
    from . import forecast as _fc

    config = config or Config.load()
    variables = _as_variables(variables)
    calib_years = _as_years(calib_years)
    formats = [out_format] if isinstance(out_format, str) else list(out_format)
    for f in formats:
        if f not in ("nc",):
            raise ValueError("bias_correct writes NetCDF only (ensemble cube)")

    _, _, tag = _resolve_region(
        config, country, bbox, admin_level, admin_name, geometry
    )
    out_root = Path(out_dir) if out_dir else config.products_dir(tag)
    region = dict(country=country, bbox=bbox, admin_level=admin_level,
                  admin_name=admin_name, geometry=geometry, config=config)

    results: Dict[str, dict] = {}
    for var in variables:
        short = short_name(var)
        if short not in _fc.DEFAULT_KIND:
            raise ValueError(
                f"No bias-correction transform defined for {var} "
                f"(known: {sorted(_fc.DEFAULT_KIND)})"
            )
        kind = _fc.DEFAULT_KIND[short]
        obs_da = (obs or {}).get(var)
        if obs_da is None:
            obs_da = get_climate(var, calib_years, freq="daily", source=source,
                                 **region)[var]["data"]
        hind_da = (hind or {}).get(var)
        if hind_da is None:
            hind_da = get_seasonal(var, init_month, calib_years,
                                   ensemble="members", source=source,
                                   **region)[var]["data"]
        fcst_da = (fcst or {}).get(var)
        if fcst_da is None:
            fcst_da = get_seasonal(var, init_month, forecast_year,
                                   ensemble="members", source=source,
                                   **region)[var]["data"]

        corrected = _fc.bias_correct_cube(obs_da, hind_da, fcst_da, kind,
                                          window_days).load()
        corrected.name = corrected.name or short
        stem = f"Seasonal_{short}_i{init_month:02d}_{forecast_year}_BC"
        nc_path = out_root / f"{stem}.nc"
        if overwrite or not nc_path.exists():
            from .drivers.seasonal import seasonal_nc_encoding

            _write_nc_product(
                corrected, nc_path,
                {corrected.name: seasonal_nc_encoding(corrected)},
            )
            write_manifest(nc_path, {
                "variable": var, "region": tag, "method": f"qdm-{kind}",
                "init_month": init_month, "forecast_year": forecast_year,
                "calib_years": [calib_years[0], calib_years[-1]],
                "window_days": window_days,
            })
        results[var] = {
            "short": short, "kind": kind,
            "nc": nc_path if nc_path.exists() else None, "data": corrected,
        }
    return results


_FORECAST_WEATHER_VARS = ["PRCP", "TMAX", "TMIN", "SRAD"]


def forecast_to_dssat(
    points,
    init_month: int,
    forecast_year: int,
    calib_years: Sequence[int],
    out_dir=None,
    ensemble: str = "mean",
    window_days: Optional[int] = None,
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    id_col: Optional[str] = None,
    station_col: Optional[str] = None,
    country_name: str = "-99",
    corrected: Optional[dict] = None,
    soil: Optional[pd.DataFrame] = None,
    soil_source: Optional[str] = None,
    weather_source: Optional[str] = None,
    config: Optional[Config] = None,
) -> list:
    """Bias-corrected seasonal forecast -> DSSAT weather+soil files (#3b).

    Chains :func:`bias_correct` (QDM the PRCP/TMAX/TMIN/SRAD forecast) into the
    :func:`to_dssat` writer: samples the corrected cube at each point, reduces
    the ensemble (``"mean"``/``"median"``, as the reference's single-series
    output does), and writes ``EXTE<n>/WHTE<n>.WTH`` + ``SOIL.SOL``. The
    weather is the forecast season; soil comes from ``extract_static_points``
    (or a provided ``soil`` frame). Pass ``corrected`` (the
    :func:`bias_correct` result) to skip the QDM step — the offline-test path.
    Returns the :func:`to_dssat` manifest.
    """
    config = config or Config.load()
    if ensemble not in ("mean", "median"):
        raise ValueError("ensemble must be 'mean' or 'median'")
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)

    if corrected is None:
        corrected = bias_correct(
            _FORECAST_WEATHER_VARS, init_month, forecast_year, calib_years,
            country=country, bbox=bbox, admin_level=admin_level,
            admin_name=admin_name, geometry=geometry, window_days=window_days,
            source=weather_source, config=config,
        )

    lons = df[lon_col].to_numpy(dtype=float)
    lats = df[lat_col].to_numpy(dtype=float)
    frames = []
    for var, info in corrected.items():
        cube = info["data"] if isinstance(info, dict) else info
        short = cube.name or short_name(var)
        if "member" in cube.dims:
            cube = cube.mean("member") if ensemble == "mean" else cube.median("member")
        series = _point_series(cube, lons, lats).load()  # (time, point)
        long = series.to_pandas()
        long.columns = df.index
        long = long.reset_index().melt(
            id_vars="time", var_name="point", value_name="value"
        )
        long["variable"] = short
        frames.append(long)
    weather = pd.concat(frames, ignore_index=True)

    return to_dssat(
        df, out_dir=out_dir, weather=weather, soil=soil, soil_source=soil_source,
        lon_col=lon_col, lat_col=lat_col, id_col=id_col, station_col=station_col,
        country=country_name, config=config,
    )


# ---------------------------------------------------------------------------
# Spatial scaffolding (scope-map P1): the AOI point-grid generator and the
# field<->geospatial admin-name linker that every module re-implements
# (~105 copies of get_GridCoordinates / extract_geoSpatialPointData). Both are
# thin wrappers over the boundaries the layer already caches (geoBoundaries).
_KM_PER_DEG_LAT = 111.32


def _grid_points(bbox, res_km: float):
    """Regular lon/lat grid over a bbox at ~res_km spacing (cos-lat scaled)."""
    w, s, e, n = bbox
    if res_km <= 0:
        raise ValueError("res_km must be > 0")
    mean_lat = (s + n) / 2.0
    dlat = res_km / _KM_PER_DEG_LAT
    dlon = res_km / (_KM_PER_DEG_LAT * max(np.cos(np.radians(mean_lat)), 1e-6))
    lats = np.arange(s, n + 1e-9, dlat)
    lons = np.arange(w, e + 1e-9, dlon)
    glon, glat = np.meshgrid(lons, lats)
    return glon.ravel(), glat.ravel()


def _admin_names_for_points(config, country, lons, lats, max_level: int):
    """point-in-polygon admin names per point: {'NAME_1': [...], 'NAME_2': [...]}.

    Each level is an independent geoBoundaries lookup (ADM1 -> NAME_1,
    ADM2 -> NAME_2); a missing/unavailable level yields an all-None column
    with a warning rather than failing the whole call.
    """
    import geopandas as gpd

    pts = gpd.GeoDataFrame(
        {"_i": np.arange(len(lons))},
        geometry=gpd.points_from_xy(lons, lats),
        crs="EPSG:4326",
    )
    out: Dict[str, np.ndarray] = {}
    for lvl in range(1, max_level + 1):
        col = f"NAME_{lvl}"
        try:
            gdf = boundaries.load_geometry(config, country, level=lvl)
        except Exception as exc:  # missing level, network, no shapeName
            warnings.warn(f"Could not load ADM{lvl} for {country} ({exc}); "
                          f"{col} left empty.")
            out[col] = np.array([None] * len(lons), dtype=object)
            continue
        right = gdf[["shapeName", "geometry"]].rename(columns={"shapeName": col})
        joined = gpd.sjoin(pts, right, how="left", predicate="within")
        # overlapping polygons can duplicate a point; keep its first match
        joined = joined[~joined["_i"].duplicated(keep="first")].sort_values("_i")
        out[col] = joined[col].to_numpy()
    return out


def make_grid(
    country: Optional[str] = None,
    bbox: Optional[Sequence[float]] = None,
    admin_level: int = 0,
    admin_name: Optional[str] = None,
    geometry=None,
    res_km: float = 5.0,
    tag_admin_level: int = 2,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Regular point grid clipped to a country/admin boundary (or a bbox).

    Replaces the per-module ``get_GridCoordinates`` / ``getCoordinates``: builds
    a ~``res_km`` grid (default 5 km; use 1.0 or 0.25 for finer AOIs), keeps the
    points inside the requested geometry, and tags each with its admin unit
    names. Returns a DataFrame with ``lon``, ``lat``, ``country`` and (when a
    country is given) ``NAME_1``/``NAME_2`` up to ``tag_admin_level``. With
    ``bbox`` only, returns the full rectangular grid (no clip, no admin tags).
    """
    config = config or Config.load()
    geom = None
    if geometry is not None:
        gdf = boundaries.load_aoi(geometry)
        region_bbox = boundaries.geometry_bbox(gdf)
        geom = gdf.geometry.union_all() if hasattr(gdf.geometry, "union_all") \
            else gdf.geometry.unary_union
    elif country:
        gdf = boundaries.load_geometry(config, country, admin_level, admin_name)
        region_bbox = boundaries.geometry_bbox(gdf)
        geom = gdf.geometry.union_all() if hasattr(gdf.geometry, "union_all") \
            else gdf.geometry.unary_union
    elif bbox is not None:
        region_bbox = tuple(float(v) for v in bbox)
        if len(region_bbox) != 4:
            raise ValueError("bbox must be (west, south, east, north)")
    else:
        raise ValueError("Provide geometry=..., country=... or bbox=(w, s, e, n)")

    lons, lats = _grid_points(region_bbox, res_km)
    if geom is not None:
        import geopandas as gpd

        pts = gpd.GeoSeries(gpd.points_from_xy(lons, lats), crs="EPSG:4326")
        inside = pts.within(geom).to_numpy()
        lons, lats = lons[inside], lats[inside]
    if len(lons) == 0:
        raise ValueError("Grid is empty (boundary smaller than res_km?)")

    out = pd.DataFrame({"lon": lons, "lat": lats})
    if country:
        out.insert(0, "country", boundaries.iso3(country))
        if tag_admin_level >= 1:
            names = _admin_names_for_points(
                config, country, lons, lats, tag_admin_level
            )
            for col, vals in names.items():
                out[col] = vals
    return out


def tag_admin(
    points,
    country: str,
    admin_level: int = 2,
    lon_col: Optional[str] = None,
    lat_col: Optional[str] = None,
    config: Optional[Config] = None,
) -> pd.DataFrame:
    """Assign admin unit names to points (the field<->geospatial link).

    The reusable half of the modules' ``extract_geoSpatialPointData``: given
    trial/point coordinates, tag each with ``country`` and ``NAME_1`` (and
    ``NAME_2`` when ``admin_level >= 2``) via point-in-polygon against
    geoBoundaries. Returns the input frame with those columns added.
    """
    config = config or Config.load()
    df, lon_col, lat_col = _read_points(points, lon_col, lat_col)
    lons = df[lon_col].to_numpy(dtype=float)
    lats = df[lat_col].to_numpy(dtype=float)
    names = _admin_names_for_points(config, country, lons, lats, admin_level)
    out = df.copy()
    out["country"] = boundaries.iso3(country)
    for col, vals in names.items():
        out[col] = vals
    return out


__all__ = [
    "get_climate",
    "extract_points",
    "extract_growing_season",
    "rainy_days",
    "get_static",
    "get_dem",
    "get_soil",
    "get_cropmask",
    "get_seasonal",
    "get_modis",
    "get_ndvi",
    "get_season",
    "extract_static_points",
    "to_dssat",
    "to_apsim",
    "to_wofost",
    "to_oryza",
    "make_grid",
    "tag_admin",
    "bias_correct",
    "forecast_to_dssat",
]
