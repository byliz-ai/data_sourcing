"""End-to-end tests of the seasonal (SEAS5) layer over the fake driver."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data.api import get_seasonal
from agwise_data.cache import read_manifest
from agwise_data.drivers.seasonal import deaccumulate_leads

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain


def test_get_seasonal_product(config):
    res = get_seasonal(
        variables="PRCP", init_month=2, years=[2000, 2001],
        bbox=BBOX, source="fake_seasonal", config=config,
    )
    info = res["AGRO.PRCP"]
    assert info["nc"].exists()
    da = info["data"]
    assert list(da.dims) == ["member", "time", "lat", "lon"]
    assert da.sizes["member"] == 5
    # valid dates: 30 daily leads per year, starting the day after init
    times = pd.DatetimeIndex(da["time"].values)
    assert times[0] == pd.Timestamp("2000-02-02")
    assert set(times.year) == {2000, 2001}
    assert da.sizes["time"] == 60
    # value encodes member*1000 + lead_day
    v = float(
        da.sel(member=2, time="2000-02-05").sel(lat=0.0, lon=34.0, method="nearest")
    )
    assert v == pytest.approx(2004.0)
    meta = read_manifest(info["nc"])
    assert meta["init_month"] == 2
    assert meta["ensemble"] == "members"


def test_seasonal_cache_per_year(config):
    from tests.conftest import fake_seasonal_calls

    kwargs = dict(
        variables="PRCP", init_month=3, bbox=BBOX,
        source="fake_seasonal", config=config,
    )
    get_seasonal(years=[2000, 2001], **kwargs)
    n = len(fake_seasonal_calls())
    assert n == 2  # one fetch per year
    # extending the range only fetches the missing year
    get_seasonal(years=[2000, 2001, 2002], **kwargs)
    fetched_years = [c[2] for c in fake_seasonal_calls()[n:]]
    assert fetched_years == [2002]


def test_seasonal_ensemble_mean(config):
    res = get_seasonal(
        variables="PRCP", init_month=2, years=2000, bbox=BBOX,
        ensemble="mean", source="fake_seasonal", config=config,
    )
    da = res["AGRO.PRCP"]["data"]
    assert "member" not in da.dims
    # mean over members 0..4 of (member*1000 + lead_day) = 2000 + lead_day
    v = float(da.sel(time="2000-02-03").sel(lat=0.0, lon=34.0, method="nearest"))
    assert v == pytest.approx(2002.0)
    # separate product file from the members one
    res2 = get_seasonal(
        variables="PRCP", init_month=2, years=2000, bbox=BBOX,
        source="fake_seasonal", config=config,
    )
    assert res["AGRO.PRCP"]["nc"] != res2["AGRO.PRCP"]["nc"]


def test_seasonal_tif_needs_reduced_ensemble(config):
    with pytest.raises(ValueError, match="reduced ensemble"):
        get_seasonal(
            variables="PRCP", init_month=2, years=2000, bbox=BBOX,
            out_format=["nc", "tif"], source="fake_seasonal", config=config,
        )


def test_seasonal_invalid_init_month(config):
    with pytest.raises(ValueError, match="init_month"):
        get_seasonal(
            variables="PRCP", init_month=13, years=2000, bbox=BBOX,
            source="fake_seasonal", config=config,
        )


def test_deaccumulate_leads():
    # accumulated totals 1, 3, 6 → daily increments 1, 2, 3
    lead = pd.to_timedelta([24, 48, 72], unit="h")
    acc = xr.DataArray(
        [[1.0, 3.0, 6.0]],
        coords={"number": [0], "forecast_period": lead},
        dims=("number", "forecast_period"),
    )
    daily = deaccumulate_leads(acc, "forecast_period")
    assert daily.values.tolist() == [[1.0, 2.0, 3.0]]
    # float noise producing a negative increment is clipped to zero
    acc2 = xr.DataArray(
        [[1.0, 0.9999, 2.0]],
        coords={"number": [0], "forecast_period": lead},
        dims=("number", "forecast_period"),
    )
    daily2 = deaccumulate_leads(acc2, "forecast_period")
    assert float(daily2[0, 1]) == 0.0
    assert np.all(daily2.values >= 0)
