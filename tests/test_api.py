"""End-to-end tests over the fake driver (no network, no credentials)."""

import numpy as np
import pandas as pd
import pytest

from agwise_data.api import extract_growing_season, extract_points, get_climate
from agwise_data.cache import read_manifest

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain


def test_get_climate_monthly_bbox(config):
    res = get_climate(
        variables="PRCP",
        years=[2020, 2021],
        bbox=BBOX,
        freq="monthly",
        source="fake",
        config=config,
    )
    info = res["AGRO.PRCP"]
    assert info["nc"].exists()
    da = info["data"]
    assert da.sizes["time"] == 24
    # Jan 2020: synthetic value = dayofyear, so monthly sum = 1+2+...+31
    jan = float(da.sel(time="2020-01-01").isel(lat=0, lon=0))
    assert jan == pytest.approx(sum(range(1, 32)))
    # manifest sidecar exists and records provenance
    meta = read_manifest(info["nc"])
    assert meta["source_id"] == "fake"
    assert meta["freq"] == "monthly"


def test_get_climate_product_cache_hit(config):
    from tests.conftest import fake_calls

    kwargs = dict(
        variables="PRCP",
        years=[2020],
        bbox=BBOX,
        freq="monthly",
        source="fake",
        config=config,
    )
    get_climate(**kwargs)
    n_calls = len(fake_calls())
    res2 = get_climate(**kwargs)  # second call: no new fetches
    assert len(fake_calls()) == n_calls
    assert res2["AGRO.PRCP"]["nc"].exists()


def test_harmonized_year_reused_across_products(config):
    from tests.conftest import fake_calls

    get_climate(
        variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
        source="fake", config=config,
    )
    n_calls = len(fake_calls())
    # different product (daily, smaller bbox inside the first region) —
    # the containing region cache must be reused, not re-fetched
    get_climate(
        variables="PRCP", years=[2020], bbox=(34.0, -1.0, 36.0, 1.0),
        freq="daily", source="fake", config=config,
    )
    assert len(fake_calls()) == n_calls


def test_get_climate_unit_conversion_applied(config):
    res = get_climate(
        variables="TMAX", years=[2020], bbox=BBOX, freq="daily",
        source="fake", qc="off", config=config,
    )
    da = res["AGRO.TMAX"]["data"]
    # synthetic Kelvin-ish values are dayofyear; k_to_degc subtracts 273.15
    # (physically impossible in degC, hence qc="off")
    first = float(da.isel(time=0, lat=0, lon=0))
    assert first == pytest.approx(1 - 273.15)


def test_extract_points_long_format(config):
    pts = pd.DataFrame({"lon": [34.25, 36.0], "lat": [0.0, 1.0]})
    out = extract_points(
        pts, "PRCP", start="2020-01-01", end="2020-01-10",
        source="fake", config=config,
    )
    assert set(out.columns) == {"point", "lon", "lat", "time", "variable", "value"}
    assert len(out) == 2 * 10
    # synthetic value = dayofyear regardless of location
    day3 = out[(out["time"] == pd.Timestamp("2020-01-03"))]["value"]
    assert np.allclose(day3, 3.0)


def test_extract_points_monthly_midmonth_start_keeps_first_month(config):
    pts = pd.DataFrame({"lon": [34.25], "lat": [0.0]})
    out = extract_points(
        pts, "PRCP", start="2020-01-15", end="2020-03-31",
        freq="monthly", source="fake", config=config,
    )
    months = sorted(pd.DatetimeIndex(out["time"]).month.unique())
    assert months == [1, 2, 3]  # January not dropped despite mid-month start


def test_extract_growing_season_columns_and_values(config):
    pts = pd.DataFrame(
        {
            "X": [34.25, 36.0, 38.5],
            "Y": [0.0, 1.0, -1.0],
            "Pl_date": ["2020-11-15", "2020-03-01", "bad-date"],
            "Hv_date": ["2021-02-10", "2020-06-30", "2020-05-01"],
            "yield": [1.0, 2.0, 3.0],
        }
    )
    with pytest.warns(UserWarning, match="1/3 rows skipped"):
        out = extract_growing_season(
            pts,
            variables=["PRCP"],
            planting_col="Pl_date",
            harvest_col="Hv_date",
            source="fake",
            config=config,
        )
    # legacy column names by default
    assert "Precipitation_m1" in out.columns
    assert "totalRF" in out.columns and "nrRainyDays" in out.columns
    # row 0 spans Nov 2020 - Feb 2021 -> 4 months (cross-year window)
    assert "Precipitation_m4" in out.columns
    row0 = out.iloc[0]
    nov_sum = sum(range(306, 336))  # doy of Nov 1..30, 2020 (leap year)
    assert row0["Precipitation_m1"] == pytest.approx(nov_sum)
    # totalRF: daily doy values from 2020-11-15 to 2021-02-10 inclusive
    doys = list(range(320, 367)) + list(range(1, 42))
    assert row0["totalRF"] == pytest.approx(sum(doys))
    # synthetic value = dayofyear, so Jan 1 (value 1.0) is below the 2 mm threshold
    assert row0["nrRainyDays"] == sum(1 for d in doys if d >= 2)
    # invalid row: original data kept, climate columns NaN
    assert np.isnan(out.iloc[2]["Precipitation_m1"])
    assert out.iloc[2]["yield"] == 3.0


