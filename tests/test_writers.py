"""Tests for the crop-model input writers (weather half: DSSAT .WTH, APSIM .met).

Network-free and R-free: they assert the exact file structure the DSSAT/apsimx
readers expect (the layout was matched byte-for-byte against reference files
those packages emit, and round-trips through read_wth / read_apsim_met were
verified live). They also cover the shared TAV/AMP and TMIN<=TMAX logic.
"""

import numpy as np
import pandas as pd
import pytest

from agwise_data.writers import apsim, dssat, oryza, soil, wofost
from agwise_data.writers._common import prepare_weather, station_code, tav_amp


def _soil_row():
    """A realistic soil-point row (extract_static_points shape, right units)."""
    clay = [33.9, 43.0, 49.0, 54.0, 39.9, 38.7]
    sand = [43.1, 34.0, 28.0, 27.0, 40.0, 41.1]
    silt = [23.0, 23.0, 21.0, 19.0, 20.1, 20.2]
    soc = [35.8, 19.6, 16.2, 12.0, 3.0, 1.9]      # g/kg
    nit = [2.8, 2.0, 1.6, 0.9, 0.8, 0.7]          # g/kg
    ph = [6.1, 6.1, 6.1, 6.1, 7.41, 7.62]
    cec = [28.6, 27.2, 27.2, 27.8, 27.9, 28.0]
    bd = [1.30, 1.30, 1.30, 1.40, 1.45, 1.46]
    row = {}
    for i, d in enumerate(soil.DEPTH_LABELS):
        row[f"CLAY_{d}"] = clay[i]; row[f"SAND_{d}"] = sand[i]
        row[f"SILT_{d}"] = silt[i]; row[f"SOC_{d}"] = soc[i]
        row[f"NITROGEN_{d}"] = nit[i]; row[f"PH_{d}"] = ph[i]
        row[f"CEC_{d}"] = cec[i]; row[f"BDOD_{d}"] = bd[i]
    return row


def _two_month_series():
    jan = pd.date_range("2021-01-01", "2021-01-31", freq="D")
    feb = pd.date_range("2021-02-01", "2021-02-28", freq="D")
    dates = jan.append(feb)
    tmax = np.r_[np.full(len(jan), 20.0), np.full(len(feb), 30.0)]
    tmin = np.r_[np.full(len(jan), 10.0), np.full(len(feb), 20.0)]
    return pd.DataFrame({
        "DATE": dates, "TMAX": tmax, "TMIN": tmin,
        "SRAD": np.full(len(dates), 18.0), "PRCP": np.full(len(dates), 5.0),
    })


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def test_tav_amp_known_values():
    df = prepare_weather(_two_month_series())
    tav, amp = tav_amp(df)
    # Jan mean 15 (31 d), Feb mean 25 (28 d): TAV is day-weighted = 19.7;
    # AMP uses the monthly means, = (25-15)/2 = 5.
    assert amp == 5.0
    assert tav == 19.7


def test_prepare_weather_prcp_alias_and_swap():
    df = pd.DataFrame({
        "time": pd.date_range("2021-01-01", periods=3, freq="D"),
        "TMAX": [20.0, 10.0, 25.0],  # day 2 is crossed (TMIN 15 > TMAX 10)
        "TMIN": [10.0, 15.0, 12.0],
        "SRAD": [18.0, 18.0, 18.0],
        "PRCP": [1.0, 2.0, 3.0],
    })
    out = prepare_weather(df)
    assert "RAIN" in out.columns  # PRCP -> RAIN alias
    assert (out["TMAX"] >= out["TMIN"]).all()  # crossed day swapped
    assert out.loc[1, "TMAX"] == 15.0 and out.loc[1, "TMIN"] == 10.0


def test_prepare_weather_missing_cols_raises():
    df = pd.DataFrame({"DATE": pd.date_range("2021-01-01", periods=2), "TMAX": [1, 2]})
    with pytest.raises(ValueError, match="missing"):
        prepare_weather(df)


def test_station_code():
    assert station_code("Kigali") == "KIGA"
    assert station_code("") == "AGWS"
    assert station_code("Nyagatare District") == "NYAG"


