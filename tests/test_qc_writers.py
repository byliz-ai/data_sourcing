"""Phase 1 part 3: cross-variable checks, gap-filling and post-write validation."""

import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from agwise_data import qc
from agwise_data.writers import apsim, dssat, oryza, soil, wofost
from agwise_data.writers._common import prepare_weather
from agwise_data.writers.validate import (
    validate_met, validate_oryza_weather, validate_sol, validate_wofost_weather,
    validate_wth,
)
from tests.test_writers import (
    _oryza_series, _season_weather_long, _soil_frame, _soil_row,
    _two_month_series, _wofost_weather_long,
)


def test_extraterrestrial_radiation_matches_fao56_example_8():
    # FAO-56 example 8: 20 deg S on 3 September (day 246) -> 32.2 MJ m-2 day-1
    assert qc.extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.05)


def test_gridded_srad_above_ra_is_masked():
    times = pd.date_range("2021-03-21", periods=3, freq="D")  # Ra ~ 37.8 at 0 deg
    da = xr.DataArray(
        np.array([20.0, 40.0, 45.0], dtype="float32")[:, None, None],
        coords={"time": times, "lat": [0.0], "lon": [35.0]},
        dims=("time", "lat", "lon"), name="SRAD",
    )
    out, stats = qc.apply(da, "SRAD", "warn")
    vals = out.values.ravel()
    assert vals[0] == 20.0 and np.isnan(vals[1]) and np.isnan(vals[2])
    report = qc.build_report("SRAD", "agera5", "warn", qc.compute(stats))
    assert report["srad_above_extraterrestrial"]["count"] == 2
    assert any("extraterrestrial" in w for w in report["warnings"])


@pytest.mark.parametrize(
    "fractions, over3, over5",
    [
        ((30.0, 30.0, 38.5), 0, 0),   # 98.5 %
        ((30.0, 30.0, 36.0), 1, 0),   # 96 %
        ((30.0, 30.0, 30.0), 1, 1),   # 90 %: still rescaled, flagged
    ],
)
def test_normalize_texture_always_rescales_and_flags(fractions, over3, over5):
    clay, silt, sand, info = qc.normalize_texture(*[[v] for v in fractions])
    assert clay[0] + silt[0] + sand[0] == pytest.approx(100.0)
    assert info["renormalized"] == 1
    assert info["deviation_over_3pct"] == over3
    assert info["large_deviation_over_5pct"] == over5


def test_normalize_texture_leaves_missing_layers_alone():
    clay, silt, sand, info = qc.normalize_texture([np.nan, 20.0], [30.0, 30.0], [40.0, 50.0])
    assert np.isnan(clay[0]) and silt[0] == 30.0
    assert info == {"renormalized": 0, "deviation_over_3pct": 0,
                    "large_deviation_over_5pct": 0, "max_deviation_pct": 0.0}


def test_check_weather_dates_swap_ra_and_gapfill():
    df = _two_month_series().rename(columns={"PRCP": "RAIN"})
    df.loc[3, ["TMAX", "TMIN"]] = [5.0, 25.0]           # crossed
    df.loc[10, "SRAD"] = 60.0                            # > Ra (and > physical)
    df.loc[20:24, "TMAX"] = np.nan                       # 5-day gap -> filled
    df.loc[40:45, "TMIN"] = np.nan                       # 6-day gap -> kept
    df.loc[30, "RAIN"] = np.nan                          # rain never filled
    df = df.drop(index=50)                               # a missing date
    out, rep = qc.check_weather(df, "DATE", lat=-1.95)

    assert len(out) == 59 and rep["dates_inserted"] == 1
    assert rep["tmin_gt_tmax_swapped"] == 1
    assert out.loc[3, "TMAX"] == 25.0 and out.loc[3, "TMIN"] == 5.0
    assert rep["srad_above_extraterrestrial"] == 1
    assert rep["gapfilled"]["TMAX"] == 5 + 1  # the gap + the inserted date
    assert out.loc[20:24, "TMAX"].notna().all()
    assert out.loc[40:45, "TMIN"].isna().all()
    assert np.isnan(out.loc[30, "RAIN"])
    # the inserted date: TMAX/TMIN/SRAD 1-day gap filled, rain stays missing
    assert out.loc[50, ["TMAX", "TMIN", "SRAD"]].notna().all()
    assert np.isnan(out.loc[50, "RAIN"])
    assert rep["missing_after"]["RAIN"] == 2


