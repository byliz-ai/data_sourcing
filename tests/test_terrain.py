"""Unit tests of the terrain derivatives on synthetic planes."""

import numpy as np
import pytest
import xarray as xr

from agwise_data import terrain
from agwise_data.terrain import _M_PER_DEG


def plane(dz_dlat=0.0, dz_dlon=0.0, base=1000.0):
    """Elevation plane on a 0.001° grid near the equator (cos(lat) ≈ 1)."""
    lats = np.arange(0.0, 0.05, 0.001)
    lons = np.arange(30.0, 30.05, 0.001)
    z = base + dz_dlat * lats[:, None] + dz_dlon * lons[None, :]
    return xr.DataArray(
        z.astype("float64"),
        coords={"lat": lats, "lon": lons},
        dims=("lat", "lon"),
        name="ELEV",
    )


def interior(da):
    return da.isel(lat=slice(1, -1), lon=slice(1, -1)).values


def test_slope_of_flat_plane_is_zero():
    assert interior(terrain.slope(plane())) == pytest.approx(0.0)


def test_slope_of_north_tilt_matches_analytic():
    dz_dlat = 1113.2  # m per degree → gradient of 0.01 m/m
    expected = np.degrees(np.arctan(dz_dlat / _M_PER_DEG))
    out = interior(terrain.slope(plane(dz_dlat=dz_dlat)))
    assert out == pytest.approx(expected, rel=1e-4)  # float32 gradients


def test_aspect_points_downslope():
    # rises to the north → water flows south (180°)
    assert interior(terrain.aspect(plane(dz_dlat=500.0))) == pytest.approx(180.0)
    # rises to the east → downslope west (270°)
    assert interior(terrain.aspect(plane(dz_dlon=500.0))) == pytest.approx(270.0)
    # flat → aspect undefined
    assert np.isnan(interior(terrain.aspect(plane()))).all()


def test_aspect_descending_latitude_grid_consistent():
    tilted = plane(dz_dlat=500.0)
    flipped = tilted.isel(lat=slice(None, None, -1))  # descending lat order
    assert interior(terrain.aspect(flipped)) == pytest.approx(180.0)


def test_tpi_zero_on_plane_and_positive_on_peak():
    assert interior(terrain.tpi(plane(dz_dlat=500.0))) == pytest.approx(0.0)
    bumpy = plane()
    bumpy[5, 5] = 1100.0  # a peak sits above its neighborhood
    assert float(terrain.tpi(bumpy)[5, 5]) == pytest.approx(100.0)


def test_tri_measures_roughness():
    assert float(terrain.tri(plane()).max()) == pytest.approx(0.0)
    bumpy = plane()
    bumpy[5, 5] = 1100.0
    assert float(terrain.tri(bumpy)[5, 5]) == pytest.approx(100.0)
