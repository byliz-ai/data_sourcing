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
