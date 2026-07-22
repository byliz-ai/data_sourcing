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

from agwise_data.api import bias_correct, forecast_to_dssat
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


def test_bias_correct_cube_single_cell_source_not_all_nan():
    """A coarse forecast that is a single cell (small AOI on the 1° SEAS5 grid)
    must still downscale onto the fine obs grid — linear interp alone would
    leave every obs cell NaN (no extrapolation), dropping all points."""
    obs, hind, fcst = _cubes()
    # collapse hind/fcst to a single coarse cell whose centre sits outside the
    # obs grid's centre range, so linear interp yields all-NaN without the
    # nearest fallback.
    hind1 = hind.isel(lat=[0], lon=[0])
    fcst1 = fcst.isel(lat=[0], lon=[0])
    out = bias_correct_cube(obs, hind1, fcst1, "additive")
    assert dict(out.sizes) == {"member": 4, "time": 150, "lat": 2, "lon": 2}
    assert not bool(np.isnan(out).all())          # was 100% NaN before the fix
    assert float(np.isfinite(out).mean()) > 0.99  # every obs cell filled


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


# --------------------------------------------------------------------------
# forecast_to_dssat (#3b) — corrected-cube injection, no network
# --------------------------------------------------------------------------
def _soil_frame(index):
    import pandas as pd
    depths = ["0_5cm", "5_15cm", "15_30cm", "30_60cm", "60_100cm", "100_200cm"]
    clay = [34, 43, 49, 54, 40, 39]; sand = [43, 34, 28, 27, 40, 41]
    silt = [23, 23, 21, 19, 20, 20]; soc = [36, 20, 16, 12, 3, 2]
    nit = [2.8, 2, 1.6, .9, .8, .7]; ph = [6.1, 6.1, 6.1, 6.1, 7.4, 7.6]
    cec = [29, 27, 27, 28, 28, 28]; bd = [1.3, 1.3, 1.3, 1.4, 1.45, 1.46]
    row = {}
    for i, d in enumerate(depths):
        row[f"CLAY_{d}"] = clay[i]; row[f"SAND_{d}"] = sand[i]
        row[f"SILT_{d}"] = silt[i]; row[f"SOC_{d}"] = soc[i]
        row[f"NITROGEN_{d}"] = nit[i]; row[f"PH_{d}"] = ph[i]
        row[f"CEC_{d}"] = cec[i]; row[f"BDOD_{d}"] = bd[i]
    return pd.DataFrame([row for _ in index], index=index)


def _corrected_weather():
    rng = np.random.default_rng(3)
    t = pd.date_range("2021-02-02", periods=90, freq="D")
    lat = np.array([-2.0, -1.9]); lon = np.array([30.0, 30.1])
    dims = ("member", "time", "lat", "lon"); shape = (4, 90, 2, 2)
    coords = {"member": np.arange(4), "time": t, "lat": lat, "lon": lon}
    means = {"PRCP": (0, 15), "TMAX": (26, 31), "TMIN": (13, 17), "SRAD": (15, 22)}
    out = {}
    for short, (lo, hi) in means.items():
        da = xr.DataArray(rng.uniform(lo, hi, shape), coords=coords, dims=dims,
                          name=short)
        out[f"AGRO.{short}"] = {"data": da, "short": short}
    return out


def test_forecast_to_dssat_injection(tmp_path):
    import pandas as pd
    pts = pd.DataFrame({"lon": [30.02, 30.08], "lat": [-1.98, -1.92],
                        "site": ["A", "B"]})
    res = forecast_to_dssat(
        pts, init_month=2, forecast_year=2021, calib_years=[2001],
        out_dir=tmp_path / "FC_DSSAT", ensemble="mean", station_col="site",
        corrected=_corrected_weather(), soil=_soil_frame(pts.index),
    )
    assert len(res) == 2
    for n, r in enumerate(res, start=1):
        assert r["wth"].name == f"WHTE{n:04d}.WTH" and r["wth"].exists()
        assert r["sol"].exists()
    # the .WTH carries the forecast season dates (2021, DOY 33 = Feb 2)
    data = [ln for ln in res[0]["wth"].read_text().splitlines() if ln[:2].isdigit()]
    assert data[0].startswith("2021")


def test_forecast_to_dssat_infers_region_from_points(tmp_path, monkeypatch):
    # With no region given, the forecast region is inferred from the points
    # (like to_dssat), instead of erroring "Provide geometry/country/bbox".
    import pandas as pd
    import agwise_data.api as api
    pts = pd.DataFrame({"lon": [30.02, 30.08], "lat": [-1.98, -1.92],
                        "site": ["A", "B"]})
    captured = {}

    def fake_bias_correct(variables, init_month, forecast_year, calib_years, **kw):
        captured.update(kw)
        return _corrected_weather()

    monkeypatch.setattr(api, "bias_correct", fake_bias_correct)
    res = forecast_to_dssat(
        pts, init_month=2, forecast_year=2021, calib_years=[2001],
        out_dir=tmp_path / "FC_INFER", station_col="site",
        soil=_soil_frame(pts.index),
    )
    bbox = captured["bbox"]
    assert bbox is not None, "region should be inferred from points"
    w, s, e, n = bbox
    assert w < pts["lon"].min() and e > pts["lon"].max()   # buffered around points
    assert s < pts["lat"].min() and n > pts["lat"].max()
    assert len(res) == 2


def test_forecast_to_dssat_bad_ensemble(tmp_path):
    import pandas as pd
    pts = pd.DataFrame({"lon": [30.02], "lat": [-1.98]})
    with pytest.raises(ValueError, match="ensemble must be"):
        forecast_to_dssat(pts, init_month=2, forecast_year=2021,
                          calib_years=[2001], ensemble="members",
                          corrected=_corrected_weather(),
                          soil=_soil_frame(pts.index), out_dir=tmp_path / "x")
