"""Tests for the spatial scaffolding: make_grid + tag_admin.

Network-free: the geoBoundaries lookup (boundaries.load_geometry) is
monkeypatched to return synthetic polygons, so the grid maths, the
within-boundary clip, and the point-in-polygon admin tagging are all exercised
offline. The two admin squares are West = [0,1]x[0,1], East = [1,2]x[0,1].
"""

import numpy as np
import pandas as pd
import pytest

from agwise_data import boundaries
from agwise_data.api import _grid_points, make_grid, tag_admin


def _fake_geoms():
    import geopandas as gpd
    from shapely.geometry import box

    country = gpd.GeoDataFrame(
        {"shapeName": ["Country"]}, geometry=[box(0, 0, 2, 1)], crs="EPSG:4326"
    )
    adm1 = gpd.GeoDataFrame(
        {"shapeName": ["West", "East"]},
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326",
    )
    return country, adm1


@pytest.fixture()
def fake_boundaries(monkeypatch):
    country, adm1 = _fake_geoms()

    def fake_load(config, ctry, level=0, admin_name=None):
        if level == 0:
            return country
        if level == 1:
            return adm1
        raise ValueError(f"ADM{level} not available (synthetic)")

    monkeypatch.setattr(boundaries, "load_geometry", fake_load)
    # iso3("RWA") resolves offline (3 upper letters), no pycountry/network


# --------------------------------------------------------------------------
# grid maths
# --------------------------------------------------------------------------
def test_grid_points_spacing_and_bounds():
    lons, lats = _grid_points((0.0, 0.0, 2.0, 2.0), res_km=55.66)  # ~0.5 deg
    ulat = np.unique(lats)
    assert np.allclose(np.diff(ulat), 0.5, atol=1e-3)
    assert lons.min() >= 0.0 and lons.max() <= 2.0 + 1e-6
    assert lats.min() >= 0.0 and lats.max() <= 2.0 + 1e-6


def test_grid_points_bad_res():
    with pytest.raises(ValueError, match="res_km"):
        _grid_points((0, 0, 1, 1), res_km=0)


# --------------------------------------------------------------------------
# make_grid
# --------------------------------------------------------------------------
def test_make_grid_bbox_mode_no_network():
    df = make_grid(bbox=[0.0, 0.0, 1.0, 1.0], res_km=55.66)
    assert list(df.columns) == ["lon", "lat"]  # no country/admin without a country
    assert (df["lon"].between(0, 1)).all() and (df["lat"].between(0, 1)).all()
    assert len(df) > 0


def test_make_grid_country_clips_and_tags(fake_boundaries):
    df = make_grid(country="RWA", res_km=55.66, tag_admin_level=2)
    # clipped inside the country box [0,2]x[0,1]
    assert (df["lon"].between(0, 2)).all() and (df["lat"].between(0, 1)).all()
    assert (df["country"] == "RWA").all()
    # NAME_1 assigned by which square each point falls in
    assert set(df["NAME_1"].dropna().unique()) <= {"West", "East"}
    west = df[df["lon"] < 1.0]
    assert (west["NAME_1"] == "West").all()
    # ADM2 is unavailable -> column present but all None (graceful)
    assert "NAME_2" in df.columns and df["NAME_2"].isna().all()


def test_make_grid_needs_country_or_bbox():
    with pytest.raises(ValueError, match="country=... or bbox"):
        make_grid()


# --------------------------------------------------------------------------
# tag_admin
# --------------------------------------------------------------------------
def test_tag_admin_assigns_names(fake_boundaries):
    pts = pd.DataFrame({"lon": [0.5, 1.5, 5.0], "lat": [0.5, 0.5, 5.0]})
    with pytest.warns(UserWarning, match="ADM2"):
        out = tag_admin(pts, country="RWA", admin_level=2)
    assert list(out["NAME_1"].iloc[:2]) == ["West", "East"]
    assert pd.isna(out["NAME_1"].iloc[2])  # point outside both squares
    assert (out["country"] == "RWA").all()
    assert out["NAME_2"].isna().all()  # ADM2 missing -> all None


def test_tag_admin_level1_only(fake_boundaries):
    pts = pd.DataFrame({"lon": [0.5], "lat": [0.5]})
    out = tag_admin(pts, country="RWA", admin_level=1)
    assert out.loc[0, "NAME_1"] == "West"
    assert "NAME_2" not in out.columns