# --------------------------------------------------------------------------
# DSSAT .WTH
# --------------------------------------------------------------------------
def test_write_wth_structure(tmp_path):
    p = dssat.write_wth(
        _two_month_series(), lat=-1.95, lon=30.06, path=tmp_path / "S0001.WTH",
        station="Kigali", elev=1500,
    )
    lines = p.read_text().splitlines()
    assert lines[0] == "$WEATHER: "
    assert lines[3] == "@ INSI      LAT     LONG  ELEV   TAV   AMP REFHT WNDHT"
    # GENERAL data row: 4-char code, lat/long 3dp, elev int, tav/amp/refht/wndht
    assert lines[4] == "  KIGA   -1.950   30.060  1500  19.7   5.0   2.0   2.0"
    assert lines[6] == "@  DATE  TMAX  TMIN  SRAD  RAIN"
    # first data row: YYYYDDD = 2021001, values in 6-wide 1dp fields
    assert lines[7] == "2021001  20.0  10.0  18.0   5.0"


def test_write_wth_date_is_yyyyddd(tmp_path):
    df = _two_month_series()
    p = dssat.write_wth(df, lat=0.0, lon=0.0, path=tmp_path / "d.WTH")
    data = [ln for ln in p.read_text().splitlines() if ln[:2].isdigit()]
    # Feb 1 2021 is day-of-year 32
    assert any(ln.startswith("2021032") for ln in data)


def test_write_wth_missing_elev_uses_sentinel(tmp_path):
    p = dssat.write_wth(_two_month_series(), lat=0.0, lon=0.0, path=tmp_path / "e.WTH")
    general = p.read_text().splitlines()[4]
    assert "   -99" in general  # ELEV sentinel when not supplied


def test_write_wth_empty_raises(tmp_path):
    empty = pd.DataFrame(
        {"DATE": pd.to_datetime([]), "TMAX": [], "TMIN": [], "SRAD": [], "RAIN": []}
    )
    with pytest.raises(ValueError, match="No weather rows"):
        dssat.write_wth(empty, lat=0.0, lon=0.0, path=tmp_path / "x.WTH")


# --------------------------------------------------------------------------
# APSIM .met
# --------------------------------------------------------------------------
def test_write_met_structure(tmp_path):
    p = apsim.write_met(
        _two_month_series(), lat=-1.95, lon=30.06, path=tmp_path / "site.met",
        site="KIGALI",
    )
    lines = p.read_text().splitlines()
    assert lines[1] == "[weather.met.weather]"
    assert "site = KIGALI" in lines
    assert "latitude = -1.95" in lines
    assert "tav = 19.7" in lines
    assert "amp = 5.0" in lines
    assert "year day radn maxt mint rain" in lines
    assert "() () (MJ/m2/day) (oC) (oC) (mm)" in lines
    # first data row: APSIM order year day radn maxt mint rain, whole ints bare
    assert "2021 1 18 20 10 5" in lines


def test_write_met_integer_formatting(tmp_path):
    df = _two_month_series()
    df.loc[0, "SRAD"] = 18.5  # a genuine decimal stays a decimal
    p = apsim.write_met(df, lat=0.0, lon=0.0, path=tmp_path / "f.met")
    text = p.read_text()
    assert "2021 1 18.5 20 10 5" in text


# --------------------------------------------------------------------------
# Soil: pedotransfer + texture helpers
# --------------------------------------------------------------------------
def test_saxton_rawls_values_and_ordering():
    pwp, fc, sat, ks = soil.saxton_rawls(clay=40, sand=30, som=3.0)
    assert round(float(pwp), 3) == 0.246
    assert round(float(fc), 3) == 0.380
    assert round(float(sat), 3) == 0.487
    assert round(float(ks), 2) == 3.05
    assert pwp < fc < sat  # physically ordered


def test_saxton_rawls_vectorized():
    import numpy as np
    pwp, fc, sat, ks = soil.saxton_rawls(
        clay=np.array([40.0, 20.0]), sand=np.array([30.0, 60.0]),
        som=np.array([3.0, 1.0]),
    )
    assert pwp.shape == (2,)
    # the higher-clay soil holds more water at wilting point
    assert pwp[0] > pwp[1]


def test_texture_class_cases():
    assert soil.texture_class(0.40, 0.20) == "clay"
    assert soil.texture_class(0.05, 0.05) == "sand"
    assert soil.texture_class(0.339, 0.23) == "clay loam"
    assert soil.texture_class(-1, 0.2) == "NO DATA"


def test_texture_props_and_slu1():
    code, albedo, cn2 = soil.texture_props("clay loam")
    assert code == "CL" and albedo == 0.13 and cn2 == 73.0
    assert round(soil.slu1(33.9, 43.1), 2) == 5.29
    assert soil.slu1(85, 90) == 20 - 0.15 * 90  # sandy branch


