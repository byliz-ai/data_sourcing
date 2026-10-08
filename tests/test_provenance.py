"""Phase 2 provenance: sidecars, recipes, run manifests, declarations, methods."""

import copy
import json
import warnings

import pandas as pd
import pytest

from agwise_data import catalog, provenance
from agwise_data.api import extract_points, extract_static_points, get_climate
from agwise_data.cache import read_manifest
from tests.conftest import FAKE_ENTRY

BBOX = (33.0, -2.0, 40.0, 2.0)


def _with_conversion(conversion):
    entry = copy.deepcopy(FAKE_ENTRY)
    entry["variables"]["AGRO.TMAX"]["conversion"] = conversion
    catalog.register_entry(entry)


# --- recipes ---------------------------------------------------------------
def test_recipe_is_stable_and_tracks_the_catalog_recipe(config):
    r1 = provenance.recipe("fake", "AGRO.TMAX")
    assert r1 == provenance.recipe("fake", "TMAX") and len(r1) == 8
    assert provenance.recipe("fake", "AGRO.PRCP") != r1
    entry = copy.deepcopy(FAKE_ENTRY)
    entry["description"] = "edited text"
    entry["access"] = [{"type": "fake", "role": "primary", "url": "mirror"}]
    catalog.register_entry(entry)
    assert provenance.recipe("fake", "AGRO.TMAX") == r1  # not a recipe change
    _with_conversion(None)
    assert provenance.recipe("fake", "AGRO.TMAX") != r1


def test_recipe_unknown_source_is_none():
    assert provenance.recipe("no_such_source", "AGRO.PRCP") is None


# --- cache sidecars -------------------------------------------------------
def test_sidecars_record_version_hash_recipe_and_steps(config):
    res = get_climate("TMAX", [2020], bbox=BBOX, freq="monthly", source="fake",
                      qc="off", config=config)
    nc = res["AGRO.TMAX"]["nc"]
    meta = read_manifest(nc)
    assert meta["agwise_data_version"] == provenance.package_version()
    assert meta["sha256"] == provenance.sha256_file(nc)
    assert meta["bytes"] == nc.stat().st_size
    assert meta["recipe"] == provenance.recipe("fake", "AGRO.TMAX")
    assert any("monthly" in s for s in meta["transforms"])
    harmonized = config.harmonized_path("fake", meta["domain"], "TMAX", 2020)
    hmeta = read_manifest(harmonized)
    assert hmeta["recipe"] == meta["recipe"]
    assert hmeta["sha256"] == provenance.sha256_file(harmonized)
    assert any("cropped to domain" in s for s in hmeta["transforms"])


def test_changed_recipe_rebuilds_harmonized_and_product(config):
    from tests.conftest import fake_calls

    kw = dict(variables="TMAX", years=[2020], bbox=BBOX, source="fake",
              qc="off", config=config)
    first = float(get_climate(**kw)["AGRO.TMAX"]["data"].isel(time=0, lat=0, lon=0))
    n = len(fake_calls())
    get_climate(**kw)
    assert len(fake_calls()) == n  # same recipe: cache hit
    _with_conversion(None)  # the catalog now reads TMAX without K -> degC
    second = float(get_climate(**kw)["AGRO.TMAX"]["data"].isel(time=0, lat=0, lon=0))
    assert len(fake_calls()) == n + 1  # harmonized year re-fetched
    assert second == pytest.approx(first + 273.15, abs=1e-3)  # product rebuilt


def test_files_without_recipe_stay_valid(config):
    from tests.conftest import fake_calls

    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, source="fake",
              config=config)
    nc = get_climate(**kw)["AGRO.PRCP"]["nc"]
    for path in (nc, config.harmonized_path(
            "fake", read_manifest(nc)["domain"], "PRCP", 2020)):
        side = path.with_name(path.name + ".meta.json")
        meta = json.loads(side.read_text())
        meta.pop("recipe")
        side.write_text(json.dumps(meta))
    n = len(fake_calls())
    get_climate(**kw)
    assert len(fake_calls()) == n


# --- point frames ---------------------------------------------------------
def test_point_frames_carry_provenance(config):
    pts = pd.DataFrame({"lon": [35.0], "lat": [0.5]})
    df = extract_points(pts, ["PRCP", "TMAX"], "2020-01-01", "2020-01-31",
                        source="fake", qc="off", config=config)
    assert df.attrs["provenance"]["AGRO.PRCP"] == {
        "source": "fake", "recipe": provenance.recipe("fake", "AGRO.PRCP")}
    st = extract_static_points(pts, ["CLAY"], source="fake_static", config=config)
    assert st.attrs["provenance"]["SOIL.CLAY"]["source"] == "fake_static"


