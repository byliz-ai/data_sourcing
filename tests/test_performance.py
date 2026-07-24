"""Tests for the performance machinery: segmented downloads, region-scoped
caching, parallel prefetch and storage encoding. All network-free."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data.api import _effective_domain, get_climate
from agwise_data.cache import _split_ranges
from agwise_data.config import (
    Config,
    parse_region_name,
    region_domain_name,
    round_region_bbox,
)
from agwise_data.drivers.base import nc_encoding
from agwise_data.harmonize import short_name

BBOX = (33.0, -2.0, 40.0, 2.0)


# ---------------------------------------------------------------------------
def test_split_ranges_covers_everything():
    bounds = _split_ranges(10, 3)
    assert bounds == [(0, 2), (3, 5), (6, 9)]
    assert bounds[0][0] == 0 and bounds[-1][1] == 9
    total = sum(b - a + 1 for a, b in bounds)
    assert total == 10
    # more parts than bytes
    assert _split_ranges(2, 8) == [(0, 0), (1, 1)]
    assert _split_ranges(100, 1) == [(0, 99)]


def test_region_bbox_rounding_and_name_roundtrip():
    rbox = round_region_bbox((28.86, -2.84, 30.9, -1.05))
    assert rbox == [28.0, -4.0, 32.0, 0.0]  # padded 0.5 then whole degrees
    name = region_domain_name(rbox)
    assert name == "rg_28_m4_32_0"
    assert parse_region_name(name) == rbox
    assert parse_region_name("africa") is None
    # clipping at the antimeridian/poles
    assert round_region_bbox((-179.9, -89.9, 179.9, 89.9)) == [-180.0, -90.0, 180.0, 90.0]


def test_effective_domain_routing(config):
    years = [2020]
    # small request, nothing cached -> region-scoped domain, registered
    dom = _effective_domain(config, "fake", "PRCP", years, BBOX, None)
    assert dom.startswith("rg_")
    assert dom in config.domains
    # explicit override always wins
    assert (
        _effective_domain(config, "fake", "PRCP", years, BBOX, "africa") == "africa"
    )
    # domain scope forces the containing domain
    config.fetch_scope = "domain"
    assert _effective_domain(config, "fake", "PRCP", years, BBOX, None) == "africa"
    config.fetch_scope = "auto"
    # a complete cache at the containing domain is reused for free
    short = short_name("PRCP")
    base_file = config.harmonized_path("fake", "africa", short, 2020)
    base_file.parent.mkdir(parents=True, exist_ok=True)
    base_file.write_bytes(b"x")
    assert _effective_domain(config, "fake", "PRCP", years, BBOX, None) == "africa"


def test_region_domains_rediscovered_across_sessions(config):
    get_climate(
        variables="PRCP", years=[2020], bbox=BBOX, freq="daily",
        source="fake", config=config,
    )
    rg = [d for d in config.domains if d.startswith("rg_")]
    assert rg
    # a fresh Config (new session) over the same root re-registers it
    fresh = Config(root=config.root)
    assert rg[0] in fresh.domains


def test_get_climate_parallel_prefetch(config):
    from tests.conftest import fake_calls

    res = get_climate(
        variables=["PRCP", "TMAX"],
        years=[2020, 2021],
        bbox=BBOX,
        freq="monthly",
        source="fake",
        config=config,
    )
    # each (variable, year) fetched exactly once despite the thread pool
    calls = fake_calls()
    assert len(calls) == len(set(calls)) == 4
    assert res["AGRO.PRCP"]["data"].sizes["time"] == 24
    assert res["AGRO.TMAX"]["data"].sizes["time"] == 24
    # values still correct under parallel fetch (Jan 2020 sum of doys)
    jan = float(res["AGRO.PRCP"]["data"].sel(time="2020-01-01").isel(lat=0, lon=0))
    assert jan == pytest.approx(sum(range(1, 32)))


def test_incomplete_past_year_is_rejected(config):
    """A driver returning a truncated past year (e.g. because the server
    started rate-limiting mid-run) must NOT poison the shared cache."""
    from agwise_data import catalog
    from agwise_data.drivers import get_driver, register
    from agwise_data.drivers.base import Driver
    from tests.conftest import synthetic_year

    @register("fake_truncated")
    class TruncatedDriver(Driver):
        def _fetch_year(self, variable, year, domain):
            da = synthetic_year(year).isel(time=slice(0, 107))  # cut short
            return da, {}

    entry = {
        "id": "fake_truncated",
        "driver": "fake_truncated",
        "version": "0",
        "access": [],
        "variables": {"AGRO.PRCP": {"source_name": "precip"}},
    }
    catalog.register_entry(entry)
    driver = get_driver(entry, config)
    with pytest.raises(RuntimeError, match="incomplete past year"):
        driver.ensure_daily_year("PRCP", 2020, "africa")
    # nothing half-written landed in the cache
    assert not config.harmonized_path("fake_truncated", "africa", "PRCP", 2020).exists()


def test_nc_encoding_chunks_capped_to_dims():
    times = pd.date_range("2020-01-01", periods=10, freq="D")
    da = xr.DataArray(
        np.zeros((10, 5, 7), dtype="float32"),
        coords={"time": times, "lat": np.arange(5.0), "lon": np.arange(7.0)},
        dims=("time", "lat", "lon"),
        name="PRCP",
    )
    enc = nc_encoding(da)
    assert enc["chunksizes"] == (10, 5, 7)
    assert enc["complevel"] == 1 and enc["zlib"] is True


def test_open_years_concats_time_not_lon_on_grid_epsilon(config):
    """Two years whose grids differ by float noise (as when one year is read
    locally and another downloaded) must concatenate along time, not lon."""
    import os

    from agwise_data import catalog, drivers

    drv = drivers.get_driver(catalog.get_entry("fake"), config)
    p1 = drv.ensure_daily_year("AGRO.PRCP", 2020, "africa")
    p2 = drv.ensure_daily_year("AGRO.PRCP", 2021, "africa")

    # Nudge 2021's lon by ~1e-14, mimicking a different fetch path's rounding.
    with xr.open_dataset(p2) as ds:
        ds = ds.load()
    lon0 = int(ds.sizes["lon"])
    ds = ds.assign_coords(lon=ds["lon"].values + 1e-14)
    tmp = p2.with_suffix(".perturbed.nc")
    ds.to_netcdf(tmp)
    os.replace(tmp, p2)

    da = drv.open_years("AGRO.PRCP", [2020, 2021], "africa").load()
    assert da.sizes["lon"] == lon0                      # not doubled
    assert da.sizes["time"] == 366 + 365                # 2020 leap + 2021
    tt = da["time"].values.astype("datetime64[ns]").astype("int64")
    assert bool((np.diff(tt) > 0).all())                # time strictly ascending


def test_write_nc_product_is_atomic_on_failure(tmp_path):
    """A failed product write leaves no file (and no temp), so it can't poison
    a later cache-hit — the failure mode seen in the forecast QA run."""
    import pytest

    from agwise_data.api import _write_nc_product

    da = xr.DataArray(np.arange(3.0), dims="x", name="v")
    p = tmp_path / "prod.nc"
    with pytest.raises(Exception):
        _write_nc_product(da, p, {"v": {"dtype": "not-a-real-dtype"}})
    assert not p.exists()                                   # nothing left behind
    assert not list(tmp_path.glob(".prod.nc*.tmp"))         # no orphan temp


def test_prefetch_pins_cog_workers_when_parallel(config):
    """A parallel prefetch batch pins the inner fan-out (cog_workers) to 1 to
    break the max_workers x cog_workers multiplier, and restores it after."""
    from agwise_data import api

    config.max_workers = 4
    config.cog_workers = 8
    seen = []

    class _Rec:
        def ensure_daily_year(self, var, year, dom):
            seen.append(config.cog_workers)

    tasks = [(_Rec(), "AGRO.PRCP", y, "africa") for y in range(2015, 2019)]
    api._prefetch(config, tasks)
    assert seen and all(v == 1 for v in seen)   # pinned during the parallel batch
    assert config.cog_workers == 8              # restored afterwards


def test_prefetch_serial_keeps_full_cog_workers(config):
    """A single-task (serial) prefetch keeps the full inner fan-out."""
    from agwise_data import api

    config.max_workers = 4
    config.cog_workers = 8
    seen = []

    class _Rec:
        def ensure_daily_year(self, var, year, dom):
            seen.append(config.cog_workers)

    api._prefetch(config, [(_Rec(), "AGRO.PRCP", 2015, "africa")])
    assert seen == [8]


def test_pinned_cog_workers_restores_on_error(config):
    from agwise_data.api import _pinned_cog_workers

    config.cog_workers = 8
    try:
        with _pinned_cog_workers(config, 1):
            assert config.cog_workers == 1
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert config.cog_workers == 8


# ---------------------------------------------------------------------------
# Parallel local reads via a PROCESS pool (works around xarray's global HDF5
# lock, which serializes netCDF reads/writes within one process).


def test_prefetch_routes_between_threads_and_processes(config, monkeypatch):
    """A big batch goes to the process pool; a small one stays on threads, and
    read_workers < 2 disables the process path entirely."""
    import agwise_data.api as api

    proc_calls = []
    monkeypatch.setattr(
        api, "_prefetch_processes",
        lambda cfg, tasks: proc_calls.append(len(tasks)),
    )

    class _Drv:
        entry = {}
        config = None

        def __init__(self):
            self.n = 0

        def ensure_daily_year(self, variable, year, domain):
            self.n += 1

    config.max_workers = 4
    config.read_workers = 4
    drv = _Drv()

    few = [(drv, "PRCP", y, "africa") for y in range(3)]
    api._prefetch(config, few)
    assert proc_calls == [] and drv.n == 3            # few -> threads

    drv.n = 0
    many = [(drv, "PRCP", y, "africa") for y in range(api._PROCESS_MIN_TASKS)]
    api._prefetch(config, many)
    assert proc_calls == [api._PROCESS_MIN_TASKS] and drv.n == 0   # many -> processes

    proc_calls.clear()
    config.read_workers = 1
    drv.n = 0
    api._prefetch(config, many)
    assert proc_calls == [] and drv.n == api._PROCESS_MIN_TASKS     # disabled -> threads


def _write_agera5_tmax_year(landing, year):
    p = landing / "TemperatureMax" / "AgEra"
    p.mkdir(parents=True, exist_ok=True)
    times = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    lat = np.arange(-2.0, 2.001, 1.0)
    lon = np.arange(29.0, 33.001, 1.0)
    data = np.full((len(times), len(lat), len(lon)), 298.0)  # 298 K -> 24.85 C
    ds = xr.Dataset(
        {str(year): (("time", "latitude", "longitude"), data)},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    ds["crs"] = 0
    ds.to_netcdf(p / f"{year}.nc")


def test_prefetch_processes_fills_cache_from_local(tmp_path, config):
    """The real process pool (forkserver/spawn) reads local files in parallel
    and populates the shared cache — the multi-year historical read path."""
    import agwise_data.api as api
    from agwise_data import catalog, drivers
    from agwise_data.harmonize import short_name

    landing = tmp_path / "landing"
    years = list(range(2000, 2000 + api._PROCESS_MIN_TASKS))
    for y in years:
        _write_agera5_tmax_year(landing, y)

    config.local_root = landing
    config.register_domain("rw", [28.0, -3.0, 34.0, 3.0])
    config.read_workers = 3
    drv = drivers.get_driver(catalog.get_entry("agera5"), config)
    tasks = [(drv, "AGRO.TMAX", y, "rw") for y in years]

    api._prefetch_processes(config, tasks)

    for y in years:
        dest = config.harmonized_path("agera5", "rw", short_name("AGRO.TMAX"), y)
        assert dest.exists(), f"missing cache for {y}"
    # harmonization ran inside the worker: 298 K -> ~24.85 C, full year kept
    import calendar

    with xr.open_dataarray(
        config.harmonized_path("agera5", "rw", short_name("AGRO.TMAX"), years[0])
    ) as da:
        assert da.sizes["time"] == (366 if calendar.isleap(years[0]) else 365)
        assert abs(float(da.max()) - 24.85) < 0.1
