"""Terrain derivatives from a geographic (lat/lon) elevation grid.

Slope, aspect, TPI and TRI computed with numpy on an elevation DataArray in
EPSG:4326 — the same indices the legacy AgWise scripts took from
``terra::terrain``. Horizontal distances are converted from degrees to
meters per row (longitude spacing shrinks with cos(lat)) so slopes are
correct anywhere on the globe, not just at the equator.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

_M_PER_DEG = 111_320.0  # meters per degree of latitude (spherical Earth)


def _spacing_m(da: xr.DataArray):
    """(dy_m, dx_m_per_row): grid spacing in meters; dx varies with latitude."""
    lats = da["lat"].values
    lons = da["lon"].values
    dy = float(np.abs(np.diff(lats).mean())) * _M_PER_DEG
    dx_deg = float(np.abs(np.diff(lons).mean()))
    dx = dx_deg * _M_PER_DEG * np.cos(np.deg2rad(lats))
    return dy, dx


def slope(elev: xr.DataArray) -> xr.DataArray:
    """Slope in degrees, dims (lat, lon)."""
    dzdy, dzdx = _gradients(elev)
    out = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return elev.copy(data=out.astype("float32"))


def aspect(elev: xr.DataArray) -> xr.DataArray:
    """Aspect in degrees clockwise from north (flat cells → NaN)."""
    dzdy, dzdx = _gradients(elev)
    # downslope direction: gradient points uphill, so negate both components
    out = np.degrees(np.arctan2(-dzdx, -dzdy))  # 0 = north, 90 = east
    out = np.mod(out, 360.0)
    flat = (dzdx == 0) & (dzdy == 0)
    out[flat] = np.nan
    return elev.copy(data=out.astype("float32"))


def _gradients(elev: xr.DataArray):
    """(dz/dy, dz/dx) in m/m on the geographic grid, north-up.

    Computed in float32: a 30 m DEM over a cache domain is easily 100+ Mpx,
    and float64 intermediates put a multi-GB spike on every derivative
    (get_static runs several in parallel). Elevation differences of
    neighbouring cells are well within float32 resolution.
    """
    if list(elev.dims) != ["lat", "lon"]:
        elev = elev.transpose("lat", "lon")
    z = elev.values.astype("float32")
    dy, dx = _spacing_m(elev)
    lat_ascending = float(elev["lat"][0]) < float(elev["lat"][-1])
    dzdy = np.gradient(z, axis=0) / np.float32(dy)
    if not lat_ascending:
        dzdy = -dzdy
    dzdx = np.gradient(z, axis=1) / dx[:, None].astype("float32")
    return dzdy, dzdx


def _neighbor_stack(z: np.ndarray) -> np.ndarray:
    """(8, H, W) stack of the 8 neighbors, edge-padded."""
    p = np.pad(z, 1, mode="edge")
    return np.stack(
        [
            p[i : i + z.shape[0], j : j + z.shape[1]]
            for i in (0, 1, 2)
            for j in (0, 1, 2)
            if not (i == 1 and j == 1)
        ]
    )


def tpi(elev: xr.DataArray) -> xr.DataArray:
    """Topographic position index: cell minus mean of its 8 neighbors."""
    z = elev.values.astype("float32")
    out = z - _neighbor_stack(z).mean(axis=0, dtype="float32")
    return elev.copy(data=out.astype("float32"))


def tri(elev: xr.DataArray) -> xr.DataArray:
    """Terrain ruggedness index: mean |cell - neighbor| over the 8 neighbors."""
    z = elev.values.astype("float32")
    out = np.abs(_neighbor_stack(z) - z).mean(axis=0, dtype="float32")
    return elev.copy(data=out.astype("float32"))


DERIVATIVES = {
    "TOPO.SLOPE": slope,
    "TOPO.ASPECT": aspect,
    "TOPO.TPI": tpi,
    "TOPO.TRI": tri,
}
