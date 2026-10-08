"""Two-level range QC (agwise_data.qc) and its wiring into the products."""

import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data import qc
from agwise_data.api import get_climate, get_static
from agwise_data.cache import manifest_path, read_manifest
from agwise_data.harmonize import CANONICAL_VARS, STATIC_VARS

BBOX = (33.0, -2.0, 40.0, 2.0)  # inside the fake source's domain


def _cube(values):
    times = pd.date_range("2020-01-01", periods=len(values), freq="D")
    data = np.asarray(values, dtype="float32")[:, None, None]
    return xr.DataArray(
        data, coords={"time": times, "lat": [0.0], "lon": [35.0]},
        dims=("time", "lat", "lon"), name="PRCP",
    )


def _computed(stats):
    import dask

    return dask.compute(stats)[0]


# ---------------------------------------------------------------------------
def test_default_ranges_cover_every_weather_variable_with_matching_units():
    ranges = qc.default_ranges()
    for name, meta in CANONICAL_VARS.items():
        assert name in ranges, name
        assert ranges[name]["units"] == meta["units"], name
    for name, spec in ranges.items():
        meta = CANONICAL_VARS.get(name) or STATIC_VARS[name]
        assert spec["units"] == meta["units"], name
        (plo, phi), (qlo, qhi) = spec["physical"], spec["plausible"]
        assert plo <= qlo <= qhi <= phi, f"{name}: plausible not inside physical"


def test_warn_masks_physical_and_keeps_plausible():
    # PRCP: physical [0, 2000], plausible [0, 500]
    da = _cube([-1.0, 0.0, 10.0, 600.0, 2500.0, np.nan])
    out, stats = qc.apply(da, "PRCP", "warn")
    vals = out.values.ravel()
    assert np.isnan(vals[0]) and np.isnan(vals[4])  # impossible -> NaN
    assert vals[3] == 600.0  # extreme but possible -> kept
    report = qc.build_report("PRCP", "chirps", "warn", _computed(stats))
    assert report["physical"]["below"] == 1
    assert report["physical"]["above"] == 1
    assert report["plausible"]["above"] == 2  # 600 and 2500
    assert report["plausible"]["below"] == 1  # -1
    assert report["n_missing_input"] == 1
    assert report["max_after_qc"] == 600.0
    assert len(report["warnings"]) == 2


def test_strict_also_masks_plausible_and_off_does_nothing():
    da = _cube([10.0, 600.0])
    strict, _ = qc.apply(da, "PRCP", "strict")
    assert np.isnan(strict.values.ravel()[1])
    off, stats = qc.apply(da, "PRCP", "off")
    assert off is da and stats == {}


def test_overrides_by_any_name_replace_only_the_named_level():
    r = qc.ranges_for("AGRO.PRCP", {"Precipitation": {"plausible": [0, 300]}})
    assert r == {"physical": [0, 2000], "plausible": [0, 300]}
    r = qc.ranges_for("TMAX", {"PRCP": {"plausible": [0, 300]}})
    assert r["plausible"] == [-40, 50]  # other variables untouched
    # a variable without defaults gets checked once the user gives a range
    assert qc.ranges_for("TPI") is None
    assert qc.ranges_for("TPI", {"TPI": {"physical": [-500, None]}}) == {
        "physical": [-500, None], "plausible": [None, None]
    }


@pytest.mark.parametrize(
    "bad",
    [
        {"NOPE": {"physical": [0, 1]}},
        {"PRCP": {"physical": [5, 1]}},
        {"PRCP": {"physical": [0]}},
        {"PRCP": {"typo": [0, 1]}},
    ],
)
def test_invalid_overrides_fail_early(bad):
    with pytest.raises(ValueError):
        qc.validate_overrides(bad)


def test_invalid_mode():
    with pytest.raises(ValueError):
        qc.resolve_mode("loose")


def test_signature_tracks_mode_and_ranges():
    base = qc.signature("PRCP", "warn")
    assert qc.signature("PRCP", "strict") != base
    assert qc.signature("PRCP", "warn", {"PRCP": {"plausible": [0, 300]}}) != base
    # an override for another variable does not change this one
    assert qc.is_default("PRCP", "warn", {"TMAX": {"plausible": [0, 40]}})


# ---------------------------------------------------------------------------
# Products (fake driver: PRCP = day of year, 1..366 in 2020)

