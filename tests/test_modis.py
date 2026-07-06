"""End-to-end tests of the MODIS composite layer over the fake drivers."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data import catalog
from agwise_data.api import get_modis, get_ndvi
from agwise_data.cache import read_manifest
from agwise_data.drivers import register
from agwise_data.drivers.modis import mask_invalid, plan_tiles
from agwise_data.harmonize import standardize_composite
from agwise_data.stac import to_stac_collection

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain

SOURCES = ("fake_mod", "fake_myd")  # Terra-like + Aqua-like fakes


def test_get_modis_interleaves_terra_and_aqua(config):
    res = get_modis(
        variables="NDVI", years=[2020, 2021], bbox=BBOX,
        source=SOURCES, config=config,
    )
    info = res["RS.NDVI"]
    assert info["nc"].exists()
    da = info["data"]
    assert list(da.dims) == ["time", "lat", "lon"]
    # 23 Terra + 23 Aqua composites per year, interleaved and sorted
    assert da.sizes["time"] == 92
    times = pd.DatetimeIndex(da["time"].values)
    assert times.is_monotonic_increasing
    assert list(times[:3]) == [
        pd.Timestamp("2020-01-01"),  # Terra DOY 1
        pd.Timestamp("2020-01-09"),  # Aqua DOY 9
        pd.Timestamp("2020-01-17"),  # Terra DOY 17
    ]
    # values encode the composite DOY
    v = float(da.sel(time="2020-01-09").sel(lat=0.0, lon=34.0, method="nearest"))
    assert v == pytest.approx(9.0)
    meta = read_manifest(info["nc"])
    assert meta["source_ids"] == list(SOURCES)
    assert meta["n_composites"] == 92


def test_get_ndvi_single_satellite(config):
    res = get_ndvi(
        years=2020, bbox=BBOX, source="fake_mod", config=config,
    )
    da = res["RS.NDVI"]["data"]
    assert da.sizes["time"] == 23
    doys = pd.DatetimeIndex(da["time"].values).dayofyear
    assert list(doys) == list(range(1, 369, 16))


def test_modis_cache_per_year(config):
    from tests.conftest import fake_modis_calls

    kwargs = dict(variables="NDVI", bbox=BBOX, source=SOURCES, config=config)
    get_modis(years=[2020, 2021], **kwargs)
    n = len(fake_modis_calls())
    assert n == 4  # one fetch per (satellite, year)
    # extending the range only fetches the missing year on both satellites
    get_modis(years=[2020, 2021, 2022], **kwargs)
    fetched = [(c[0], c[2]) for c in fake_modis_calls()[n:]]
    assert sorted(fetched) == [("fake_mod", 2022), ("fake_myd", 2022)]


def test_modis_tif_band_labels(config):
    import rasterio

    res = get_modis(
        variables="NDVI", years=2020, bbox=BBOX,
        source="fake_mod", out_format=["nc", "tif"], config=config,
    )
    tif = res["RS.NDVI"]["tif"]
    assert tif and tif.exists()
    with rasterio.open(tif) as src:
        assert src.count == 23
        # composite dates in the labels: year-based selection keeps working
        assert src.descriptions[0] == "2020_01_01"
        assert src.descriptions[1] == "2020_01_17"


def test_modis_incomplete_past_year_refused(config):
    from tests.conftest import FakeModisDriver, _fake_modis_entry

    @register("fake_modis_trunc")
    class TruncatedModisDriver(FakeModisDriver):
        def _fetch_year(self, variable, year, domain):
            da, meta = super()._fetch_year(variable, year, domain)
            return da.isel(time=slice(0, 20)), meta

    entry = _fake_modis_entry("fake_mod_trunc", 1, "2000-02-18")
    entry["driver"] = "fake_modis_trunc"
    catalog.register_entry(entry)

    with pytest.raises(RuntimeError, match="incomplete past year"):
        get_modis(
            variables="NDVI", years=2020, bbox=BBOX,
            source="fake_mod_trunc", config=config,
        )


def test_modis_invalid_satellite(config):
    with pytest.raises(ValueError, match="satellite"):
        get_modis(variables="NDVI", years=2020, bbox=BBOX,
                  satellite="sentinel", config=config)


def test_plan_tiles_covers_window():
    tiles = plan_tiles(5000, 3000, tile=2048)
    assert len(tiles) == 6  # 3 x 2 blocks
    covered = np.zeros((3000, 5000), dtype=int)
    for x, y, w, h in tiles:
        covered[y : y + h, x : x + w] += 1
    assert (covered == 1).all()  # full cover, no overlap
    with pytest.raises(ValueError):
        plan_tiles(0, 100)


def test_mask_invalid_fill_range_and_qa():
    spec = {"fill_value": -3000, "valid_range": [-2000, 10000]}
    raw = np.array([[-3000, 5000], [12000, 8000]], dtype="int16")
    qa = np.array([[0, 3], [1, 1]], dtype="int16")
    out = mask_invalid(raw, spec, qa, qa_keep=[0, 1])
    assert np.isnan(out[0, 0])          # fill value
    assert np.isnan(out[0, 1])          # cloudy QA
    assert np.isnan(out[1, 0])          # out of valid range
    assert out[1, 1] == pytest.approx(8000.0)
    # without QA, only fill/range masking applies
    out2 = mask_invalid(raw, spec)
    assert out2[0, 1] == pytest.approx(5000.0)


def test_standardize_composite_conventions():
    times = pd.to_datetime(["2021-01-17", "2021-01-01"])
    da = xr.DataArray(
        np.ones((2, 2, 2), dtype="float32"),
        coords={"time": times, "y": [2.0, 1.0], "x": [30.0, 31.0]},
        dims=("time", "y", "x"),
        name="NDVI_raw",
    )
    out = standardize_composite(da, "NDVI", "mod13q1")
    assert out.name == "NDVI"
    assert list(out.dims) == ["time", "lat", "lon"]
    assert float(out.lat[0]) < float(out.lat[-1])           # ascending lat
    assert pd.DatetimeIndex(out["time"].values).is_monotonic_increasing
    assert out.attrs["agwise_name"] == "RS.NDVI"
    assert out.attrs["source"] == "mod13q1"


def test_modis_catalog_and_stac():
    assert "mod13q1" in catalog.list_sources()
    assert "myd13q1" in catalog.list_sources()
    coll = to_stac_collection("mod13q1")
    assert "RS.NDVI" in coll["summaries"]["variables"]
    ndvi = coll["summaries"]["variables"]["RS.NDVI"]
    assert "vegetation index" in ndvi["description"].lower()
    assert coll["extent"]["temporal"]["interval"][0][0] == "2000-02-18T00:00:00Z"
    # the Aqua twin starts later and is offset by 8 days
    aqua = catalog.get_entry("myd13q1")
    assert aqua["first_doy"] == 9
