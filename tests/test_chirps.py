"""CHIRPS driver access-path routing (network-free).

When the UCSB HTTP host blocks us (403 -> CogUnavailable), the driver must fall
through to the Earth Engine mirror, and only reach the yearly NetCDF if that
fails too. These tests monkeypatch the three fetch methods to assert the order
without touching the network.
"""

import pytest

from agwise_data import catalog, drivers
import agwise_data.drivers.chirps as chirps_mod
from agwise_data.drivers.chirps import CogUnavailable


def _small_chirps_driver(config, monkeypatch):
    # Force the "small region" branch so COG + GEE are attempted.
    monkeypatch.setattr(chirps_mod, "_bbox_area_deg2", lambda bbox: 0.0)
    entry = catalog.get_entry("chirps")
    return drivers.get_driver(entry, config)


def test_chirps_catalog_has_gee_mirror():
    entry = catalog.get_entry("chirps")
    gee = chirps_mod._access_of_type(entry, "gee")
    assert gee and gee["collection"] == "UCSB-CHG/CHIRPS/DAILY"
    assert gee.get("band") == "precipitation"


def test_cog_block_falls_through_to_gee(config, monkeypatch):
    drv = _small_chirps_driver(config, monkeypatch)

    def blocked_cog(*a, **k):
        raise CogUnavailable("server is rate-limiting us (HTTP 403)")

    def netcdf_forbidden(*a, **k):
        raise AssertionError("must not reach the NetCDF path when GEE works")

    monkeypatch.setattr(drv, "_fetch_year_cog", blocked_cog)
    monkeypatch.setattr(drv, "_fetch_year_gee",
                        lambda *a, **k: ("GEE_DA", {"access": "gee"}))
    monkeypatch.setattr(drv, "_fetch_year_netcdf", netcdf_forbidden)

    da, meta = drv._fetch_year("AGRO.PRCP", 2023, config.domain)
    assert da == "GEE_DA" and meta["access"] == "gee"


def test_gee_failure_falls_through_to_netcdf(config, monkeypatch):
    drv = _small_chirps_driver(config, monkeypatch)

    def blocked_cog(*a, **k):
        raise CogUnavailable("HTTP 403")

    def dead_gee(*a, **k):
        raise RuntimeError("Earth Engine not configured")

    monkeypatch.setattr(drv, "_fetch_year_cog", blocked_cog)
    monkeypatch.setattr(drv, "_fetch_year_gee", dead_gee)
    monkeypatch.setattr(drv, "_fetch_year_netcdf",
                        lambda *a, **k: ("NC_DA", {"access": "netcdf"}))

    da, meta = drv._fetch_year("AGRO.PRCP", 2023, config.domain)
    assert da == "NC_DA" and meta["access"] == "netcdf"


# ---------------------------------------------------------------------------
# CHIRPS v3: a local-only alternative source (source="chirps_v3").


def _write_chirps_v3_year(landing, year=2021):
    import numpy as np
    import pandas as pd
    import xarray as xr

    p = landing / "Rainfall" / "chirps_v3"
    p.mkdir(parents=True)
    times = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    lat = np.arange(-2.0, 0.001, 0.5)     # ascending, like the real files
    lon = np.arange(29.0, 31.001, 0.5)
    data = np.full((len(times), len(lat), len(lon)), 3.5, dtype="float32")
    ds = xr.Dataset(
        {"precip": (("time", "latitude", "longitude"), data)},
        coords={"time": times, "latitude": lat, "longitude": lon},
    )
    ds.to_netcdf(p / f"{year}.nc")


def test_chirps_v3_is_selectable_and_not_default():
    assert catalog.source_for("PRCP", "chirps_v3") == "chirps_v3"
    assert catalog.source_for("PRCP") == "chirps"        # default unchanged


def test_chirps_v3_reads_the_local_year(tmp_path, config):
    import xarray as xr

    landing = tmp_path / "landing"
    _write_chirps_v3_year(landing, 2021)
    config.local_root = landing
    config.register_domain("rw", [28.0, -3.0, 32.0, 1.0])
    drv = drivers.get_driver(catalog.get_entry("chirps_v3"), config)

    dest = drv.ensure_daily_year("AGRO.PRCP", 2021, "rw")
    with xr.open_dataarray(dest) as da:
        assert da.sizes["time"] == 365
        assert abs(float(da.mean()) - 3.5) < 1e-6        # mm/day, no conversion


def test_chirps_v3_without_local_raises_clear_error(config):
    assert config.local_root is None
    drv = drivers.get_driver(catalog.get_entry("chirps_v3"), config)
    with pytest.raises(RuntimeError, match="AGWISE_LOCAL_ROOT"):
        drv.ensure_daily_year("AGRO.PRCP", 1999, "africa")
