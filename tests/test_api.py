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
    from tests.conftest import FakeDriver

    kwargs = dict(
        variables="PRCP",
        years=[2020],
        bbox=BBOX,
        freq="monthly",
        source="fake",
        config=config,
    )
    get_climate(**kwargs)
    n_calls = len(FakeDriver.calls)
    res2 = get_climate(**kwargs)  # second call: no new fetches
    assert len(FakeDriver.calls) == n_calls
    assert res2["AGRO.PRCP"]["nc"].exists()


def test_harmonized_year_reused_across_products(config):
    from tests.conftest import FakeDriver

    get_climate(
        variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
        source="fake", config=config,
    )
    n_calls = len(FakeDriver.calls)
    # different product (daily, different bbox) but same harmonized year
    get_climate(
        variables="PRCP", years=[2020], bbox=(34.0, -1.0, 36.0, 1.0),
        freq="daily", source="fake", config=config,
    )
    assert len(FakeDriver.calls) == n_calls


def test_get_climate_unit_conversion_applied(config):
    res = get_climate(
        variables="TMAX", years=[2020], bbox=BBOX, freq="daily",
        source="fake", config=config,
    )
    da = res["AGRO.TMAX"]["data"]
    # synthetic Kelvin-ish values are dayofyear; k_to_degc subtracts 273.15
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