def test_prepare_weather_keeps_interior_missing_days_and_trims_edges():
    df = _two_month_series()
    df.loc[0:1, ["TMAX", "TMIN", "SRAD", "PRCP"]] = np.nan   # leading empty days
    df.loc[30, ["TMAX", "TMIN", "SRAD", "PRCP"]] = np.nan    # interior empty day
    out = prepare_weather(df, gapfill_days=0)
    assert out["DATE"].iloc[0] == pd.Timestamp("2021-01-03")
    assert (out["DATE"].diff().dropna() == pd.Timedelta(days=1)).all()
    assert out.loc[out["DATE"] == "2021-01-31", "TMAX"].isna().all()


# ---------------------------------------------------------------------------
# Post-write validation

def test_written_files_validate_clean(tmp_path):
    rep = {}
    p = dssat.write_wth(_two_month_series(), lat=-1.95, lon=30.06,
                        path=tmp_path / "a.WTH", report=rep)
    v = rep["validation"]["wth"]
    assert v["ok"] and v["n_days"] == 59 and v["date_gaps"] == 0
    assert validate_wth(p)["ok"]
    assert rep["weather"]["gapfill_max_days"] == qc.GAPFILL_MAX_DAYS

    assert validate_met(apsim.write_met(_two_month_series(), lat=-1.95, lon=30.06,
                                        path=tmp_path / "a.met"))["ok"]
    sol = soil.write_sol(_soil_row(), lat=-1.95, lon=30.06, path=tmp_path / "S.SOL")
    assert validate_sol(sol)["ok"]


def test_wofost_and_oryza_files_validate_clean(tmp_path):
    pts = pd.DataFrame({"lon": [30.06], "lat": [-1.95]})
    wide = (_wofost_weather_long(pts)
            .pivot_table(index="time", columns="variable", values="value")
            .reset_index())
    rep = {}
    p = wofost.write_weather(wide, path=tmp_path / "w.csv", lat=-1.95, report=rep)
    assert rep["validation"]["weather"]["ok"] and validate_wofost_weather(p)["ok"]
    rep = {}
    paths = oryza.write_weather(_oryza_series(), lat=-1.95, lon=30.06,
                                out_dir=tmp_path / "ory", report=rep)
    assert len(paths) == 2  # the series spans two calendar years
    v = rep["validation"]["weather"]
    assert v["ok"] and v["n_days"] == 5 and v["tmin_gt_tmax"] == 0
    assert validate_oryza_weather(paths)["ok"]


def test_validate_wth_catches_broken_files(tmp_path):
    p = dssat.write_wth(_two_month_series(), lat=0.0, lon=0.0, path=tmp_path / "b.WTH")
    lines = p.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("@  DATE")) + 1
    lines[start + 2] = lines[start + 2][:19] + "   nan" + lines[start + 2][25:]
    del lines[start + 10]                                          # a date gap
    lines[start + 20] = lines[start + 20][:7] + "  10.0  25.0" + lines[start + 20][19:]
    p.write_text("\n".join(lines) + "\n")
    v = validate_wth(p)
    assert not v["ok"]
    text = " | ".join(v["problems"])
    assert "malformed data row" in text
    assert "gaps in the daily dates" in text
    assert "TMIN > TMAX" in text


def test_validate_sol_catches_water_order_and_texture(tmp_path):
    p = soil.write_sol(_soil_row(), lat=0.0, lon=0.0, path=tmp_path / "S.SOL")
    lines = p.read_text().splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("@  SLB  SLMH")) + 1
    row = lines[i]
    # swap SLLL and SDUL (cols 13-18, 19-24) and push clay to 95 %
    lines[i] = row[:12] + row[18:24] + row[12:18] + row[24:54] + "  95.0" + row[60:]
    p.write_text("\n".join(lines) + "\n")
    v = validate_sol(p)
    assert not v["ok"]
    text = " | ".join(v["problems"])
    assert "SLLL < SDUL < SSAT" in text and "clay + silt > 100" in text


