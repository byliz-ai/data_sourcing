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
from agwise_data.drivers.modis import ModisDriver
from agwise_data.drivers.seasonal import SeasonalDriver
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
            data = np.stack(layers)
            # a masked "town": SoilGrids-style NoData block (all depths NaN)
            mask = ((lats >= -0.001) & (lats <= 0.201))[:, None] & (
                (lons >= 34.999) & (lons <= 35.201)
            )[None, :]
            data[:, mask] = np.nan
            da = xr.DataArray(
                data,
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
        # raw fake layer = (depth_index+1)*100; d10 -> 10/20/30 in each unit,
        # enough to exercise the pedotransfer derivations (hydraulics/olsen_p).
        "SOIL.SAND": {"source_name": "sand", "conversion": "d10", "nodata": 0},
        "SOIL.SOC": {"source_name": "soc", "conversion": "d10", "nodata": 0},
        "SOIL.EXTP": {"source_name": "p", "conversion": "d10", "nodata": 0},
    },
}


# ---------------------------------------------------------------------------
# Crop-mask counterpart: a synthetic WorldCover cropland mask (no GEE). A
# regular subset of pixels is cropland (1.0), the rest NaN — enough to
# assert the 1/NaN encoding and grid alignment through the whole pipeline.


@register("fake_worldcover")
class FakeWorldCoverDriver(StaticDriver):
    calls: list = []  # class-level: records fetches to assert cache hits

    def _fetch_static(self, variable: str, domain: str):
        FakeWorldCoverDriver.calls.append((variable, domain))
        w, s, e, n = self.config.bbox_for(domain)
        lats = np.arange(s, n + 0.001, 0.1).round(4)
        lons = np.arange(w, e + 0.001, 0.1).round(4)
        rows = np.arange(len(lats))[:, None]
        cols = np.arange(len(lons))[None, :]
        mask = np.full((len(lats), len(lons)), np.nan, dtype="float32")
        mask[(rows + cols) % 3 == 0] = 1.0  # ~1/3 of the cells are cropland
        da = xr.DataArray(
            mask,
            coords={"lat": lats, "lon": lons},
            dims=("lat", "lon"),
            name="Map",
        )
        return da, {"access": "fake", "crop_class": 40, "crop_fraction_min": 0.5}


FAKE_WORLDCOVER_ENTRY = {
    "id": "fake_worldcover",
    "title": "Synthetic cropland-mask test source",
    "license": "none",
    "version": "0",
    "driver": "fake_worldcover",
    "extent": {
        "spatial": {"bbox": FAKE_BBOX},
        "temporal": {"start": None, "end": None},
    },
    "access": [{"type": "fake", "role": "primary"}],
    "variables": {"LC.CROPLAND": {"source_name": "Map", "conversion": None}},
}


# ---------------------------------------------------------------------------
# Seasonal counterpart: synthetic ensemble forecasts. Values encode member
# and lead (member*1000 + lead_day) for easy assertions.

FAKE_MEMBERS = 5
FAKE_LEAD_DAYS = 30


def synthetic_seasonal(init_month: int, year: int, bbox) -> xr.DataArray:
    w, s, e, n = bbox
    lats = np.arange(s, n + 0.001, 0.5)
    lons = np.arange(w, e + 0.001, 0.5)
    valid = pd.date_range(
        f"{year}-{init_month:02d}-02", periods=FAKE_LEAD_DAYS, freq="D"
    )
    members = np.arange(FAKE_MEMBERS)
    lead_day = np.arange(1, FAKE_LEAD_DAYS + 1)
    data = (
        members[:, None] * 1000.0 + lead_day[None, :]
    )[:, :, None, None] * np.ones((1, 1, len(lats), len(lons)))
    return xr.DataArray(
        data.astype("float32"),
        coords={"number": members, "time": valid, "latitude": lats, "longitude": lons},
        dims=("number", "time", "latitude", "longitude"),
        name="tp",
    )


@register("fake_seasonal")
class FakeSeasonalDriver(SeasonalDriver):
    calls: list = []  # class-level: records fetches to assert cache hits

    def _fetch_seasonal(self, variable: str, init_month: int, year: int, domain: str):
        FakeSeasonalDriver.calls.append((variable, init_month, year, domain))
        da = synthetic_seasonal(init_month, year, self.config.bbox_for(domain))
        return da, {"source_url": f"fake://{variable}/{init_month:02d}/{year}"}


