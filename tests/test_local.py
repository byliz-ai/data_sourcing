"""Local source adapter (network-free): reuse legacy Landing files.

Builds a tiny AgERA5-style legacy file with the quirks the real ones have — the
data variable named after the year, an extra `crs` variable, Kelvin units, and
an out-of-order time axis — and checks the adapter reads it, clips to the
region, sorts time, and applies the catalog's unit conversion.
"""

import numpy as np
import pandas as pd
import xarray as xr

from agwise_data.catalog import get_entry
from agwise_data.drivers.local import fetch_local_year, local_file


def _write_legacy_tmax(landing, year=2020):
    p = landing / "TemperatureMax" / "AgEra"
    p.mkdir(parents=True)
    # Out-of-order time (a real file started mid-year), 3x3 grid over Rwanda.
    times = pd.to_datetime([f"{year}-07-01", f"{year}-01-01", f"{year}-01-02"])
    lat = np.array([-2.0, -1.0, 0.0])
    lon = np.array([29.0, 30.0, 31.0])
    data = np.full((3, 3, 3), 298.0)  # 298 K -> 24.85 C
    ds = xr.Dataset(
        {str(year): (("time", "latitude", "longitude"), data)},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    ds["crs"] = 0  # CRS placeholder the adapter must ignore
    ds.to_netcdf(p / f"{year}.nc")


def test_local_disabled_by_default(config):
    """No local_root -> adapter is inert (drivers download as before)."""
    entry = get_entry("agera5")
    assert config.local_root is None
    assert local_file(config, entry, "AGRO.TMAX", 2020) is None
    assert fetch_local_year(config, entry, "agera5", "AGRO.TMAX", 2020, "africa") is None


def test_local_reads_clips_sorts_and_converts(tmp_path, config):
    landing = tmp_path / "landing"
    _write_legacy_tmax(landing)
    config.local_root = landing
    config.register_domain("rw", [28.0, -3.0, 32.0, 1.0])

    entry = get_entry("agera5")
    da, meta = fetch_local_year(config, entry, "agera5", "AGRO.TMAX", 2020, "rw")

    assert meta["access"] == "local" and meta["source_file"].endswith("2020.nc")
    # time sorted ascending
    tt = da["time"].values.astype("datetime64[ns]").astype("int64")
    assert bool((np.diff(tt) > 0).all())
    # Kelvin -> Celsius applied via the catalog conversion (298 K -> ~24.85 C)
    assert abs(float(da.max()) - 24.85) < 0.1
    # the `crs`/year-named-variable quirks did not leak a second variable
    assert da.name == "2m_temperature"


def test_local_missing_file_falls_back(tmp_path, config):
    """local_root set but no file for that year -> None (caller downloads)."""
    config.local_root = tmp_path / "empty_landing"
    entry = get_entry("agera5")
    assert fetch_local_year(config, entry, "agera5", "AGRO.TMAX", 1999, "africa") is None


def _write_tif(path, data, west=28.0, north=1.0, res=0.1, nodata=None):
    import rasterio
    from rasterio.transform import from_origin

    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(west, north, res, res), nodata=nodata,
    ) as dst:
        dst.write(data.astype("float32"), 1)


def test_local_static_soil_reads_depth_stack_preconverted(tmp_path, config):
    from agwise_data.drivers.local import fetch_local_static

    landing = tmp_path / "landing"
    entry = get_entry("soilgrids")
    depths = entry["depths"]
    # One physical-units GeoTIFF per depth (clay ~30 %, NOT the raw x10 WCS int).
    for i, depth in enumerate(depths):
        _write_tif(
            landing / "Soil" / "soilGrids" / "profile" / f"clay_{depth}_mean_30s.tif",
            np.full((5, 5), 30.0 + i),
        )
    config.local_root = landing
    config.register_domain("rw", [28.0, -0.5, 29.0, 1.0])

    da, meta = fetch_local_static(config, entry, "soilgrids", "SOIL.CLAY", "rw")
    assert meta["access"] == "local"
    assert da.sizes["depth"] == len(depths)
    # preconverted: value stays ~30 (no /10 conversion applied)
    assert abs(float(da.isel(depth=0).mean()) - 30.0) < 0.5


def test_local_composite_modis_stacks_scales_and_masks(tmp_path, config):
    from agwise_data.drivers.local import fetch_local_composite

    landing = tmp_path / "landing"
    entry = get_entry("mod13q1")
    # Per-composite NDVI tifs, domain-tagged; raw int16*1e4. One fill pixel.
    raw = np.full((5, 5), 5000.0)   # -> NDVI 0.5
    raw[0, 0] = -3000               # fill_value -> NaN
    for doy in (1, 17, 9):          # out of order on purpose
        _write_tif(landing / "modis" / "rw" / f"NDVI_2021_{doy:03d}.tif", raw)
    config.local_root = landing
    config.register_domain("rw", [28.0, -0.5, 29.0, 1.0])

    da, meta = fetch_local_composite(config, entry, "mod13q1", "RS.NDVI", 2021, "rw")
    assert meta["access"] == "local" and meta["n_composites"] == 3
    tt = da["time"].values.astype("datetime64[ns]").astype("int64")
    assert bool((np.diff(tt) > 0).all())          # sorted by composite date
    assert abs(float(da.max()) - 0.5) < 1e-6        # d10000 applied (5000 -> 0.5)
    assert bool(np.isnan(da.isel(time=0, lat=0, lon=0)))  # fill -> NaN
