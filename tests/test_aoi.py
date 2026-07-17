"""Tests for the user-uploaded AOI (area of interest) region selector."""

import json

import geopandas as gpd
import pytest
from shapely.geometry import box, mapping

from agwise_data import boundaries
from agwise_data.api import get_climate, get_static, make_grid

# A rectangle well inside the fake sources' domain (FAKE_BBOX = 30,-5,42,5).
AOI = box(34.0, -1.0, 37.0, 1.0)


def _write_geojson(path, geom):
    fc = {"type": "FeatureCollection",
          "features": [{"type": "Feature", "properties": {},
                        "geometry": mapping(geom)}]}
    path.write_text(json.dumps(fc))
    return str(path)


# --- loader --------------------------------------------------------------

def test_load_aoi_from_shapely_geojson_dict_and_file(tmp_path):
    g1 = boundaries.load_aoi(AOI)
    assert g1.crs.to_epsg() == 4326 and len(g1) >= 1
    assert boundaries.geometry_bbox(g1) == pytest.approx((34.0, -1.0, 37.0, 1.0))

    g2 = boundaries.load_aoi(mapping(AOI))          # bare GeoJSON geometry
    assert g2.crs.to_epsg() == 4326

    path = _write_geojson(tmp_path / "aoi.geojson", AOI)
    g3 = boundaries.load_aoi(path)                   # a file the user uploads
    assert boundaries.geometry_bbox(g3) == pytest.approx((34.0, -1.0, 37.0, 1.0))


def test_load_aoi_reprojects_to_4326():
    mercator = gpd.GeoDataFrame(geometry=[AOI], crs="EPSG:4326").to_crs(3857)
    g = boundaries.load_aoi(mercator)
    assert g.crs.to_epsg() == 4326
    assert boundaries.geometry_bbox(g) == pytest.approx((34.0, -1.0, 37.0, 1.0), abs=1e-6)


def test_load_aoi_rejects_junk():
    with pytest.raises(TypeError):
        boundaries.load_aoi(12345)


def test_aoi_tag_stable_and_distinct():
    a = boundaries.load_aoi(box(34, -1, 37, 1))
    b = boundaries.load_aoi(box(34, -1, 37, 1))
    c = boundaries.load_aoi(box(34, -1, 36, 1))
    assert boundaries.aoi_tag(a) == boundaries.aoi_tag(b)   # same shape -> same tag
    assert boundaries.aoi_tag(a) != boundaries.aoi_tag(c)   # different shape -> different tag
    assert boundaries.aoi_tag(a, "myzone.geojson").startswith("aoi_myzone_")


# --- end to end over the fake drivers ------------------------------------

def test_get_climate_with_aoi_clips_and_tags(config, tmp_path):
    path = _write_geojson(tmp_path / "zone.geojson", AOI)
    res = get_climate("PRCP", years=2020, source="fake", geometry=path, config=config)
    info = res["AGRO.PRCP"]
    da = info["data"]
    # the product path carries the readable AOI tag
    assert "aoi_zone_" in str(info["nc"])
    # clipped to the polygon, not the whole fake domain (which spans 30..42 lon)
    assert float(da.lon.min()) >= 33.9 and float(da.lon.max()) <= 37.1
    assert float(da.lat.min()) >= -1.1 and float(da.lat.max()) <= 1.1
    assert da.sizes["lon"] < 25   # full FAKE_BBOX would be ~25 lons at 0.5deg


def test_get_static_with_aoi(config):
    res = get_static("CLAY", source="fake_static", geometry=AOI, config=config)
    da = res["SOIL.CLAY"]["data"]
    assert float(da.lon.max()) <= 37.1 and float(da.lat.max()) <= 1.1


def test_make_grid_with_aoi_clips(config):
    grid = make_grid(geometry=AOI, res_km=50, config=config)
    assert len(grid) > 0
    assert grid["lon"].between(33.9, 37.1).all()
    assert grid["lat"].between(-1.1, 1.1).all()