# --- declarations and methods text ----------------------------------------
_VARS = {
    "AGRO.PRCP": {"source": "chirps_v3"},
    "AGRO.TMAX": {"source": "agera5"},
    "SOIL.CLAY": {"source": "soilgrids"},
}


def test_declaration_names_version_and_sources():
    lines = provenance.declaration(_VARS, ("AGRO",))
    text = " ".join(lines)
    assert all(ln.startswith("! ") and len(ln) <= 78 for ln in lines)
    assert provenance.package_version() in text
    assert "chirps_v3 v3.0 (PRCP)" in text and "agera5 v2.0 (TMAX)" in text
    assert "CLAY" not in text
    assert provenance.declaration(_VARS, ("SOIL",), prefix="*")[0].startswith("* ")


def test_methods_text_cites_every_source():
    manifest = provenance.build_run_manifest(
        "to_dssat", ".", [], _VARS, transforms=["gapfill", "saxton_rawls"],
        parameters={"gapfill_days": 5})
    text = provenance.methods_text(manifest)
    assert f"agwise-data v{provenance.package_version()}" in text
    assert "CHIRPS v3.0" in text and "AgERA5" in text and "SoilGrids" in text
    assert "Funk" not in text  # CHIRPS v2 is not used
    assert "Saxton" in text and "gaps of up to 5 days" in text
    assert "References" in text


def test_methods_text_for_a_product(config):
    res = get_climate("PRCP", [2020], bbox=BBOX, source="fake", config=config)
    text = provenance.methods_text(res["AGRO.PRCP"]["nc"])
    assert "Synthetic test source" in text and "range QC (warn)" in text


# --- run manifests --------------------------------------------------------
def _cm_frames(pts):
    from tests.test_writers import _season_weather_long, _soil_frame

    return _season_weather_long(pts), _soil_frame(pts)


def test_to_dssat_run_manifest_is_deterministic(tmp_path):
    from agwise_data.api import to_dssat

    pts = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                        "site": ["Kigali", "Nyagatare"]})
    weather, soil = _cm_frames(pts)
    weather.attrs["provenance"] = {
        "AGRO.TMAX": {"source": "agera5"}, "AGRO.TMIN": {"source": "agera5"},
        "AGRO.SRAD": {"source": "agera5"}, "AGRO.PRCP": {"source": "chirps_v3"},
    }
    out = tmp_path / "D"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        to_dssat(pts, out_dir=out, station_col="site", weather=weather, soil=soil)
        first = (out / "manifest.json").read_bytes()
        to_dssat(pts, out_dir=out, station_col="site", weather=weather, soil=soil)
    assert (out / "manifest.json").read_bytes() == first

    m = json.loads(first)
    assert m["function"] == "to_dssat"
    assert m["points"]["n"] == 2
    paths = [f["path"] for f in m["files"]]
    assert paths == sorted(paths)
    assert {"EXTE0001/WHTE0001.WTH", "EXTE0001/SOIL.SOL", "qc_report.json",
            "METHODS.md"} <= set(paths)
    for f in m["files"]:
        assert f["sha256"] == provenance.sha256_file(out / f["path"])
    assert m["variables"]["AGRO.PRCP"]["source"] == "chirps_v3"
    assert m["variables"]["SOIL.*"]["source"] == "user-supplied"
    assert set(m["sources"]) == {"agera5", "chirps_v3"}
    assert m["qc"]["files_with_problems"] == 0
    assert "created_utc" not in first.decode()
    wth = (out / "EXTE0001" / "WHTE0001.WTH").read_text()
    assert "! agwise-data" in wth and "chirps_v3 v3.0 (PRCP)" in wth
    methods = (out / "METHODS.md").read_text()
    assert "CHIRPS v3.0" in methods


# --- xarray lock leak (fixed in agwise_data.cache) -------------------------
def test_combined_lock_nonblocking_acquire_releases_on_failure():
    import threading

    import agwise_data.cache  # noqa: F401  (applies the fix)
    from xarray.backends.locks import HDF5_LOCK, CombinedLock

    for _ in range(50):  # lock order inside CombinedLock varies
        busy = threading.Lock()
        busy.acquire()
        assert CombinedLock([HDF5_LOCK, busy]).acquire(blocking=False) is False
        assert not HDF5_LOCK.locked()
        busy.release()
