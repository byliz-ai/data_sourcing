"""Test fixtures: an isolated data root and a fake driver/catalog entry.

The fake driver generates deterministic synthetic daily data locally, so
the whole pipeline (harmonize → cache → products → extraction) is tested
without any network access or credentials.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data import catalog
from agwise_data.config import Config
from agwise_data.drivers import register
from agwise_data.drivers.base import Driver
from agwise_data.harmonize import apply_conversion

FAKE_BBOX = [30.0, -5.0, 42.0, 5.0]  # around Kenya


def synthetic_year(year: int, source_name: str = "precip") -> xr.DataArray:
    """Daily cube whose values encode the day of year, for easy assertions."""
    times = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    lats = np.arange(FAKE_BBOX[1], FAKE_BBOX[3] + 0.001, 0.5)
    lons = np.arange(FAKE_BBOX[0], FAKE_BBOX[2] + 0.001, 0.5)
    doy = times.dayofyear.values.astype("float32")
    data = np.broadcast_to(
        doy[:, None, None], (len(times), len(lats), len(lons))
    ).copy()
    return xr.DataArray(
        data,
        coords={"time": times, "latitude": lats, "longitude": lons},
        dims=("time", "latitude", "longitude"),
        name=source_name,
    )


@register("fake")
class FakeDriver(Driver):
    calls: list = []  # class-level: records fetches to assert cache hits

    def _fetch_year(self, variable: str, year: int, domain: str):
        FakeDriver.calls.append((variable, year, domain))
        spec = self.entry["variables"][variable]
        da = synthetic_year(year, spec.get("source_name", "value"))
        da = apply_conversion(da, spec.get("conversion"))
        return da, {"source_url": f"fake://{variable}/{year}"}


FAKE_ENTRY = {
    "id": "fake",
    "title": "Synthetic test source",
    "license": "none",
    "version": "0",
    "driver": "fake",
    "extent": {
        "spatial": {"bbox": FAKE_BBOX},
        "temporal": {"start": "2000-01-01", "end": None},
    },
    "access": [{"type": "fake", "role": "primary"}],
    "variables": {
        "AGRO.PRCP": {"source_name": "precip", "conversion": None},
        "AGRO.TMAX": {"source_name": "tmax_k", "conversion": "k_to_degc"},
    },
}


def fake_calls() -> list:
    """The fetch log of the *registered* fake driver class.

    pytest can import this file twice (as ``conftest`` and as
    ``tests.conftest``), producing two FakeDriver classes; the driver
    registry always holds the active one, so tests must go through it.
    """
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake"].calls


@pytest.fixture()
def config(tmp_path):
    catalog.register_entry(FAKE_ENTRY)
    fake_calls().clear()
    return Config(root=tmp_path / "root", domain="africa")