def test_root_growth_factor():
    rgf = soil.root_growth_factor()
    assert rgf[0] == 1.0 and rgf[1] == 1.0  # top 15 cm
    assert round(rgf[2], 3) == 0.638 and round(rgf[5], 3) == 0.05


def test_build_profile_missing_column_raises():
    with pytest.raises(KeyError, match="CLAY_0_5cm"):
        soil.build_profile({"SAND_0_5cm": 40.0})


# --------------------------------------------------------------------------
# Soil: DSSAT .SOL
# --------------------------------------------------------------------------
def test_write_sol_structure(tmp_path):
    p = soil.write_sol(
        _soil_row(), lat=-0.225, lon=34.975, path=tmp_path / "SOIL.SOL",
        pedon="TRAN00047", site="Nyando", country="Kenya",
    )
    lines = p.read_text().splitlines()
    assert lines[0] == "*SOILS: General DSSAT Soil Input File"
    # pedon line: code CL and description "clay loam", total depth 200
    assert lines[2] == "*TRAN00047   ISRIC V2    CL      200 clay loam"
    assert lines[3].startswith("@SITE")
    assert lines[5].startswith("@ SCOM")
    # SCOM row carries texture-derived SALB/SLU1/SLRO
    assert lines[6] == "   -99  0.13  5.29  0.50  73.0  1.00  1.00 IB001 IB001 IB001"
    assert lines[7].startswith("@  SLB")
    # six layers follow
    layer_rows = lines[8:14]
    assert len(layer_rows) == 6
    assert layer_rows[0].split()[0] == "5" and layer_rows[-1].split()[0] == "200"
    # top-layer SLOC = SOC/10 = 3.58; SLCL = 33.9
    fields = layer_rows[0].split()
    assert fields[8] == "3.580" and fields[9] == "33.9"


def test_write_sol_nan_becomes_sentinel(tmp_path):
    row = _soil_row()
    row["CEC_100_200cm"] = float("nan")
    p = soil.write_sol(row, lat=0.0, lon=0.0, path=tmp_path / "S.SOL")
    last = p.read_text().splitlines()[13]
    # SCEC column is the 3rd-from-last field; NaN -> -99
    assert last.split()[-2] == "-99"


# --------------------------------------------------------------------------
# Soil: APSIM soil table
# --------------------------------------------------------------------------
def test_apsim_soil_table():
    df = soil.apsim_soil_table(_soil_row())
    assert list(df["Depth"]) == [
        "0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"
    ]
    assert list(df["Thickness"]) == [50, 100, 150, 300, 400, 1000]
    # AirDry = LL15 - 0.02, elementwise
    assert ((df["LL15"] - df["AirDry"]).round(3) == 0.02).all()
    # SAT capped at 0.697
    assert (df["SAT"] <= 0.697).all()
    # KS never zero (0 -> NaN for the caller to forward-fill)
    assert not (df["KS"] == 0).any()
    # whole-profile parameters ride along as attrs
    assert df.attrs["Salb"] == 0.13 and df.attrs["CN2Bare"] == 73.0


# --------------------------------------------------------------------------
# Orchestration: to_dssat / to_apsim (network-free via injected frames)
# --------------------------------------------------------------------------
def _season_weather_long(points):
    """Synthetic per-point season weather in get_season long format."""
    dates = pd.date_range("2020-09-14", "2021-02-28", freq="D")  # cross-year
    rng = np.random.default_rng(0)
    ranges = {"TMAX": (24, 30), "TMIN": (12, 16), "SRAD": (15, 22), "PRCP": (0, 20)}
    rows = []
    for pid in points.index:
        for v, (lo, hi) in ranges.items():
            for t in dates:
                rows.append({
                    "point": pid, "lon": points.lon[pid], "lat": points.lat[pid],
                    "time": t, "variable": v, "value": round(rng.uniform(lo, hi), 1),
                })
    return pd.DataFrame(rows)


def _soil_frame(points):
    return pd.DataFrame([_soil_row() for _ in range(len(points))], index=points.index)


