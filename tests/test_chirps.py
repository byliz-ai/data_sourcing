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
