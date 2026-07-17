"""Tests for the NDVI gap-fill + Savitzky-Golay smoothing layer."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data.api import get_ndvi, smooth_ndvi
from agwise_data.cache import read_manifest
from agwise_data.smoothing import savgol_gapfill, smooth_stack

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain
SOURCES = ("fake_mod", "fake_myd")


def _ramp_cube(n_time=20, ny=3, nx=4, slope=2.0, intercept=1.0):
    """A cube whose per-pixel series is a straight line (SG must preserve it)."""
    t = np.arange(n_time, dtype="float64")
    series = slope * t + intercept
    data = np.broadcast_to(series[:, None, None], (n_time, ny, nx)).copy()
    times = pd.date_range("2020-01-01", periods=n_time, freq="8D")
    return xr.DataArray(
        data.astype("float32"),
        coords={"time": times, "lat": np.arange(ny) * 0.5,
                "lon": np.arange(nx) * 0.5},
        dims=("time", "lat", "lon"),
        name="NDVI",
    )


# --- numeric core ---------------------------------------------------------

def test_savgol_preserves_a_linear_ramp():
    # Savitzky-Golay of polyorder >= 1 reproduces a linear series exactly.
    series = (3.0 * np.arange(15) - 4.0)[:, None]
    out = savgol_gapfill(series, window=5, polyorder=3)
    assert np.allclose(out[:, 0], series[:, 0], atol=1e-4)
    assert out.dtype == np.float32


@pytest.mark.parametrize("gapfill", ["linear", "mean"])
def test_smooth_stack_fills_gaps_and_keeps_allnan_nan(gapfill):
    da = _ramp_cube()
    da.values[5, 0, 0] = np.nan          # a single gap -> filled
    da.values[:, 1, 1] = np.nan          # never observed -> stays NaN
    out = smooth_stack(da, window=5, polyorder=3, gapfill=gapfill)

    assert out.dims == da.dims
    assert out.name == "NDVI"
    assert np.isfinite(out.values[:, 0, 0]).all()     # gap filled
    assert np.isnan(out.values[:, 1, 1]).all()        # all-NaN preserved
    # away from the gap (window=5 only perturbs +/-2 around index 5) the ramp
    # is preserved to float precision by either fill method.
    true_ramp = 2.0 * np.arange(20) + 1.0
    far = np.r_[0:3, 8:20]
    assert np.allclose(out.values[far, 0, 0], true_ramp[far], atol=1e-3)
    assert f"gapfill={gapfill}" in out.attrs["smoothing"]


def test_linear_gapfill_beats_mean_on_a_gappy_ramp():
    # On a straight line, linear interpolation recovers the gap exactly while
    # the temporal mean does not, so the whole pixel comes back on the ramp.
    da = _ramp_cube(n_time=20)
    da.values[5, 0, 0] = np.nan
    lin = smooth_stack(da, window=5, polyorder=3, gapfill="linear")
    mean = smooth_stack(da, window=5, polyorder=3, gapfill="mean")
    true_ramp = 2.0 * np.arange(20) + 1.0
    lin_err = np.abs(lin.values[:, 0, 0] - true_ramp).max()
    mean_err = np.abs(mean.values[:, 0, 0] - true_ramp).max()
    assert lin_err < 1e-3          # linear recovers the line
    assert mean_err > lin_err      # mean fill distorts around the gap


def test_mean_path_matches_numeric_core():
    da = _ramp_cube(n_time=15)
    da.values[7, 1, 2] = np.nan
    via_stack = smooth_stack(da, window=5, polyorder=3, gapfill="mean").values
    via_core = savgol_gapfill(
        da.transpose("time", "lat", "lon").values, window=5, polyorder=3
    )
    assert np.allclose(via_stack, via_core, equal_nan=True)


def test_smooth_stack_validation_errors():
    da = _ramp_cube(n_time=20)
    with pytest.raises(ValueError, match="odd integer"):
        smooth_stack(da, window=8)
    with pytest.raises(ValueError, match="smaller than window"):
        smooth_stack(da, window=5, polyorder=5)
    with pytest.raises(ValueError, match="at least"):
        smooth_stack(_ramp_cube(n_time=5), window=9)
    with pytest.raises(ValueError, match="gapfill must be"):
        smooth_stack(da, gapfill="spline")


# --- smooth_ndvi end to end over the fake drivers -------------------------

def test_smooth_ndvi_matches_raw_on_a_linear_series(config):
    # The interleaved Terra+Aqua fake is a perfect ramp of composite DOYs, so
    # the smoothed cube should equal the raw one to float precision.
    raw = get_ndvi(years=2020, bbox=BBOX, source=SOURCES, config=config)
    raw_da = raw["RS.NDVI"]["data"]

    res = smooth_ndvi(
        years=2020, bbox=BBOX, source=SOURCES, cropmask=False, config=config,
    )
    info = res["RS.NDVI"]
    assert info["nc"].exists()
    sm = info["data"].transpose("time", "lat", "lon")
    assert sm.sizes["time"] == 46
    assert np.allclose(sm.values, raw_da.transpose("time", "lat", "lon").values,
                       atol=1e-2)

    meta = read_manifest(info["nc"])
    assert meta["smoothing"]["method"] == "savitzky_golay"
    assert meta["smoothing"]["window"] == 9
    assert meta["smoothing"]["gapfill"] == "linear"
    assert meta["cropmask"] is False


def test_smooth_ndvi_cropmask_blanks_non_cropland(config):
    res = smooth_ndvi(
        years=2020, bbox=BBOX, source=SOURCES,
        cropmask=True, cropmask_source="fake_worldcover", config=config,
    )
    sm = res["RS.NDVI"]["data"]
    finite = np.isfinite(sm.values)
    # a mix: cropland pixels smoothed, non-cropland fully NaN
    assert finite.any()
    assert (~finite).any()
    # a non-cropland pixel is NaN across the whole series
    per_pixel_all_nan = np.isnan(sm.values).all(axis=0)
    assert per_pixel_all_nan.any()

    meta = read_manifest(res["RS.NDVI"]["nc"])
    assert meta["cropmask"] is True


def test_smooth_ndvi_caches_and_reuses(config):
    from tests.conftest import fake_modis_calls

    kwargs = dict(years=2020, bbox=BBOX, source=SOURCES, cropmask=False,
                  config=config)
    smooth_ndvi(**kwargs)
    n = len(fake_modis_calls())
    smooth_ndvi(**kwargs)  # second call: product cache hit, no refetch
    assert len(fake_modis_calls()) == n


def test_smooth_ndvi_custom_params_get_own_product(config):
    default = smooth_ndvi(
        years=2020, bbox=BBOX, source=SOURCES, cropmask=False, config=config,
    )["RS.NDVI"]["nc"]
    custom = smooth_ndvi(
        years=2020, bbox=BBOX, source=SOURCES, cropmask=False,
        window=7, polyorder=2, config=config,
    )["RS.NDVI"]["nc"]
    legacy = smooth_ndvi(
        years=2020, bbox=BBOX, source=SOURCES, cropmask=False,
        gapfill="mean", config=config,
    )["RS.NDVI"]["nc"]
    assert default != custom != legacy
    assert "w7p2" in custom.name
    assert "_mean_SG" in legacy.name