def test_to_dssat_writes_per_point_files(tmp_path):
    from agwise_data.api import to_dssat

    pts = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                        "site": ["Kigali", "Nyagatare"]})
    res = to_dssat(
        pts, out_dir=tmp_path / "DSSAT", station_col="site",
        weather=_season_weather_long(pts), soil=_soil_frame(pts),
    )
    assert len(res) == 2
    for n, r in enumerate(res, start=1):
        assert r["wth"].name == f"WHTE{n:04d}.WTH"
        assert r["wth"].exists() and r["sol"].exists()
        assert r["dir"].name == f"EXTE{n:04d}"
    # the .WTH carries the station code and cross-year weather
    wth = res[0]["wth"].read_text().splitlines()
    assert wth[3].startswith("@ INSI")
    assert "KIGA" in wth[4]
    # first/last data dates straddle the New Year
    data = [ln for ln in wth if ln[:2].isdigit()]
    assert data[0].startswith("2020") and data[-1].startswith("2021")


def test_to_apsim_writes_met_and_soil_table(tmp_path):
    from agwise_data.api import to_apsim

    pts = pd.DataFrame({"lon": [30.06], "lat": [-1.95], "site": ["Kigali"]})
    res = to_apsim(
        pts, out_dir=tmp_path / "APSIM", station_col="site",
        weather=_season_weather_long(pts), soil=_soil_frame(pts),
    )
    assert len(res) == 1
    r = res[0]
    assert r["met"].name == "wth_loc_1.met" and r["met"].exists()
    assert r["soil"].name == "soil_1.csv" and r["soil"].exists()
    soil_lines = r["soil"].read_text().splitlines()
    assert soil_lines[0].startswith("# Salb=0.13 CN2Bare=73.0")
    assert soil_lines[1].split(",")[0] == "Depth"  # header row


def test_to_dssat_skips_point_without_weather(tmp_path):
    from agwise_data.api import to_dssat

    pts = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90]})
    weather = _season_weather_long(pts)
    weather = weather[weather["point"] == 0]  # drop point 1's weather
    res = to_dssat(pts, out_dir=tmp_path / "D", weather=weather, soil=_soil_frame(pts))
    assert len(res) == 1 and res[0]["point"] == 0


# --------------------------------------------------------------------------
# WOFOST writer (weather table + soil parameters)
# --------------------------------------------------------------------------
def _wofost_series():
    """A short daily frame with all six WOFOST weather inputs + edge cases."""
    dates = pd.date_range("2021-03-01", "2021-03-04", freq="D")
    return pd.DataFrame({
        "DATE": dates,
        "TMAX": [26.0, 26.0, 10.0, 26.0],   # day 3 crossed (tmax<tmin)
        "TMIN": [14.0, 14.0, 20.0, np.nan],  # day 4 has a NaN -> dropped
        "SRAD": [19.0, 19.0, 19.0, 19.0],    # MJ -> kJ
        "RHUM": [65.0, 65.0, 65.0, 65.0],    # %
        "WIND": [1.5, 1.5, 1.5, 1.5],
        "PRCP": [3.0, 0.0, 0.0, 0.0],
    })


def test_wofost_esat_kpa_matches_plantecophys():
    # plantecophys::esat (Jones 1992) at 20 C, sea level ~= 2.347 kPa.
    assert round(float(wofost.esat_kpa(20.0)), 3) == 2.347
    # actual vapour pressure at 70% RH is that x 0.70.
    assert round(float(0.70 * wofost.esat_kpa(20.0)), 3) == 1.643


def test_wofost_prepare_weather_units_and_cleaning():
    df = wofost.prepare_weather(_wofost_series())
    assert list(df.columns) == wofost.WOFOST_WEATHER_COLS
    # NaN row dropped (complete.cases); crossed day kept but swapped.
    assert len(df) == 3
    assert (df["tmin"] <= df["tmax"]).all()
    # SRAD scaled MJ -> kJ.
    assert (df["srad"] == 19000.0).all()
    # vapr = (RH/100) * esat_kPa(tmean); day 1 tmean=20, RH=65% -> 1.526 kPa.
    assert round(float(df["vapr"].iloc[0]), 3) == 1.526
    # day 3 (10/20 -> swapped to 10/20) tmean=15 -> lower vapr.
    assert df["vapr"].iloc[2] < df["vapr"].iloc[0]


def test_wofost_prepare_weather_missing_cols_raises():
    bad = _wofost_series().drop(columns=["WIND"])
    with pytest.raises(ValueError, match="WIND"):
        wofost.prepare_weather(bad)


