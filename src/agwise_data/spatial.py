"""Spatial helpers: bbox subsetting, geometry masking, raster export."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Tuple

import numpy as np
import xarray as xr

Bbox = Tuple[float, float, float, float]  # west, south, east, north


def _axis_indices(vals: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Ascending indices of the cells whose centre is in [lo, hi].

    If none are (the box is smaller than the grid and falls between centres —
    e.g. a sub-degree AOI on the 1° SEAS5 grid), fall back to the single cell
    nearest the box centre, so the selection covers the AOI instead of
    emptying the axis into a degenerate, unwritable cube.
    """
    idx = np.where((vals >= lo) & (vals <= hi))[0]
    if idx.size:
        return idx
    return np.array([int(np.abs(vals - (lo + hi) / 2.0).argmin())])


def subset_bbox(da: xr.DataArray, bbox: Sequence[float], buffer: float = 0.0) -> xr.DataArray:
    """Select the lat/lon box (any latitude order), keeping the covering cell.

    A box smaller than the grid that falls between cell centres would empty an
    axis with a plain slice; each axis then falls back to the nearest covering
    cell so the result is never a degenerate, unwritable cube.
    """
    w, s, e, n = bbox
    w, s, e, n = w - buffer, s - buffer, e + buffer, n + buffer
    lat_name = "lat" if "lat" in da.dims else "latitude"
    lon_name = "lon" if "lon" in da.dims else "longitude"
    lat_idx = _axis_indices(np.asarray(da[lat_name].values), s, n)
    lon_idx = _axis_indices(np.asarray(da[lon_name].values), w, e)
    return da.isel({lat_name: lat_idx, lon_name: lon_idx})


def clip_geometry(da: xr.DataArray, gdf) -> xr.DataArray:
    """Crop + mask to a GeoDataFrame's geometry (terra crop|mask equivalent)."""
    import rioxarray  # noqa: F401  (registers the .rio accessor)

    da = subset_bbox(da, gdf.total_bounds, buffer=0.1)
    da = da.rio.write_crs("EPSG:4326").rio.set_spatial_dims(
        x_dim="lon", y_dim="lat"
    )
    clipped = da.rio.clip(gdf.geometry.values, gdf.crs, drop=True, all_touched=True)
    # rioxarray adds grid_mapping/spatial_ref bookkeeping we don't persist
    clipped.attrs.pop("grid_mapping", None)
    return clipped


def write_geotiff(da: xr.DataArray, path: Path, labels: Optional[list] = None) -> Path:
    """Export a (time, lat, lon) cube as a multi-band GeoTIFF with named bands."""
    import rioxarray  # noqa: F401
    import rasterio

    out = da
    if "time" in out.dims:
        out = out.transpose("time", "lat", "lon")
    out = out.rio.write_crs("EPSG:4326").rio.set_spatial_dims(
        x_dim="lon", y_dim="lat"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    out.rio.to_raster(path)
    if labels:
        with rasterio.open(path, "r+") as dst:
            for i, label in enumerate(labels[: dst.count]):
                dst.set_band_description(i + 1, str(label))
    return path


def points_bbox(lons: np.ndarray, lats: np.ndarray, buffer: float = 0.5) -> Bbox:
    return (
        float(np.min(lons)) - buffer,
        float(np.min(lats)) - buffer,
        float(np.max(lons)) + buffer,
        float(np.max(lats)) + buffer,
    )
