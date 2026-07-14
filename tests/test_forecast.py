"""Tests for seasonal-forecast bias correction (QDM), scope-map #3.

Network-free: the QDM maths are checked on synthetic samples with a known
injected bias, and the bias_correct API is driven through its obs/hind/fcst
injection path (no get_climate/get_seasonal fetch). The key property: the
systematic model bias is removed while the forecast's own anomaly is kept.
"""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data.api import bias_correct
from agwise_data.forecast import bias_correct_cube, quantile_delta_map


# --------------------------------------------------------------------------
# pure QDM
# --------------------------------------------------------------------------
def test_qdm_additive_removes_bias_keeps_anomaly():
    rng = np.random.default_rng(0)
    obs = rng.normal(20, 2, 3000)
    hind = rng.normal(23, 2, 3000)          # model warm bias +3
    fcst = rng.normal(24, 2, 800)           # +1 anomaly above hind mean
    bc = quantile_delta_map(fcst, obs, hind, "additive")
    assert abs(bc.mean() - 21.0) < 0.4      # obs mean + preserved +1 anomaly


def test_qdm_multiplicative_no_negatives():
    rng = np.random.default_rng(1)
    obs = rng.gamma(2, 2, 3000)
    hind = rng.gamma(2, 2, 3000) * 1.5      # 1.5x wet bias
    fcst = rng.gamma(2, 2, 800) * 1.5 * 1.2  # +20% forecast anomaly
    bc = quantile_delta_map(fcst, obs, hind, "multiplicative")
    assert (bc >= 0).all()
    assert abs(bc.mean() - obs.mean() * 1.2) < obs.mean() * 0.15


def test_qdm_too_few_samples_returns_unchanged():
    vals = np.array([5.0, np.nan, 7.0])
    out = quantile_delta_map(vals, np.array([1.0]), np.array([2.0]), "additive")
    assert np.isnan(out[1]) and out[0] == 5.0 and out[2] == 7.0


def test_qdm_bad_kind_raises():
    with pytest.raises(ValueError, match="additive.*multiplicative"):
        quantile_delta_map(np.array([1.0, 2.0]), np.arange(5.0), np.arange(5.0),
                           kind="nope")


# --------------------------------------------------------------------------
# cube-level (with regrid)
# --------------------------------------------------------------------------
def _cubes(kind="additive"):
    rng = np.random.default_rng(2)
    t_o = pd.date_range("2001-02-02", periods=150, freq="D")
    lat = np.array([-2.0, -1.9]); lon = np.array([30.0, 30.1])
    obs = xr.DataArray(rng.normal(20, 2, (150, 2, 2)),
                       coords={"time": t_o, "lat": lat, "lon": lon},
                       dims=("time", "lat", "lon"), name="TMAX")
    latc = np.array([-2.05, -1.85]); lonc = np.array([29.95, 30.15])
    hind = xr.DataArray(rng.normal(23, 2, (4, 150, 2, 2)),
                        coords={"member": np.arange(4), "time": t_o,
                                "lat": latc, "lon": lonc},
                        dims=("member", "time", "lat", "lon"), name="TMAX")
    t_f = pd.date_range("2021-02-02", periods=150, freq="D")
    fcst = xr.DataArray(rng.normal(24, 2, (4, 150, 2, 2)),
                        coords={"member": np.arange(4), "time": t_f,
                                "lat": latc, "lon": lonc},
                        dims=("member", "time", "lat", "lon"), name="TMAX")
    return obs, hind, fcst


def test_bias_correct_cube_regrids_and_corrects():
    obs, hind, fcst = _cubes()
    out = bias_correct_cube(obs, hind, fcst, "additive")
    # regridded onto the obs grid
    assert list(out["lat"].values) == list(obs["lat"].values)
    assert dict(out.sizes) == {"member": 4, "time": 150, "lat": 2, "lon": 2}
    assert abs(float(out.mean()) - 21.0) < 0.6  # bias removed, +1 anomaly kept


def test_bias_correct_cube_window():
    obs, hind, fcst = _cubes()
    out = bias_correct_cube(obs, hind, fcst, "additive", window_days=20)
    assert dict(out.sizes)["time"] == 150
    assert abs(float(out.mean()) - 21.0) < 0.8


# --------------------------------------------------------------------------
# bias_correct API (injection path, no network)
# --------------------------------------------------------------------------
def test_bias_correct_api_injection(config):
    obs, hind, fcst = _cubes()
    res = bias_correct(
        ["TMAX"], init_month=2, forecast_year=2021, calib_years=[2001],
        bbox=[30.0, -2.0, 30.1, -1.9],
        obs={"AGRO.TMAX": obs}, hind={"AGRO.TMAX": hind}, fcst={"AGRO.TMAX": fcst},
        config=config,
    )
    info = res["AGRO.TMAX"]
    assert info["kind"] == "additive"
    assert info["nc"].name == "Seasonal_TMAX_i02_2021_BC.nc"
    assert info["nc"].exists()
    assert abs(float(info["data"].mean()) - 21.0) < 0.6


def test_bias_correct_unknown_variable_raises(config):
    # RHUM is a valid climate variable but has no defined BC transform
    with pytest.raises(ValueError, match="No bias-correction transform"):
        bias_correct(["RHUM"], init_month=2, forecast_year=2021,
                     calib_years=[2001], bbox=[30.0, -2.0, 30.1, -1.9],
                     obs={}, hind={}, fcst={}, config=config)