def test_wofost_soil_params_topmeter_weighting():
    # Constant hydraulics across depths -> top-meter mean equals that constant.
    p = wofost.soil_params(_soil_row())
    prof = soil.build_profile(_soil_row())
    w = np.array([5, 10, 15, 30, 40.0])
    assert p["SMW"] == round(float((prof["pwp"][:5] * w).sum() / 100), 3)
    assert p["SMFCF"] == round(float((prof["fc"][:5] * w).sum() / 100), 3)
    assert p["SM0"] == round(float((prof["sat"][:5] * w).sum() / 100), 3)
    # K0: mm/h -> cm/day via 0.1 * 24.
    assert p["K0"] == round(0.1 * 24 * float((prof["ks"][:5] * w).sum() / 100), 2)
    # ordering SMW <= SMFCF <= SM0 (wilting <= field cap <= saturation).
    assert p["SMW"] <= p["SMFCF"] <= p["SM0"]
    # site-independent defaults present.
    assert p["RDMSOL"] == 150 and p["WAV"] == 50 and p["SMLIM"] == 1


def test_wofost_soil_params_defaults_overridable():
    p = wofost.soil_params(_soil_row(), defaults={"RDMSOL": 120, "WAV": 30})
    assert p["RDMSOL"] == 120 and p["WAV"] == 30


def test_wofost_write_weather_srad_is_plain_decimal(tmp_path):
    path = wofost.write_weather(_wofost_series(), tmp_path / "w.csv")
    text = path.read_text()
    assert "e+" not in text.lower()  # no scientific notation
    assert "19000" in text.splitlines()[1]


def _wofost_weather_long(points):
    """Synthetic per-point WOFOST season weather in get_season long format."""
    dates = pd.date_range("2020-09-14", "2021-02-28", freq="D")  # cross-year
    rng = np.random.default_rng(1)
    ranges = {"TMAX": (24, 30), "TMIN": (12, 16), "SRAD": (15, 22),
              "PRCP": (0, 20), "RHUM": (50, 90), "WIND": (0.5, 3)}
    rows = []
    for pid in points.index:
        for v, (lo, hi) in ranges.items():
            for t in dates:
                rows.append({
                    "point": pid, "lon": points.lon[pid], "lat": points.lat[pid],
                    "time": t, "variable": v, "value": round(rng.uniform(lo, hi), 1),
                })
    return pd.DataFrame(rows)


def test_to_wofost_writes_per_point_files(tmp_path):
    from agwise_data.api import to_wofost

    pts = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                        "site": ["Kigali", "Nyagatare"]})
    res = to_wofost(
        pts, out_dir=tmp_path / "WOFOST", station_col="site",
        weather=_wofost_weather_long(pts), soil=_soil_frame(pts),
    )
    assert len(res) == 2
    for n, r in enumerate(res, start=1):
        assert r["dir"].name == f"EXTE{n:04d}"
        assert r["weather"].name == f"weather_{n}.csv" and r["weather"].exists()
        assert r["soil"].name == f"soil_{n}.csv" and r["soil"].exists()
    # weather header is exactly the WOFOST columns
    header = res[0]["weather"].read_text().splitlines()[0]
    assert header == ",".join(wofost.WOFOST_WEATHER_COLS)
    # soil file is the long parameter table with the derived rows
    soil_txt = res[0]["soil"].read_text()
    assert soil_txt.splitlines()[0] == "parameter,value,units,note"
    for key in ("SMW", "SMFCF", "SM0", "K0", "RDMSOL"):
        assert key in soil_txt


# --------------------------------------------------------------------------
# ORYZA v3 writer (CABO weather + 8-layer PADDY soil)
# --------------------------------------------------------------------------
def _oryza_series():
    """Daily frame with the six weather inputs, straddling the New Year."""
    dates = pd.date_range("2020-12-30", "2021-01-03", freq="D")
    return pd.DataFrame({
        "DATE": dates,
        "TMAX": [26.0, 26.0, 10.0, 26.0, 26.0],  # day 3 crossed
        "TMIN": [14.0, 14.0, 20.0, 14.0, 14.0],
        "SRAD": [19.0, 19.0, 19.0, 19.0, 19.0],
        "RHUM": [65.0, 65.0, 65.0, 65.0, 65.0],
        "WIND": [1.5, 1.5, 1.5, 1.5, 1.5],
        "PRCP": [3.0, 0.0, 0.0, 0.0, 2.0],
    })


def test_oryza_year_ext():
    assert oryza._year_ext(1998) == "998"
    assert oryza._year_ext(2021) == "021"
    assert oryza._year_ext(2000) == "000"


