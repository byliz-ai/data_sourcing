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
    # window-start daily labels: 30 leads per year, the first IS the init date
    times = pd.DatetimeIndex(da["time"].values)
    assert times[0] == pd.Timestamp("2000-02-01")
    assert set(times.year) == {2000, 2001}
    assert da.sizes["time"] == 60
    assert da.attrs["time_label"] == "window_start"
    # value encodes member*1000 + lead_day (2000-02-05 = lead day 5)
    v = float(
        da.sel(member=2, time="2000-02-05").sel(lat=0.0, lon=34.0, method="nearest")
    )
    assert v == pytest.approx(2005.0)
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
    # mean over members 0..4 of (member*1000 + lead_day) = 2000 + lead_day;
    # window-start labels: 2000-02-03 = lead day 3
    v = float(da.sel(time="2000-02-03").sel(lat=0.0, lon=34.0, method="nearest"))
    assert v == pytest.approx(2003.0)
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


def test_to_valid_time_labels_window_start():
    """leadtime_hour=24 describes [init, init+24h) and must be labeled with
    the init date itself — not init+24h, one day late (the pre-v0.31 bug)."""
    from agwise_data.drivers.seasonal import Seas5Driver

    lead = pd.to_timedelta([24, 48, 72], unit="h")
    da = xr.DataArray(
        [[[1.0, 3.0, 6.0]]],  # accumulated-from-step-0 totals
        coords={
            "forecast_reference_time": [np.datetime64("2022-12-01")],
            "number": [0],
            "forecast_period": lead,
        },
        dims=("forecast_reference_time", "number", "forecast_period"),
    )
    out = Seas5Driver._to_valid_time(da, {"accumulated": True})
    times = pd.DatetimeIndex(out["time"].values)
    assert times[0] == pd.Timestamp("2022-12-01")  # first forecast day = init
    assert times[-1] == pd.Timestamp("2022-12-03")
    # de-accumulated daily values ride on the same axis
    assert out.sel(number=0).values.tolist() == [1.0, 2.0, 3.0]

    # instantaneous fields (no accumulated flag) share the same day labels
    out2 = Seas5Driver._to_valid_time(da.copy(), {})
    assert pd.DatetimeIndex(out2["time"].values)[0] == pd.Timestamp("2022-12-01")
    assert out2.sel(number=0).values.tolist() == [1.0, 3.0, 6.0]


def _forge_pre_v031(nc_path):
    """Rewrite a cached seasonal file as a pre-v0.31 one: daily labels one
    day late (window end) and no ``time_label`` stamp in attrs/manifest."""
    from agwise_data.cache import write_manifest

    with xr.open_dataset(nc_path) as ds:
        name = next(v for v in ds.data_vars if v not in ("spatial_ref", "crs"))
        da = ds[name].load()
    old = da.assign_coords(time=da["time"].values + np.timedelta64(1, "D"))
    old.attrs.pop("time_label", None)
    nc_path.unlink()
    old.to_netcdf(nc_path)
    meta = read_manifest(nc_path)
    meta.pop("time_label", None)
    write_manifest(nc_path, meta)


def test_year_cache_migrates_pre_v031_labels(config):
    """A cached per-year file from before v0.31 is shifted back one day in
    place (no re-download) the next time it is requested."""
    from agwise_data import catalog, drivers
    from tests.conftest import fake_seasonal_calls

    driver = drivers.get_driver(catalog.get_entry("fake_seasonal"), config)
    path = driver.ensure_seasonal("AGRO.PRCP", 2, 2000, "africa")
    _forge_pre_v031(path)
    n = len(fake_seasonal_calls())

    assert driver.ensure_seasonal("AGRO.PRCP", 2, 2000, "africa") == path
    assert len(fake_seasonal_calls()) == n  # migrated, not re-fetched
    with xr.open_dataset(path) as ds:
        da = ds["PRCP"]
        assert pd.DatetimeIndex(da["time"].values)[0] == pd.Timestamp("2000-02-01")
        assert da.attrs["time_label"] == "window_start"
    assert read_manifest(path)["time_label"] == "window_start"

    # idempotent: an already-migrated file is left untouched
    mtime = path.stat().st_mtime_ns
    driver.ensure_seasonal("AGRO.PRCP", 2, 2000, "africa")
    assert path.stat().st_mtime_ns == mtime


def test_get_seasonal_rebuilds_pre_v031_product(config):
    """A region product written before v0.31 (labels one day late) is
    rebuilt locally from the year cache on the next call — no re-fetch."""
    from tests.conftest import fake_seasonal_calls

    kwargs = dict(
        variables="PRCP", init_month=2, years=2000, bbox=BBOX,
        source="fake_seasonal", config=config,
    )
    res = get_seasonal(**kwargs)
    nc = res["AGRO.PRCP"]["nc"]
    res["AGRO.PRCP"]["data"].close()
    _forge_pre_v031(nc)
    n = len(fake_seasonal_calls())

    res2 = get_seasonal(**kwargs)
    assert len(fake_seasonal_calls()) == n  # rebuilt from cache, no re-fetch
    da = res2["AGRO.PRCP"]["data"]
    assert pd.DatetimeIndex(da["time"].values)[0] == pd.Timestamp("2000-02-01")
    assert da.attrs["time_label"] == "window_start"


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
