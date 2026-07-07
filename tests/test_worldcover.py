"""Tests for the ESA WorldCover crop-mask layer.

The pure threshold logic and the harmonize/catalog/STAC wiring are tested
directly; the end-to-end pipeline runs over the fake WorldCover driver
(no GEE, no network), matching the other driver test suites.
"""

import numpy as np
import pytest

from agwise_data import catalog, drivers
from agwise_data.api import get_cropmask, get_static
from agwise_data.cache import read_manifest
from agwise_data.drivers.worldcover import WorldCoverGeeDriver, cropland_mask
from agwise_data.harmonize import (
    DEFAULT_STATIC_SOURCE,
    static_canonical_name,
    static_has_depth,
    static_short_name,
)
from agwise_data.stac import to_stac_collection

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain


def test_cropland_mask_threshold():
    frac = np.array([[0.0, 0.49, 0.5], [0.9, np.nan, 1.0]], dtype="float32")
    out = cropland_mask(frac, 0.5)
    # >= threshold → 1.0; below and NaN fraction → NaN (never fabricated)
    assert out[0, 2] == 1.0 and out[1, 0] == 1.0 and out[1, 2] == 1.0
    assert np.isnan(out[0, 0]) and np.isnan(out[0, 1]) and np.isnan(out[1, 1])
    assert out.dtype == np.float32
    # a stricter threshold keeps only the fully-cropland cells
    strict = cropland_mask(frac, 1.0)
    assert strict[1, 2] == 1.0
    assert np.isnan(strict[0, 2]) and np.isnan(strict[1, 0])


def test_cropland_names_and_default_source():
    assert static_canonical_name("CROPLAND") == "LC.CROPLAND"
    assert static_canonical_name("cropmask") == "LC.CROPLAND"  # legacy label
    assert static_short_name("LC.CROPLAND") == "CROPLAND"
    assert not static_has_depth("LC.CROPLAND")
    assert DEFAULT_STATIC_SOURCE["LC.CROPLAND"] == "esa_worldcover"


def test_worldcover_catalog_and_driver():
    entry = catalog.get_entry("esa_worldcover")
    assert entry["driver"] == "worldcover_gee"
    assert entry.get("version")  # recorded in manifests; bump if the policy changes
    assert "LC.CROPLAND" in entry["variables"]
    access = entry["access"][0]
    assert access["collection"] == "ESA/WorldCover/v200"
    assert access["crop_class"] == 40
    # the driver the catalog names is the real GEE crop-mask driver
    from agwise_data.config import Config

    drv = drivers.get_driver(entry, Config())
    assert isinstance(drv, WorldCoverGeeDriver)


def test_worldcover_stac_serialization():
    stac = to_stac_collection("esa_worldcover")
    assert stac["id"] == "esa_worldcover"
    var = stac["summaries"]["variables"]["LC.CROPLAND"]
    assert var["unit"] == "1"
    assert var["source_name"] == "Map"
    # static layer → open-ended temporal interval
    assert stac["extent"]["temporal"]["interval"][0] == [None, None]


def test_get_cropmask_pipeline(config):
    res = get_cropmask(bbox=BBOX, source="fake_worldcover", config=config)
    info = res["LC.CROPLAND"]
    assert info["short"] == "CROPLAND"
    assert info["source"] == "fake_worldcover"
    assert info["nc"].exists()

    da = info["data"]
    assert set(da.dims) == {"lat", "lon"}
    # a binary mask: every finite value is exactly 1.0, the rest NaN
    finite = da.values[np.isfinite(da.values)]
    assert finite.size > 0
    assert np.all(finite == 1.0)
    assert np.isnan(da.values).any()

    meta = read_manifest(info["nc"])
    assert meta["source_id"] == "fake_worldcover"
    assert meta["variable"] == "LC.CROPLAND"


def test_get_cropmask_cache_hit(config):
    from tests.conftest import fake_worldcover_calls

    kwargs = dict(bbox=BBOX, source="fake_worldcover", config=config)
    get_cropmask(**kwargs)
    n = len(fake_worldcover_calls())
    res2 = get_cropmask(**kwargs)  # second call: served from cache, no refetch
    assert len(fake_worldcover_calls()) == n
    assert res2["LC.CROPLAND"]["nc"].exists()


def test_cropmask_also_reachable_via_get_static(config):
    # get_cropmask is a thin wrapper; get_static resolves the same layer.
    res = get_static("CROPLAND", bbox=BBOX, source="fake_worldcover", config=config)
    assert "LC.CROPLAND" in res