def test_oryza_prepare_weather_units_and_vapr():
    df = oryza.prepare_weather(_oryza_series())
    assert list(df.columns) == ["date", "srad", "tmin", "tmax", "vapr", "wind", "rain"]
    assert (df["srad"] == 19000.0).all()          # MJ -> kJ
    assert (df["tmin"] <= df["tmax"]).all()        # crossed day swapped
    assert len(df) == 5                            # rows kept (no drop)
    # FAO-56: vapr = 0.5*(esat(tmax)+esat(tmin)) * RH/100; day1 26/14, RH65%.
    exp = 0.5 * (float(wofost.esat_kpa(26.0)) + float(wofost.esat_kpa(14.0))) * 0.65
    assert round(float(df["vapr"].iloc[0]), 4) == round(exp, 4)


def test_oryza_write_weather_splits_by_year(tmp_path):
    paths = oryza.write_weather(
        _oryza_series(), lat=-1.9, lon=30.1, out_dir=tmp_path,
        id_name="Musanze", stn=1, elev=1850,
    )
    names = sorted(p.name for p in paths)
    assert names == ["MUSA1.020", "MUSA1.021"]     # one file per calendar year
    txt = [p for p in paths if p.name.endswith(".020")][0].read_text().splitlines()
    # station line: lon,lat,elev,angstromA,angstromB
    station = [ln for ln in txt if ln.startswith("30.1,")][0]
    assert station == "30.1,-1.9,1850,0.25,0.5"
    # data line: stn,year,day,srad,tmin,tmax,vapr,wind,rain — Dec 30 2020 = doy 365
    data = [ln for ln in txt if ln.startswith("1,2020,")][0].split(",")
    assert data[:3] == ["1", "2020", "365"] and data[3] == "19000.0"


def test_oryza_soil_layers_remap_and_units():
    L = oryza.soil_layers(_soil_row())
    assert L["n_layers"] == 8
    assert list(L["tkl_m"]) == [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.30, 0.40]
    # 6 SoilGrids depths remapped onto 8 layers via [0,1,1,2,2,2,3,4]:
    # layers 2 and 3 share SoilGrids depth 1, so their clay is equal.
    assert L["clayx"][1] == L["clayx"][2]
    assert L["clayx"][3] == L["clayx"][4] == L["clayx"][5]
    # CLAYX/SANDX are fractions (0-1); retention ordering holds.
    assert (L["clayx"] <= 1.0).all() and (L["sandx"] <= 1.0).all()
    assert (L["wcst"] >= L["wcfc"]).all()
    assert (L["wcfc"] >= L["wcwp"]).all()
    assert (L["wcwp"] >= L["wcad"]).all()
    # KST = Saxton ks (mm/h) * 2.4 -> cm/day
    prof = soil.build_profile(_soil_row())
    assert round(float(L["kst"][0]), 4) == round(float(prof["ks"][0] * 2.4), 4)
    # SOC kg/ha = 1000 * thickness(cm) * BD * OC%
    exp_soc0 = 1000.0 * 5.0 * prof["bdod"][0] * prof["sloc"][0]
    assert round(float(L["soc"][0]), 1) == round(exp_soc0, 1)


def test_oryza_write_soil_structure(tmp_path):
    path = oryza.write_soil(_soil_row(), tmp_path / "soil.sol", id_name="TEST")
    txt = path.read_text()
    assert "SCODE = 'PADDY'" in txt
    assert "NL = 8" in txt
    for key in ("TKL =", "CLAYX =", "SANDX =", "BD =", "SOC =", "SON =",
                "KST =", "WCST =", "WCFC =", "WCWP =", "WCAD =", "WCLINT ="):
        assert key in txt
    # every per-layer vector must have 8 comma-separated values
    for line in txt.splitlines():
        if line.startswith(("CLAYX =", "KST =", "WCST =", "TKL =")):
            vals = line.split("=", 1)[1].split("!")[0].split(",")
            assert len([v for v in vals if v.strip()]) == 8


def test_to_oryza_writes_per_point_files(tmp_path):
    from agwise_data.api import to_oryza

    pts = pd.DataFrame({"lon": [30.06], "lat": [-1.95], "site": ["Kigali"]})
    res = to_oryza(
        pts, out_dir=tmp_path / "ORYZA", station_col="site",
        weather=_wofost_weather_long(pts), soil=_soil_frame(pts),
    )
    assert len(res) == 1
    r = res[0]
    assert r["dir"].name == "EXTE0001"
    assert r["soil"].name == "soil_1.sol" and r["soil"].exists()
    # cross-year season (2020-09..2021-02) -> two weather files
    assert len(r["weather"]) == 2
    assert all(w.exists() for w in r["weather"])
    exts = sorted(w.suffix for w in r["weather"])
    assert exts == [".020", ".021"]