def test_build_profile_normalizes_texture():
    row = _soil_row()
    row[f"SAND_{soil.DEPTH_LABELS[0]}"] += 4.0   # top layer sums to 104 %
    p = soil.build_profile(row)
    top = p["clay"][0] + p["silt"][0] + p["sand"][0]
    assert top == pytest.approx(100.0)
    assert p["texture_qc"]["deviation_over_3pct"] == 1
    assert p["texture_qc"]["large_deviation_over_5pct"] == 0


def test_to_dssat_writes_qc_report(tmp_path):
    from agwise_data.api import to_dssat

    pts = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90]})
    weather = _season_weather_long(pts)
    gap = (weather["point"] == 0) & (weather["variable"] == "SRAD") & (
        weather["time"].between("2020-10-01", "2020-10-03"))
    weather.loc[gap, "value"] = np.nan
    res = to_dssat(pts, out_dir=tmp_path / "D", weather=weather, soil=_soil_frame(pts))
    report = json.loads((tmp_path / "D" / "qc_report.json").read_text())
    assert report["summary"]["n_points"] == 2
    assert report["summary"]["files_with_problems"] == 0
    assert report["summary"]["days_gapfilled"] == 3
    assert res[0]["qc"]["weather"]["gapfilled"] == {"SRAD": 3}
    assert set(res[0]["qc"]["validation"]) == {"wth", "sol"}


def test_crop_model_run_warns_when_a_file_fails_validation(tmp_path, monkeypatch):
    from agwise_data.api import to_apsim
    from agwise_data.writers import apsim as apsim_w

    pts = pd.DataFrame({"lon": [30.06], "lat": [-1.95]})
    weather = _season_weather_long(pts)
    hole = (weather["variable"] == "TMAX") & weather["time"].between(
        "2020-10-01", "2020-10-10")  # 10 days: too long to gap-fill
    weather.loc[hole, "value"] = np.nan
    with pytest.warns(qc.QCWarning, match="failed post-write validation"):
        to_apsim(pts, out_dir=tmp_path / "A", weather=weather, soil=_soil_frame(pts))
    report = json.loads((tmp_path / "A" / "qc_report.json").read_text())
    v = report["points"][0]["validation"]["met"]
    assert not v["ok"] and v["missing"]["tmax"] == 10


def test_fill_reaches_diagonal_neighbour_on_a_1km_grid():
    """Kigali case: the staged 1 km SoilGrids rasters mask some depths only;
    the only complete pixel is a diagonal neighbour ~1.3 km away, beyond the
    1 km default radius that was sized for the 250 m WCS grid."""
    from agwise_data.api import _nearest_valid_fill

    res = 1.0 / 120  # 30 arc-seconds
    lats = -1.95 + res * np.arange(-1, 2)
    lons = 30.06 + res * np.arange(-1, 2)
    data = np.full((3, 3, 3), np.nan)
    data[0] = 28.0                     # 0-5 cm valid everywhere
    data[:, 0, 0] = [27.0, 30.0, 33.0]  # one complete diagonal neighbour
    da = xr.DataArray(data, coords={"depth": ["a", "b", "c"], "lat": lats, "lon": lons},
                      dims=("depth", "lat", "lon"))
    hit = _nearest_valid_fill(da, 30.06, -1.95, 1000.0)
    assert hit is not None
    donor, dist = hit
    assert donor.tolist() == [27.0, 30.0, 33.0]
    assert 1000 < dist < 1400


def test_validate_sol_flags_layers_without_water_properties(tmp_path):
    row = _soil_row()
    for k in list(row):
        if k.startswith(("CLAY_5", "SAND_5", "SILT_5")):  # the 5-15 cm layer
            row[k] = np.nan
    v = validate_sol(soil.write_sol(row, lat=0.0, lon=0.0, path=tmp_path / "S.SOL"))
    assert not v["ok"]
    assert any("no SLLL/SDUL/SSAT" in p for p in v["problems"])