def test_get_climate_writes_report_and_keeps_plausible_outliers(config):
    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, freq="daily",
              source="fake", config=config)
    default = get_climate(**kw)["AGRO.PRCP"]
    assert default["nc"].name == "Daily_PRCP_2020_2020_fake.nc"  # no QC tag
    report = json.loads(default["qc"].read_text())
    assert report["mode"] == "warn" and report["checked"] is True
    assert report["physical"]["above"] == 0 and report["plausible"]["above"] == 0
    assert read_manifest(default["nc"])["qc_signature"] == report["signature"]

    custom = {"PRCP": {"plausible": [0, 300]}}
    with pytest.warns(qc.QCWarning, match="plausible range"):
        res = get_climate(qc_ranges=custom, **kw)["AGRO.PRCP"]
    assert res["nc"] != default["nc"]
    assert res["nc"].name.startswith("Daily_PRCP_2020_2020_fake_qc")
    da = res["data"]
    n_cells = da.sizes["lat"] * da.sizes["lon"]
    report = json.loads(res["qc"].read_text())
    assert report["plausible"]["above"] == 66 * n_cells  # doy 301..366
    assert float(da.max()) == 366.0  # kept


def test_get_climate_physical_override_masks_values(config):
    with pytest.warns(qc.QCWarning, match="physical range"):
        res = get_climate(
            variables="PRCP", years=[2020], bbox=BBOX, freq="daily",
            source="fake", qc_ranges={"PRCP": {"physical": [0, 300]}},
            config=config,
        )["AGRO.PRCP"]
    da = res["data"]
    assert float(da.max()) == 300.0
    assert bool(da.sel(time="2020-12-31").isnull().all())


def test_get_climate_monthly_aggregates_after_qc(config):
    with pytest.warns(qc.QCWarning):
        res = get_climate(
            variables="PRCP", years=[2020], bbox=BBOX, freq="monthly",
            source="fake", qc="strict",
            qc_ranges={"PRCP": {"plausible": [0, 31]}}, config=config,
        )["AGRO.PRCP"]
    da = res["data"]
    assert float(da.sel(time="2020-01-01").isel(lat=0, lon=0)) == sum(range(1, 32))
    assert bool(da.sel(time="2020-02-01").isnull().all())  # every day masked


def test_get_climate_qc_off_has_no_report(config):
    res = get_climate(
        variables="PRCP", years=[2020], bbox=BBOX, freq="daily",
        source="fake", qc="off", config=config,
    )["AGRO.PRCP"]
    assert res["nc"].name == "Daily_PRCP_2020_2020_fake_qcoff.nc"
    assert res["qc"] is None


def test_pre_qc_product_is_rebuilt_from_harmonized_cache(config):
    from tests.conftest import fake_calls

    kw = dict(variables="PRCP", years=[2020], bbox=BBOX, freq="daily",
              source="fake", config=config)
    first = get_climate(**kw)["AGRO.PRCP"]
    # simulate a product written before QC existed
    meta = read_manifest(first["nc"])
    meta.pop("qc_signature")
    manifest_path(first["nc"]).write_text(json.dumps(meta))
    first["qc"].unlink()
    n_calls = len(fake_calls())

    again = get_climate(**kw)["AGRO.PRCP"]
    assert again["qc"].exists()
    assert "qc_signature" in read_manifest(again["nc"])
    assert len(fake_calls()) == n_calls  # rebuilt locally, nothing refetched


def test_get_static_qc_per_depth(config):
    # fake CLAY = 10/20/30 % at the three depths
    with pytest.warns(qc.QCWarning, match="physical range"):
        res = get_static(
            variables="CLAY", bbox=(33.0, -2.0, 36.0, 1.0),
            source="fake_static", qc_ranges={"CLAY": {"physical": [0, 15]}},
            config=config,
        )["SOIL.CLAY"]
    da = res["data"]
    assert float(da.isel(depth=0).max()) == 10.0
    assert bool(da.isel(depth=slice(1, None)).isnull().all())
    report = json.loads(res["qc"].read_text())
    n_valid_per_depth = int(da.isel(depth=0).notnull().sum())
    assert report["physical"]["above"] == 2 * n_valid_per_depth


def test_cli_qc_flags(tmp_path):
    from agwise_data.cli import _parse_qc_ranges, build_parser

    assert _parse_qc_ranges('{"PRCP": {"plausible": [0, 300]}}') == {
        "PRCP": {"plausible": [0, 300]}
    }
    f = tmp_path / "ranges.yaml"
    f.write_text("SOIL.PH:\n  plausible: [4, 9]\n")
    assert _parse_qc_ranges(str(f)) == {"SOIL.PH": {"plausible": [4, 9]}}

    args = build_parser().parse_args(
        ["get", "--vars", "PRCP", "--years", "2020", "--bbox", "33,-2,40,2",
         "--qc", "strict", "--qc-ranges", '{"PRCP": {"physical": [0, 900]}}']
    )
    assert args.qc == "strict"
    assert args.qc_ranges == {"PRCP": {"physical": [0, 900]}}
    args = build_parser().parse_args(["get-static", "--vars", "PH", "--bbox", "1,2,3,4"])
    assert args.qc == "warn" and args.qc_ranges is None
