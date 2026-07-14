"""Tests for get_season — climate/NDVI sliced to a planting->harvest window.

All network-free: they use the synthetic climate + MODIS fake drivers from
conftest. The key behaviours: the slice is bounded by the season, it crosses
the calendar year correctly, region mode writes a Season_* product, and point
mode supports both a scalar season and per-row (per-trial) seasons.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agwise_data.api import get_season

BBOX = [30.0, -5.0, 42.0, 5.0]  # == conftest.FAKE_BBOX
MODIS_SOURCES = ["fake_mod", "fake_myd"]


def _gdal_update_ok() -> bool:
    """Whether this env's rasterio can reopen a GeoTIFF in update mode.

    Some local conda builds ship a GDAL whose GTiff driver has no update
    capability, so ``write_geotiff``'s band-labelling reopen (``r+``) fails
    with ``TypeError: 'NoneType' object is not callable``. The tif export
    path is identical across get_climate/get_modis/get_season, so we skip the
    season tif assertion here rather than duplicate an env-specific failure;
    CI (which has a working GDAL) still exercises it.
    """
    import tempfile

    import numpy as np
    import xarray as xr

    from agwise_data.spatial import write_geotiff

    da = xr.DataArray(
        np.zeros((1, 2, 2), dtype="float32"),
        coords={"time": [np.datetime64("2001-01-01")], "lat": [0.0, 0.1],
                "lon": [0.0, 0.1]},
        dims=("time", "lat", "lon"),
        name="x",
    )
    try:
        with tempfile.TemporaryDirectory() as d:
            write_geotiff(da, Path(d) / "probe.tif", labels=["b1"])
        return True
    except Exception:
        return False


GDAL_UPDATE_OK = _gdal_update_ok()


# --------------------------------------------------------------------------
# Region mode
# --------------------------------------------------------------------------
def test_region_climate_crossyear_slice(config):
    res = get_season(
        "PRCP",
        planting_date="2000-12-15",
        harvest_date="2001-01-15",
        bbox=BBOX,
        source="fake",
        config=config,
    )
    da = res["AGRO.PRCP"]["data"]
    times = pd.DatetimeIndex(da["time"].values)
    # Slice is bounded by the season and actually crosses the New Year.
    assert times.min() >= pd.Timestamp("2000-12-15")
    assert times.max() <= pd.Timestamp("2001-01-15")
    assert times.min().year == 2000 and times.max().year == 2001
    # The synthetic climate value equals the day-of-year at every step.
    sample = da.isel(lat=0, lon=0).to_series()
    assert np.allclose(sample.values, sample.index.dayofyear.values)
    # A Season_* product is written (this is the season cache).
    assert res["AGRO.PRCP"]["nc"].name == "Season_PRCP_20001215_20010115.nc"
    assert res["AGRO.PRCP"]["nc"].exists()


def test_region_ndvi_crossyear_slice(config):
    res = get_season(
        "NDVI",
        planting_date="2000-09-14",
        harvest_date="2001-02-28",
        bbox=BBOX,
        source=MODIS_SOURCES,
        config=config,
    )
    da = res["RS.NDVI"]["data"]
    times = pd.DatetimeIndex(da["time"].values)
    assert len(times) > 0
    assert times.min() >= pd.Timestamp("2000-09-14")
    assert times.max() <= pd.Timestamp("2001-02-28")
    # composites from both years appear in the cross-year window
    assert set(times.year) == {2000, 2001}
    assert res["RS.NDVI"]["kind"] == "rs"


def test_region_product_cache_hit(config):
    from tests.conftest import fake_calls

    kwargs = dict(
        variables="PRCP",
        planting_date="2001-03-01",
        harvest_date="2001-06-30",
        bbox=BBOX,
        source="fake",
        config=config,
    )
    get_season(**kwargs)
    n = len(fake_calls())
    get_season(**kwargs)  # second call: product already on disk, no refetch
    assert len(fake_calls()) == n


@pytest.mark.skipif(
    not GDAL_UPDATE_OK, reason="local GDAL lacks GTiff update mode (env issue)"
)
def test_region_tif_export(config):
    res = get_season(
        "PRCP",
        planting_date="2001-03-01",
        harvest_date="2001-03-31",
        bbox=BBOX,
        source="fake",
        out_format=["nc", "tif"],
        config=config,
    )
    tif = res["AGRO.PRCP"]["tif"]
    assert tif is not None and tif.exists()


# --------------------------------------------------------------------------
# Point mode
# --------------------------------------------------------------------------
def _points():
    return pd.DataFrame(
        {"lon": [31.0, 35.0], "lat": [-1.0, 2.0], "site": ["a", "b"]}
    )


def test_points_scalar_season(config):
    out = get_season(
        ["PRCP", "TMAX"],
        planting_date="2001-04-01",
        harvest_date="2001-04-30",
        points=_points(),
        source="fake",
        config=config,
    )
    assert list(out.columns) == ["point", "lon", "lat", "time", "variable", "value"]
    assert set(out["variable"]) == {"PRCP", "TMAX"}
    t = pd.DatetimeIndex(out["time"])
    assert t.min() >= pd.Timestamp("2001-04-01")
    assert t.max() <= pd.Timestamp("2001-04-30")
    # Both points present.
    assert set(out["point"]) == {0, 1}


def test_points_perrow_crossyear_season(config):
    df = _points()
    # point a: a normal within-year season; point b: crosses the New Year
    df["pl"] = ["2001-03-01", "2000-12-20"]
    df["hv"] = ["2001-03-20", "2001-01-10"]
    out = get_season(
        "PRCP",
        points=df,
        planting_col="pl",
        harvest_col="hv",
        source="fake",
        config=config,
    )
    a = out[out["point"] == 0]
    b = out[out["point"] == 1]
    ta, tb = pd.DatetimeIndex(a["time"]), pd.DatetimeIndex(b["time"])
    assert ta.min() >= pd.Timestamp("2001-03-01")
    assert ta.max() <= pd.Timestamp("2001-03-20")
    # point b's window really straddles Dec->Jan
    assert tb.min() >= pd.Timestamp("2000-12-20")
    assert tb.max() <= pd.Timestamp("2001-01-10")
    assert set(tb.year) == {2000, 2001}


def test_points_skip_invalid_rows(config):
    df = pd.DataFrame(
        {"lon": [31.0, np.nan], "lat": [-1.0, 2.0]}
    )
    with pytest.warns(UserWarning):
        out = get_season(
            "PRCP",
            planting_date="2001-04-01",
            harvest_date="2001-04-30",
            points=df,
            source="fake",
            config=config,
        )
    assert set(out["point"]) == {0}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def test_planting_after_harvest_raises(config):
    with pytest.raises(ValueError, match="after harvest"):
        get_season(
            "PRCP",
            planting_date="2001-06-01",
            harvest_date="2001-05-01",
            bbox=BBOX,
            source="fake",
            config=config,
        )


def test_empty_variables_raises(config):
    with pytest.raises(ValueError, match="at least one variable"):
        get_season(
            [],
            planting_date="2001-01-01",
            harvest_date="2001-02-01",
            bbox=BBOX,
            config=config,
        )


def test_partial_planting_cols_raises(config):
    with pytest.raises(ValueError, match="both planting_col and harvest_col"):
        get_season(
            "PRCP",
            points=_points(),
            planting_col="pl",
            source="fake",
            config=config,
        )