FAKE_SEASONAL_ENTRY = {
    "id": "fake_seasonal",
    "title": "Synthetic seasonal test source",
    "license": "none",
    "version": "0",
    "driver": "fake_seasonal",
    "extent": {
        "spatial": {"bbox": FAKE_BBOX},
        "temporal": {"start": "1981-01-01", "end": None},
    },
    "access": [{"type": "fake", "role": "primary"}],
    "variables": {
        "AGRO.PRCP": {"source_name": "total_precipitation", "nc_var": "tp"},
        "AGRO.TMAX": {"source_name": "mx2t24", "nc_var": "mx2t24"},
    },
}


# ---------------------------------------------------------------------------
# MODIS counterpart: synthetic 16-day composites. Terra-like (first DOY 1)
# and Aqua-like (first DOY 9) entries interleave into 46 composites/year;
# values encode the composite's day of year for easy assertions.

FAKE_COMPOSITES = 23


def synthetic_composites(year: int, first_doy: int, bbox) -> xr.DataArray:
    w, s, e, n = bbox
    lats = np.arange(s, n + 0.001, 0.5)
    lons = np.arange(w, e + 0.001, 0.5)
    doys = np.arange(first_doy, first_doy + 16 * FAKE_COMPOSITES, 16)
    times = pd.to_datetime(f"{year}-01-01") + pd.to_timedelta(doys - 1, unit="D")
    data = np.broadcast_to(
        doys.astype("float32")[:, None, None], (len(doys), len(lats), len(lons))
    ).copy()
    return xr.DataArray(
        data,
        coords={"time": times, "latitude": lats, "longitude": lons},
        dims=("time", "latitude", "longitude"),
        name="NDVI",
    )


@register("fake_modis")
class FakeModisDriver(ModisDriver):
    calls: list = []  # class-level: records fetches to assert cache hits

    def _fetch_year(self, variable: str, year: int, domain: str):
        FakeModisDriver.calls.append((self.source_id, variable, year, domain))
        da = synthetic_composites(
            year, int(self.entry["first_doy"]), self.config.bbox_for(domain)
        )
        return da, {"source_url": f"fake://{self.source_id}/{variable}/{year}"}


def _fake_modis_entry(source_id: str, first_doy: int, start: str) -> dict:
    return {
        "id": source_id,
        "title": f"Synthetic MODIS test source (first DOY {first_doy})",
        "license": "none",
        "version": "0",
        "driver": "fake_modis",
        "composites_per_year": FAKE_COMPOSITES,
        "first_doy": first_doy,
        "extent": {
            "spatial": {"bbox": FAKE_BBOX},
            "temporal": {"start": start, "end": None},
        },
        "access": [{"type": "fake", "role": "primary"}],
        "variables": {"RS.NDVI": {"source_name": "NDVI", "conversion": None}},
    }


FAKE_MODIS_TERRA_ENTRY = _fake_modis_entry("fake_mod", 1, "2000-02-18")
FAKE_MODIS_AQUA_ENTRY = _fake_modis_entry("fake_myd", 9, "2002-07-04")


def fake_modis_calls() -> list:
    """The fetch log of the registered fake MODIS driver class."""
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake_modis"].calls


def fake_seasonal_calls() -> list:
    """The fetch log of the registered fake seasonal driver class."""
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake_seasonal"].calls


def fake_static_calls() -> list:
    """The fetch log of the registered fake static driver class."""
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake_static"].calls


def fake_worldcover_calls() -> list:
    """The fetch log of the registered fake WorldCover driver class."""
    from agwise_data.drivers import _REGISTRY

    return _REGISTRY["fake_worldcover"].calls


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
    catalog.register_entry(FAKE_SEASONAL_ENTRY)
    catalog.register_entry(FAKE_MODIS_TERRA_ENTRY)
    catalog.register_entry(FAKE_MODIS_AQUA_ENTRY)
    catalog.register_entry(FAKE_WORLDCOVER_ENTRY)
    fake_calls().clear()
    fake_static_calls().clear()
    fake_seasonal_calls().clear()
    fake_modis_calls().clear()
    fake_worldcover_calls().clear()
    return Config(root=tmp_path / "root", domain="africa")
