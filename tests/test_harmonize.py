import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data.harmonize import (
    apply_conversion,
    canonical_name,
    legacy_name,
    rainy_days,
    short_name,
    standardize,
    to_monthly,
)


def _daily(values, start="2020-01-01", name="precip"):
    times = pd.date_range(start, periods=len(values), freq="D")
    return xr.DataArray(
        np.asarray(values, dtype="float32")[:, None, None],
        coords={"time": times, "latitude": [1.0], "longitude": [30.0]},
        dims=("time", "latitude", "longitude"),
        name=name,
    )


def test_canonical_name_accepts_all_spellings():
    assert canonical_name("AGRO.PRCP") == "AGRO.PRCP"
    assert canonical_name("PRCP") == "AGRO.PRCP"
    assert canonical_name("prcp") == "AGRO.PRCP"
    assert canonical_name("Precipitation") == "AGRO.PRCP"
    assert canonical_name("TemperatureMax") == "AGRO.TMAX"
    assert short_name("Precipitation") == "PRCP"
    assert legacy_name("AGRO.TMAX") == "TemperatureMax"
    with pytest.raises(ValueError):
        canonical_name("NotAVariable")


def test_unit_conversions():
    da = _daily([273.15, 283.15])
    out = apply_conversion(da, "k_to_degc")
    assert np.allclose(out.values.ravel(), [0.0, 10.0])
    out = apply_conversion(_daily([2_000_000.0]), "jm2_to_mjm2")
    assert np.allclose(out.values.ravel(), [2.0])
    assert apply_conversion(da, None) is da
    with pytest.raises(ValueError):
        apply_conversion(da, "furlongs")


def test_standardize_renames_and_sorts():
    da = _daily([1.0, 2.0])
    # descending latitude on purpose
    da = xr.concat(
        [da.assign_coords(latitude=[5.0]), da.assign_coords(latitude=[1.0])],
        dim="latitude",
    )
    out = standardize(da, "PRCP", "testsrc")
    assert out.name == "PRCP"
    assert set(out.dims) == {"time", "lat", "lon"}
    assert float(out.lat[0]) < float(out.lat[-1])
    assert out.attrs["agwise_name"] == "AGRO.PRCP"
    assert out.attrs["source"] == "testsrc"


def test_to_monthly_sum_for_precip_mean_for_temp():
    # 31 days of January, value 2 each day
    da = _daily([2.0] * 31)
    monthly = to_monthly(da, "PRCP")
    assert monthly.sizes["time"] == 1
    assert float(monthly.isel(time=0).values.ravel()[0]) == pytest.approx(62.0)

    monthly_mean = to_monthly(da.rename("TMAX"), "TMAX")
    assert float(monthly_mean.isel(time=0).values.ravel()[0]) == pytest.approx(2.0)


def test_to_monthly_all_nan_stays_nan():
    da = _daily([np.nan] * 31)
    monthly = to_monthly(da, "PRCP")
    assert np.isnan(monthly.values).all()


def test_rainy_days_threshold_and_nan():
    da = _daily([0.0, 1.9, 2.0, 5.0, np.nan])
    out = rainy_days(da)
    assert float(out.values.ravel()[0]) == 2.0
    all_nan = _daily([np.nan, np.nan])
    assert np.isnan(rainy_days(all_nan).values).all()
