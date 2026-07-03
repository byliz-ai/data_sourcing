"""End-to-end tests of the static (soil/DEM) layer over the fake driver."""

import numpy as np
import pandas as pd
import pytest

from agwise_data.api import extract_static_points, get_static
from agwise_data.cache import read_manifest

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain


def test_get_static_elevation_product(config):
    res = get_static(
        variables="ELEV", bbox=BBOX, source="fake_static", config=config
    )
    info = res["TOPO.ELEV"]
    assert info["nc"].exists()
    da = info["data"]
    assert set(da.dims) == {"lat", "lon"}
    # synthetic elevation = lat*100 + lon at every grid node
    v = float(da.sel(lat=0.0, lon=34.0, method="nearest"))
    assert v == pytest.approx(34.0)
    meta = read_manifest(info["nc"])
    assert meta["source_id"] == "fake_static"
    assert meta["variable"] == "TOPO.ELEV"


def test_static_cache_hit(config):
    from tests.conftest import fake_static_calls

    kwargs = dict(variables="ELEV", bbox=BBOX, source="fake_static", config=config)
    get_static(**kwargs)
    n = len(fake_static_calls())
    res2 = get_static(**kwargs)  # second call: no new fetches
    assert len(fake_static_calls()) == n
    assert res2["TOPO.ELEV"]["nc"].exists()


def test_soil_depth_dimension_conversion_and_subset(config):
    res = get_static(
        variables="CLAY", bbox=BBOX, source="fake_static", config=config
    )
    da = res["SOIL.CLAY"]["data"]
    assert list(da.dims) == ["depth", "lat", "lon"]
    depths = [str(d) for d in da["depth"].values]
    assert depths == ["0-5cm", "5-15cm", "15-30cm"]
    # raw fake layer = (index+1)*100, conversion d10 → (index+1)*10
    assert float(da.isel(depth=1, lat=0, lon=0)) == pytest.approx(20.0)

    # depth subset: separate product file, correct single layer, no refetch
    from tests.conftest import fake_static_calls

    n = len(fake_static_calls())
    sub = get_static(
        variables="CLAY", bbox=BBOX, depths=["5-15cm"],
        source="fake_static", config=config,
    )
    assert len(fake_static_calls()) == n  # all depths were already cached
    sub_da = sub["SOIL.CLAY"]["data"]
    assert sub_da.sizes["depth"] == 1
    assert sub["SOIL.CLAY"]["nc"] != res["SOIL.CLAY"]["nc"]
    assert float(sub_da.isel(depth=0, lat=0, lon=0)) == pytest.approx(20.0)


def test_unknown_depth_rejected(config):
    with pytest.raises(ValueError, match="Unknown depths"):
        get_static(
            variables="CLAY", bbox=BBOX, depths=["3-7cm"],
            source="fake_static", config=config,
        )


def test_derived_slope_fetches_elevation_once(config):
    from tests.conftest import fake_static_calls

    res = get_static(
        variables=["ELEV", "SLOPE", "ASPECT"], bbox=BBOX,
        source="fake_static", config=config,
    )
    fetched = [v for v, _ in fake_static_calls()]
    assert fetched.count("TOPO.ELEV") == 1  # derivatives reuse the cached DEM
    slope = res["TOPO.SLOPE"]["data"]
    # fake elevation is a tilted plane rising north: slope > 0 everywhere
    interior = slope.isel(lat=slice(1, -1), lon=slice(1, -1))
    assert float(interior.min()) > 0
    meta = read_manifest(res["TOPO.SLOPE"]["nc"])
    assert meta["variable"] == "TOPO.SLOPE"


def test_extract_static_points_wide_columns(config):
    pts = pd.DataFrame(
        {"lon": [34.0, 36.5], "lat": [0.0, 1.0], "site": ["a", "b"]}
    )
    out = extract_static_points(
        pts, ["ELEV", "CLAY"], source="fake_static", config=config
    )
    assert list(out["site"]) == ["a", "b"]
    assert out["ELEV"].tolist() == pytest.approx([34.0, 136.5])
    for di, depth in enumerate(["0_5cm", "5_15cm", "15_30cm"]):
        col = f"CLAY_{depth}"
        assert col in out.columns
        assert out[col].tolist() == pytest.approx([(di + 1) * 10.0] * 2)


def test_extract_static_points_far_apart_uses_cells(config):
    # bbox area > 4 deg² → per-cell windows; values must still be exact
    pts = pd.DataFrame({"lon": [31.0, 41.0], "lat": [-4.0, 4.0]})
    out = extract_static_points(
        pts, "ELEV", source="fake_static", config=config
    )
    assert out["ELEV"].tolist() == pytest.approx([-369.0, 441.0])


def test_extract_static_points_invalid_coords(config):
    pts = pd.DataFrame({"lon": [34.0, np.nan], "lat": [0.0, 1.0]})
    out = extract_static_points(pts, "ELEV", source="fake_static", config=config)
    assert out["ELEV"].iloc[0] == pytest.approx(34.0)
    assert np.isnan(out["ELEV"].iloc[1])
