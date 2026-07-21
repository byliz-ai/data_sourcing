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


# ---------------------------------------------------------------------------
# Driver-level fallback: a staged file that is unreadable or truncated must
# not fail the call — ensure_daily_year logs a warning and downloads instead.


def _local_fake_driver(config, landing):
    """A FakeDriver whose entry maps AGRO.TMAX to a legacy landing path."""
    from agwise_data.drivers import _REGISTRY

    from tests.conftest import FAKE_ENTRY

    config.local_root = landing
    entry = {
        **FAKE_ENTRY,
        "access": FAKE_ENTRY["access"]
        + [{"type": "local", "role": "alternative",
            "paths": {"AGRO.TMAX": "TemperatureMax/AgEra/{year}.nc"}}],
    }
    return _REGISTRY["fake"](entry, config)


def _write_full_legacy_tmax(landing, year, n_days=None):
    p = landing / "TemperatureMax" / "AgEra"
    p.mkdir(parents=True, exist_ok=True)
    times = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")[:n_days]
    lat = np.arange(-5.0, 5.001, 0.5)
    lon = np.arange(30.0, 42.001, 0.5)
    data = np.full((len(times), len(lat), len(lon)), 298.0)  # K -> 24.85 C
    ds = xr.Dataset(
        {str(year): (("time", "latitude", "longitude"), data)},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    ds["crs"] = 0
    ds.to_netcdf(p / f"{year}.nc")


def test_driver_uses_good_local_file_without_downloading(tmp_path, config):
    from tests.conftest import fake_calls

    landing = tmp_path / "landing"
    _write_full_legacy_tmax(landing, 2021)
    driver = _local_fake_driver(config, landing)

    dest = driver.ensure_daily_year("AGRO.TMAX", 2021, "africa")
    assert fake_calls() == []                      # no download happened
    with xr.open_dataarray(dest) as da:
        assert da.sizes["time"] == 365
        assert abs(float(da.max()) - 24.85) < 0.1  # k_to_degc applied


def test_driver_falls_back_when_local_file_unreadable(tmp_path, config):
    """Garbage bytes where a netCDF should be -> warn + download, no crash."""
    from tests.conftest import fake_calls

    landing = tmp_path / "landing"
    bad = landing / "TemperatureMax" / "AgEra" / "2021.nc"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"this is not a netcdf file")
    driver = _local_fake_driver(config, landing)

    dest = driver.ensure_daily_year("AGRO.TMAX", 2021, "africa")
    assert fake_calls() == [("AGRO.TMAX", 2021, "africa")]  # downloaded
    assert dest.exists()


def test_driver_falls_back_when_local_year_incomplete(tmp_path, config):
    """A truncated past year (10 days) -> warn + download, not RuntimeError."""
    from tests.conftest import fake_calls

    landing = tmp_path / "landing"
    _write_full_legacy_tmax(landing, 2021, n_days=10)
    driver = _local_fake_driver(config, landing)

    dest = driver.ensure_daily_year("AGRO.TMAX", 2021, "africa")
    assert fake_calls() == [("AGRO.TMAX", 2021, "africa")]  # downloaded
    with xr.open_dataarray(dest) as da:
        assert da.sizes["time"] == 365                      # the full year


def test_static_driver_falls_back_when_local_tif_unreadable(tmp_path, config):
    """Garbage bytes where soil GeoTIFFs should be -> warn + download."""
    from agwise_data.drivers import _REGISTRY

    from tests.conftest import FAKE_DEPTHS, FAKE_STATIC_ENTRY, fake_static_calls

    landing = tmp_path / "landing"
    prof = landing / "Soil" / "soilGrids" / "profile"
    prof.mkdir(parents=True)
    for depth in FAKE_DEPTHS:
        (prof / f"clay_{depth}_mean_30s.tif").write_bytes(b"not a tif")
    config.local_root = landing
    entry = {
        **FAKE_STATIC_ENTRY,
        "access": FAKE_STATIC_ENTRY["access"]
        + [{"type": "local", "role": "alternative", "preconverted": True,
            "path": "Soil/soilGrids/profile/{var}_{depth}_mean_30s.tif"}],
    }
    driver = _REGISTRY["fake_static"](entry, config)

    dest = driver.ensure_static("SOIL.CLAY", "africa")
    assert fake_static_calls() == [("SOIL.CLAY", "africa")]  # downloaded
    assert dest.exists()