def test_extract_growing_season_agwise_names(config):
    pts = pd.DataFrame(
        {"lon": [34.0], "lat": [0.0], "pl": ["2020-01-01"], "hv": ["2020-03-31"]}
    )
    out = extract_growing_season(
        pts, "PRCP", planting_col="pl", harvest_col="hv",
        legacy_names=False, source="fake", config=config,
    )
    assert "PRCP_m1" in out.columns and "PRCP_m3" in out.columns


def test_open_product_da_ignores_crs_variable(tmp_path):
    """Regression: a country-clipped product carries a `spatial_ref` CRS var,
    so the NetCDF has >1 variable and xr.open_dataarray rejected it on the
    second (cache-hit) request. _open_product_da must return the data var."""
    import xarray as xr
    from agwise_data.api import _open_product_da

    da = xr.DataArray(
        np.zeros((2, 3, 3), "float32"),
        coords={"time": pd.date_range("2020-01-01", periods=2),
                "lat": [0.0, 0.1, 0.2], "lon": [0.0, 0.1, 0.2]},
        dims=("time", "lat", "lon"), name="PRCP",
    )
    ds = da.to_dataset()
    ds["spatial_ref"] = xr.DataArray(0)  # rioxarray-style CRS placeholder
    path = tmp_path / "Monthly_PRCP_2020_2020.nc"
    ds.to_netcdf(path)
    assert len(xr.open_dataset(path).data_vars) == 2  # would break open_dataarray

    out = _open_product_da(path)
    assert out.name == "PRCP" and out.sizes["time"] == 2


def test_get_climate_product_cache_hit_reopens(config):
    """The second call for the same product is a cache hit that reopens the
    file via _open_product_da and returns the same cube."""
    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
              source="fake", config=config)
    first = get_climate(**kw)["AGRO.PRCP"]
    assert first["nc"].exists()
    second = get_climate(**kw)["AGRO.PRCP"]  # need_nc=False -> _open_product_da
    assert second["data"].sizes["time"] == first["data"].sizes["time"]


# ---------------------------------------------------------------------------
# Product cache keys: source and exact year selection (v0.32.3)

def _register_fake2():
    """A second climate source on the same driver, to tell products apart."""
    from agwise_data import catalog
    from tests.conftest import FAKE_ENTRY

    catalog.register_entry({**FAKE_ENTRY, "id": "fake2", "title": "Second fake"})


def test_product_stem_carries_non_default_source(config):
    _register_fake2()
    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
              config=config)
    a = get_climate(source="fake", **kw)["AGRO.PRCP"]
    b = get_climate(source="fake2", **kw)["AGRO.PRCP"]
    # Two sources, same region and years: two products, never a shared hit.
    assert a["nc"].name == "Monthly_PRCP_2020_2020_fake.nc"
    assert b["nc"].name == "Monthly_PRCP_2020_2020_fake2.nc"
    assert read_manifest(a["nc"])["source_id"] == "fake"
    assert read_manifest(b["nc"])["source_id"] == "fake2"
    assert b["source"] == "fake2"


def test_product_rebuilt_when_manifest_source_differs(config):
    import json

    from agwise_data.cache import manifest_path

    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
              source="fake", config=config)
    nc = get_climate(**kw)["AGRO.PRCP"]["nc"]
    mpath = manifest_path(nc)
    meta = json.loads(mpath.read_text())
    meta["source_id"] = "some_other_source"  # e.g. the default changed
    mpath.write_text(json.dumps(meta))
    get_climate(**kw)
    assert read_manifest(nc)["source_id"] == "fake"


def test_non_contiguous_years_do_not_collide_with_range(config):
    kw = dict(variables="PRCP", bbox=BBOX, freq="monthly", source="fake",
              config=config)
    full = get_climate(years=[2020, 2021, 2022], **kw)["AGRO.PRCP"]
    gap = get_climate(years=[2020, 2022], **kw)["AGRO.PRCP"]
    assert full["nc"] != gap["nc"]
    assert gap["nc"].name.startswith("Monthly_PRCP_2020_2022_y")
    assert full["data"].sizes["time"] == 36
    assert gap["data"].sizes["time"] == 24


def test_years_tag():
    from agwise_data.api import _years_tag

    assert _years_tag([2015, 2016, 2017]) == "2015_2017"
    assert _years_tag([2017, 2015, 2016, 2016]) == "2015_2017"
    assert _years_tag([2015, 2017]).startswith("2015_2017_y")
    assert _years_tag([2015, 2017]) != _years_tag([2015, 2016, 2017])
