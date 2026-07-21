"""subset_bbox: normal slicing, latitude order, and the sub-cell rescue."""

import numpy as np
import xarray as xr

from agwise_data.spatial import subset_bbox


def _grid(lats, lons):
    data = np.arange(len(lats) * len(lons), dtype="float32").reshape(
        len(lats), len(lons)
    )
    return xr.DataArray(
        data, coords={"lat": lats, "lon": lons}, dims=("lat", "lon"), name="v"
    )


def test_subset_keeps_cells_with_centre_in_box():
    da = _grid(np.arange(-2.0, 2.01, 0.5), np.arange(30.0, 34.01, 0.5))
    out = subset_bbox(da, [31.0, -1.0, 32.0, 1.0])
    assert out["lat"].min() >= -1.0 and out["lat"].max() <= 1.0
    assert out["lon"].min() >= 31.0 and out["lon"].max() <= 32.0
    assert out.sizes["lat"] > 0 and out.sizes["lon"] > 0


def test_subset_descending_latitude():
    da = _grid(np.arange(2.0, -2.01, -0.5), np.arange(30.0, 34.01, 0.5))
    out = subset_bbox(da, [31.0, -1.0, 32.0, 1.0])
    # same cells regardless of stored latitude order
    assert set(np.round(out["lat"].values, 3)) <= {-1.0, -0.5, 0.0, 0.5, 1.0}
    assert out.sizes["lat"] == 5


def test_subset_subcell_aoi_falls_back_to_nearest_cell():
    """An AOI smaller than the grid, falling between centres (as a sub-degree
    box on the 1-degree SEAS5 grid), must return the covering cell — not empty."""
    da = _grid(np.array([8.5, 9.5]), np.array([37.5, 38.5, 39.5]))  # 1-degree grid
    out = subset_bbox(da, [38.35, 8.7, 39.2, 9.35])  # between all centres
    assert out.sizes["lat"] == 1 and out.sizes["lon"] == 1   # not 0 (was the bug)
    assert float(out["lat"][0]) in (8.5, 9.5)
    assert float(out["lon"][0]) in (38.5, 39.5)


def test_subset_box_entirely_outside_grid_returns_nearest():
    da = _grid(np.array([8.5, 9.5]), np.array([37.5, 38.5, 39.5]))
    out = subset_bbox(da, [50.0, 20.0, 51.0, 21.0])  # far away
    assert out.sizes["lat"] == 1 and out.sizes["lon"] == 1
    assert float(out["lat"][0]) == 9.5 and float(out["lon"][0]) == 39.5  # nearest corner