def test_modis_driver_falls_back_when_local_composites_unreadable(tmp_path, config):
    """A corrupt local composite tif -> warn + Earth Engine, no crash."""
    from agwise_data.drivers import _REGISTRY

    from tests.conftest import FAKE_MODIS_TERRA_ENTRY, fake_modis_calls

    landing = tmp_path / "landing"
    tiles = landing / "modis" / "africa"
    tiles.mkdir(parents=True)
    (tiles / "NDVI_2021_001.tif").write_bytes(b"not a tif")
    config.local_root = landing
    entry = {
        **FAKE_MODIS_TERRA_ENTRY,
        "access": FAKE_MODIS_TERRA_ENTRY["access"]
        + [{"type": "local", "role": "alternative",
            "composite_path": "modis/{domain}/{short}_{year}_{doy}.tif"}],
    }
    driver = _REGISTRY["fake_modis"](entry, config)

    dest = driver.ensure_composite_year("RS.NDVI", 2021, "africa")
    assert len(fake_modis_calls()) == 1                     # fell back to GEE
    assert dest.exists()


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


def test_dem_driver_reads_staged_local_tiles(tmp_path, config):
    """Staged GLO-30 tiles are used tile-by-tile before the AWS URL."""
    from agwise_data.catalog import get_entry
    from agwise_data.drivers.dem import CopDem30Driver

    tiles = tmp_path / "cop30"
    # The bbox sits inside one 1-degree tile (SW corner S01/E036): with the
    # staged tile present, no AWS request is needed at all.
    _write_tif(
        tiles / "Copernicus_DSM_COG_10_S01_00_E036_00_DEM.tif",
        np.full((100, 100), 1500.0), west=36.0, north=0.0, res=0.01,
    )
    entry = dict(get_entry("cop_dem30"))
    # replace the shipped local block (it points at the real CGLabs tile set)
    entry["access"] = [
        b for b in entry["access"] if b.get("type") != "local"
    ] + [{
        "type": "local", "role": "alternative",
        "tile_root": str(tiles),
        "tile_pattern":
            "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif",
    }]
    config.local_root = tmp_path  # local reuse on
    config.register_domain("nak", [36.2, -0.8, 36.8, -0.2])

    da, meta = CopDem30Driver(entry, config)._fetch_static("TOPO.ELEV", "nak")
    assert meta["n_tiles"] == 1 and meta["n_local_tiles"] == 1
    assert meta["access"] == "local+cog" and meta["n_missing_tiles"] == 0
    assert abs(float(da.mean()) - 1500.0) < 1e-6


def test_isda_is_a_selectable_soil_source():
    """source='isda' routes soil to iSDA; SoilGrids stays the default."""
    import pytest

    from agwise_data.catalog import static_source_for

    assert static_source_for("CLAY", "isda") == "isda"
    assert static_source_for("CLAY") == "soilgrids"          # default unchanged
    with pytest.raises(ValueError):                          # iSDA omits nitrogen
        static_source_for("NITROGEN", "isda")


def test_local_isda_applies_conversion(tmp_path, config):
    """iSDA (unlike the preconverted SoilGrids profile) applies the catalog
    conversion: db.od is stored x100, so d100 -> g/cm3."""
    from agwise_data.drivers.local import fetch_local_static

    landing = tmp_path / "landing"
    entry = get_entry("isda")
    for depth in entry["depths"]:
        _write_tif(
            landing / "Soil" / "iSDA" / f"isda_db.od_{depth}_v0.13_30s.tif",
            np.full((5, 5), 120.0),
        )
    config.local_root = landing
    config.register_domain("rw", [28.0, -0.5, 29.0, 1.0])

    da, meta = fetch_local_static(config, entry, "isda", "SOIL.BDOD", "rw")
    assert meta["access"] == "local" and da.sizes["depth"] == 2
    assert abs(float(da.isel(depth=0).mean()) - 1.2) < 0.01  # 120 / 100
