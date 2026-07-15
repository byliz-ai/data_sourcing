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
