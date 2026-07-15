"""ORYZA v3 inputs: CABO weather files + an 8-layer PADDY soil file.

The "last mile" for the ORYZA (v3) rice model, retiring the per-use-case
``Oryza/OryzaDataFiles.R`` (``oryza.soil`` / weather prep). ORYZA reads two
plain-text inputs the layer can produce from its own data:

* **Weather** — the classic CABO format, one file per calendar year named
  ``<code><stn>.<yyy>`` (``.998`` = 1998, ``.021`` = 2021). A season that
  straddles the New Year therefore yields two files. Columns are ORYZA's:
  ``station, year, day, srad, tmin, tmax, vapr, wind, rain`` with SRAD in
  **kJ m-2 day-1**, ``vapr`` the actual vapour pressure in **kPa**, wind in
  m s-1 and rain in mm; missing values are written ``-99``.
* **Soil** — an 8-layer ``PADDY`` ``.sol``. SoilGrids' six depths are remapped
  onto ORYZA's fixed 8 layers (thicknesses 0.05 m ×6, 0.30, 0.40 = top metre)
  and the Saxton-Rawls hydraulics (:mod:`.soil`) fill the retention/conductivity
  block. Units follow the ORYZA PADDY spec: WCST/WCFC/WCWP/WCAD in m3 m-3,
  KST in cm day-1, CLAYX/SANDX as fractions, BD in g cm-3, SOC/SON in kg ha-1.

Formats verified against the IRRI ORYZA documentation and the maintained
``agroclimR`` writers (there is no clean round-trip reader, so structure is
matched rather than parsed back).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from . import soil as soil_w
from ._common import station_code
from .wofost import esat_kpa

# ORYZA's fixed 8-layer scheme (top metre) and how SoilGrids' six depths map
# onto it: each ORYZA layer takes the SoilGrids interval containing its midpoint.
ORYZA_TKL_CM = np.array([5, 5, 5, 5, 5, 5, 30, 40], dtype="float64")
_SG_INDEX = [0, 1, 1, 2, 2, 2, 3, 4]  # -> DEPTH_LABELS[0..4]; 100-200 unused
N_LAYERS = len(ORYZA_TKL_CM)

# Short-name weather inputs the CABO file needs from the layer.
_WEATHER_INPUTS = ["TMAX", "TMIN", "SRAD", "RHUM", "WIND", "PRCP"]
ORYZA_WEATHER_COLS = ["station", "year", "day", "srad", "tmin", "tmax",
                      "vapr", "wind", "rain"]
_MISSING = -99.0


# --------------------------------------------------------------------------
# Weather (CABO format)
# --------------------------------------------------------------------------
def prepare_weather(daily: pd.DataFrame) -> pd.DataFrame:
    """Build the ORYZA weather frame from a per-point daily frame.

    ``daily`` needs a date column and the six short-name columns ``TMAX, TMIN,
    SRAD, RHUM, WIND, PRCP`` (``RAIN`` accepted for ``PRCP``). Returns a frame
    with ``date`` plus ORYZA's ``srad`` (kJ m-2 day-1), ``tmin``, ``tmax``,
    ``vapr`` (kPa), ``wind`` (m s-1) and ``rain`` (mm), sorted by date and with
    any TMIN>TMAX day swapped. Rows are **kept** (ORYZA needs a continuous
    daily series); individual missing values become ``-99`` at write time.
    ``vapr`` is the FAO-56 actual vapour pressure
    ``0.5*(esat(tmax)+esat(tmin)) * RH/100`` (kPa).
    """
    df = daily.copy()
    date_col = next(
        (c for c in ("DATE", "date", "time", "Date") if c in df.columns), None
    )
    if date_col is None:
        raise ValueError(
            f"No date column found (looked for DATE/date/time); got {list(df.columns)}"
        )
    df = df.rename(columns={date_col: "date"})
    if "PRCP" not in df.columns and "RAIN" in df.columns:
        df = df.rename(columns={"RAIN": "PRCP"})
    missing = [c for c in _WEATHER_INPUTS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Weather frame is missing {missing}; ORYZA needs TMAX, TMIN, SRAD, "
            "RHUM, WIND and PRCP (RAIN accepted for PRCP)."
        )

    df["date"] = pd.to_datetime(df["date"])
    for c in _WEATHER_INPUTS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    crossed = df["TMIN"] > df["TMAX"]
    if crossed.any():
        tmin = df.loc[crossed, "TMIN"].copy()
        df.loc[crossed, "TMIN"] = df.loc[crossed, "TMAX"]
        df.loc[crossed, "TMAX"] = tmin

    # FAO-56 mean saturation vapour pressure -> actual VP (kPa).
    es_mean = 0.5 * (esat_kpa(df["TMAX"]) + esat_kpa(df["TMIN"]))
    vapr = (df["RHUM"] / 100.0) * es_mean

    return pd.DataFrame({
        "date": df["date"],
        "srad": df["SRAD"] * 1000.0,   # MJ -> kJ m-2 day-1
        "tmin": df["TMIN"],
        "tmax": df["TMAX"],
        "vapr": vapr,
        "wind": df["WIND"],
        "rain": df["PRCP"],
    })


def _fmt_wth(x, nd: int) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return f"{_MISSING:.0f}"
    return f"{float(x):.{nd}f}"


def _year_ext(year: int) -> str:
    """ORYZA year extension: last three digits (1998 -> '998', 2021 -> '021')."""
    return f"{year % 1000:03d}"


def _weather_header(id_name, lon, lat, elev, first, last) -> List[str]:
    return [
        "*-----------------------------------------------------------",
        f"*  Station name: {id_name}",
        "*  ORYZA v3 weather file - by agwise-data",
        f"*  Longitude: {lon} -- Latitude: {lat} -- Elevation: {elev} m",
        f"*  Period: {first:%Y-%m-%d} : {last:%Y-%m-%d}",
        "*  -99.: nil value",
        "*",
        "*  Column    Daily value",
        "*     1      Station number",
        "*     2      Year",
        "*     3      Day",
        "*     4      irradiance        kJ m-2 d-1",
        "*     5      min temperature           oC",
        "*     6      max temperature           oC",
        "*     7      vapour pressure          kPa",
        "*     8      mean wind speed        m s-1",
        "*     9      precipitation         mm d-1",
        "*-----------------------------------------------------------",
    ]


def write_weather(
    daily: pd.DataFrame,
    lat: float,
    lon: float,
    out_dir,
    id_name: str = "AGWS",
    stn: int = 1,
    elev: float = 0.0,
    angstrom=(0.25, 0.50),
) -> List[Path]:
    """Write ORYZA CABO weather files (one per calendar year). Returns the paths.

    Files are ``<out_dir>/<code><stn>.<yyy>`` where ``code`` is the 4-char
    station code from ``id_name``. ``angstrom`` are the (A, B) coefficients on
    the station line — unused when irradiance is supplied directly, as here.
    """
    df = prepare_weather(daily)
    if df.empty:
        raise ValueError("No weather rows to write")
    code = station_code(id_name)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    a, b = angstrom
    station_line = f"{lon},{lat},{elev},{a},{b}"

    paths: List[Path] = []
    for year, grp in df.groupby(df["date"].dt.year):
        lines = _weather_header(id_name, lon, lat, elev,
                                grp["date"].min(), grp["date"].max())
        lines.append(station_line)
        for row in grp.itertuples(index=False):
            doy = row.date.timetuple().tm_yday
            lines.append(
                f"{stn},{year},{doy},"
                f"{_fmt_wth(row.srad, 1)},{_fmt_wth(row.tmin, 1)},"
                f"{_fmt_wth(row.tmax, 1)},{_fmt_wth(row.vapr, 3)},"
                f"{_fmt_wth(row.wind, 1)},{_fmt_wth(row.rain, 1)}"
            )
        path = out_dir / f"{code}{stn}.{_year_ext(int(year))}"
        path.write_text("\n".join(lines) + "\n")
        paths.append(path)
    return paths


# --------------------------------------------------------------------------
# Soil (8-layer PADDY .sol)
# --------------------------------------------------------------------------
def soil_layers(
    soil: Mapping, depths: Sequence[str] = soil_w.DEPTH_LABELS
) -> Dict[str, object]:
    """Compute ORYZA's 8 PADDY layers from a soil-point row.

    Uses :func:`.soil.build_profile` (Saxton-Rawls) for the six SoilGrids
    depths, then remaps them onto ORYZA's fixed 8 layers. Returns per-layer
    arrays in ORYZA units: CLAYX/SANDX (fraction), BD (g cm-3), WCST/WCFC/WCWP/
    WCAD (m3 m-3), KST (cm day-1), SOC/SON (kg ha-1), plus TKL (m) and the
    per-layer USDA texture class name.
    """
    p = soil_w.build_profile(soil, depths)
    idx = _SG_INDEX
    tkl_cm = ORYZA_TKL_CM

    def pick(arr):
        return np.array([arr[j] for j in idx], dtype="float64")

    clay = pick(p["clay"]); sand = pick(p["sand"]); silt = pick(p["silt"])
    bd = pick(p["bdod"]); sloc = pick(p["sloc"]); slni = pick(p["slni"])
    pwp = pick(p["pwp"]); fc = pick(p["fc"]); sat = pick(p["sat"]); ks = pick(p["ks"])

    # kg ha-1 per layer = 1000 * thickness(cm) * BD(g cm-3) * content(%)
    soc = 1000.0 * tkl_cm * bd * sloc
    son = 1000.0 * tkl_cm * bd * slni
    texture = [soil_w.texture_class(clay[i] / 100.0, silt[i] / 100.0)
               for i in range(N_LAYERS)]

    return {
        "n_layers": N_LAYERS,
        "tkl_m": tkl_cm / 100.0,
        "clayx": clay / 100.0,
        "sandx": sand / 100.0,
        "bd": bd,
        "wcst": sat,
        "wcfc": fc,
        "wcwp": pwp,
        "wcad": 0.5 * pwp,           # air-dry ~= half wilting point (no direct PTF)
        "kst": ks * 2.4,             # mm h-1 -> cm day-1 (x24 /10)
        "soc": soc,
        "son": son,
        "texture": texture,
    }


def _fmt_vec(vals, nd: int = 2) -> str:
    return ", ".join(f"{float(v):.{nd}f}" for v in vals)


def write_soil(
    soil: Mapping,
    path,
    id_name: str = "AGWISE",
    depths: Sequence[str] = soil_w.DEPTH_LABELS,
    zrtms: float = 0.5,
    wl0mx: float = 100.0,
    wl0i: float = 0.0,
    riwcli: str = "NO",
    satav: float = 20.0,
    snh4: float = 0.0,
    sno3: float = 0.0,
) -> Path:
    """Write one 8-layer ORYZA PADDY ``.sol`` file. Returns the path.

    The retention/conductivity/texture block is filled from ``soil`` (SoilGrids
    + Saxton-Rawls); the water-balance switches follow the non-puddled ORYZA
    template (``SWITPD=0``, ``SWITGW=0``, ``SWITVP=-1``, data-driven retention
    and conductivity). Model/management scalars are exposed as overridable
    defaults: ``zrtms`` (max root depth, m), ``wl0mx`` (bund height, mm),
    ``wl0i`` (initial ponding, mm), ``riwcli`` (re-init switch), ``satav``
    (annual mean soil temperature, degC), ``snh4``/``sno3`` (initial mineral N
    per layer, kg ha-1 — 0 = no data). Initial water content defaults to field
    capacity.
    """
    L = soil_layers(soil, depths)
    nl = L["n_layers"]
    tkl_cm = ORYZA_TKL_CM
    # SNH4X/SNO3X (kg ha-1 per layer) from a per-mass default: kg/ha = 1000*TKL_cm*BD*(mg/kg)/1000
    snh4x = 0.1 * tkl_cm * L["bd"] * snh4
    sno3x = 0.1 * tkl_cm * L["bd"] * sno3
    fixperc = L["kst"][-1] / 10.0
    dplowpan = float(np.sum(L["tkl_m"]))

    lines: List[str] = []
    a = lines.append
    a("**********************************************************************")
    a("* Template soil data file for PADDY soil water balance model.        *")
    a("**********************************************************************")
    a(f"* Soil        : {id_name} - texture classes: " + "-".join(L["texture"]))
    a(f"* File name    : {Path(path).name}")
    a("* Source       : SoilGrids + Saxton-Rawls pedotransfer (agwise-data)")
    a("*--------------------------------------------------------------------*")
    a("")
    a("SCODE = 'PADDY'")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 1. Various soil and management parameters")
    a("*---------------------------------------------------------------*")
    a(f"WL0MX = {wl0mx:.1f}   ! Bund height (mm)")
    a(f"NL = {nl}        ! Number of soil layers (maximum is 10) (-)")
    a(f"TKL = {_fmt_vec(L['tkl_m'])}   ! Thickness of each soil layer (m)")
    a(f"ZRTMS = {zrtms}   ! Maximum rooting depth in the soil (m)")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 2. Puddling switch: 1=PUDDLED or 0=NON PUDDLED")
    a("*---------------------------------------------------------------*")
    a("SWITPD = 0  ! Non puddled")
    a("NLPUD = 1")
    a(f"WCSTRP = {_fmt_vec(L['wcst'])}")
    a("PFCR = 6.0")
    a(f"DPLOWPAN = {dplowpan:.2f}")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 3. Groundwater switch: 0=DEEP, 1=DATA, 2=CALCULATE")
    a("*---------------------------------------------------------------*")
    a("SWITGW = 0")
    a("ZWTB =   1.,200.,")
    a("       366.,200.")
    a("ZWTBI = 100. ! Initial groundwater table depth (cm)")
    a("MINGW = 100. ! Minimum groundwater table depth (cm)")
    a("MAXGW = 100. ! Maximum groundwater table depth (cm)")
    a("ZWA   = 1.0  ! Receding rate of groundwater with no recharge (cm d-1)")
    a("ZWB   = 0.5  ! Sensitivity factor of groundwater recharge (-)")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 4. Percolation switch (cannot be 1 for non-puddled soil)")
    a("*---------------------------------------------------------------*")
    a("SWITVP = -1 ! Fixed percolation rate")
    a(f"FIXPERC = {fixperc:.2f}")
    a("PTABLE =")
    a("  1., 1.0,")
    a(" 50., 1.0,")
    a("100., 20.0,")
    a("366., 20.0")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 5. Conductivity switch: 0=NO DATA, 1=VAN GENUCHTEN, 2=POWER, 3=SPAW")
    a("*---------------------------------------------------------------*")
    a("SWITKH = 0 ! No data")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 6. Water retention switch: 0=DATA; 1=VAN GENUCHTEN")
    a("*---------------------------------------------------------------*")
    a("SWITPF = 0  ! Data")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 7. Soil physical properties")
    a("*---------------------------------------------------------------*")
    a(f"CLAYX = {_fmt_vec(L['clayx'])}")   # clay fraction (-)
    a(f"SANDX = {_fmt_vec(L['sandx'])}")   # sand fraction (-)
    a(f"BD = {_fmt_vec(L['bd'])}")         # bulk density (g cm-3)
    a(f"SOC = {_fmt_vec(L['soc'])}")       # organic C (kg C ha-1)
    a(f"SON = {_fmt_vec(L['son'])}")       # organic N (kg N ha-1)
    a(f"SNH4X = {_fmt_vec(snh4x)}")        # NH4-N (kg N ha-1)
    a(f"SNO3X = {_fmt_vec(sno3x)}")        # NO3-N (kg N ha-1)
    a("")
    a("*---------------------------------------------------------------*")
    a("* 8. Soil hydrological properties")
    a("*---------------------------------------------------------------*")
    a(f"KST = {_fmt_vec(L['kst'])}")       # sat. hydraulic conductivity (cm d-1)
    a(f"WCST = {_fmt_vec(L['wcst'], 3)}")  # saturation (m3 m-3)
    a(f"WCFC = {_fmt_vec(L['wcfc'], 3)}")  # field capacity (m3 m-3)
    a(f"WCWP = {_fmt_vec(L['wcwp'], 3)}")  # wilting point (m3 m-3)
    a(f"WCAD = {_fmt_vec(L['wcad'], 3)}")  # air dryness (m3 m-3)
    a("")
    a("*---------------------------------------------------------------*")
    a("* 9. Initialization conditions")
    a("*---------------------------------------------------------------*")
    a(f"WL0I = {wl0i:.1f}")
    a(f"WCLI = {_fmt_vec(L['wcfc'], 3)}")  # initial water content = field capacity
    a(f"RIWCLI = '{riwcli}'")
    a("")
    a("*---------------------------------------------------------------*")
    a("* 10. Initialization of soil thermal conditions")
    a("*---------------------------------------------------------------*")
    a(f"SATAV = {satav:.1f}")
    soilt = [round(satav + 2.0 - 4.0 * i / max(nl - 1, 1), 1) for i in range(nl)]
    a("SOILT = " + ", ".join(f"{v:.1f}" for v in soilt))
    a("")
    a("*---------------------------------------------------------------*")
    a("* 11. Observations (layer interpolation groups)")
    a("*---------------------------------------------------------------*")
    wclint = [f"         {i+1},{i+1},{i+1}," for i in range(nl)]
    wclint[0] = "WCLINT = " + wclint[0].strip()
    wclint[-1] = wclint[-1].rstrip(",")
    lines.extend(wclint)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
