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
from agwise_data.drivers.static import StaticDriver
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


# ---------------------------------------------------------------------------
# Static counterpart: synthetic elevation and a depth-layered soil property.

FAKE_DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]


@register("fake_static")
class FakeStaticDriver(StaticDriver):
    calls: list = []  # class-level: records fetches to assert cache hits

    def _fetch_static(self, variable: str, domain: str):
        FakeStaticDriver.calls.append((variable, domain))
        spec = self.entry["variables"][variable]
        w, s, e, n = self.config.bbox_for(domain)
        lats = np.arange(s, n + 0.001, 0.1).round(4)
        lons = np.arange(w, e + 0.001, 0.1).round(4)
        if variable == "TOPO.ELEV":
            # elevation encodes its coordinates: easy point assertions
            data = lats[:, None] * 100.0 + lons[None, :]
            da = xr.DataArray(
                data.astype("float32"),
                coords={"lat": lats, "lon": lons},
                dims=("lat", "lon"),
                name="dem",
            )
        else:
            # raw layer value encodes the depth index, scaled like SoilGrids
            layers = [
                np.full((len(lats), len(lons)), (di + 1) * 100.0, dtype="float32")
                for di in range(len(FAKE_DEPTHS))
            ]
            da = xr.DataArray(
                np.stack(layers),
                coords={"depth": FAKE_DEPTHS, "lat": lats, "lon": lons},
                dims=("depth", "lat", "lon"),
                name=spec.get("source_name", "value"),
            )
        da = apply_conversion(da, spec.get("conversion"))
        return da, {"source_url": f"fake://{variable}/{domain}"}


FAKE_STATIC_ENTRY = {
    "id": "fake_static",
    "title": "Synthetic static test source",
    "license": "none",
    "version": "0",
    "driver": "fake_static",
    "depths": FAKE_DEPTHS,
    "extent": {
        "spatial": {"bbox": FAKE_BBOX},
        "temporal": {"start": None, "end": None},
    },
    "access": [{"type": "fake", "role": "primary"}],
    "variables": {
        "TOPO.ELEV": {"source_name": "dem", "conversion": None},
        "SOIL.CLAY": {"source_name": "clay", "conversion": "d10", "nodata": 0},
    },
}


def fake_static_calls() -> list:
    """The fetch log of the registered fake static driver class."""
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake_static"].calls


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
    catalog.register_entry(FAKE_STATIC_ENTRY)
    fake_calls().clear()
    fake_static_calls().clear()
    return Config(root=tmp_path / "root", domain="africa")
