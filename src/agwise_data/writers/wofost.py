"""WOFOST inputs: weather table + soil-parameter table from the layer's data.

WOFOST (as run through the R ``meteor``/``Rwofost`` packages) reads its weather
and soil as in-memory R *lists*, not files — there is no portable on-disk format
to round-trip against. So the "last mile" for WOFOST is a pair of tidy CSVs the
module reads straight into those lists, retiring the per-use-case
``WOFOST/grid/5a_prepare_list_weather.r`` and ``5c_prepare_list_soil.r``:

* a **weather** table with WOFOST's exact columns/units
  (``date, srad, tmin, tmax, vapr, wind, prec``), and
* a **soil** parameter table (the moisture-retention + conductivity values
  WOFOST needs, derived from the Saxton-Rawls hydraulics in :mod:`.soil`,
  plus the site-independent defaults the legacy script set).

Units WOFOST expects (reagro.org/methods/explanatory/wofost/{weather,soil}):
``srad`` kJ m-2 day-1, ``tmin``/``tmax`` degC, ``vapr`` **kPa**, ``wind`` m s-1,
``prec`` mm day-1. The layer delivers SRAD in MJ m-2 day-1 (x1000 -> kJ) and
RHUM in percent; ``vapr`` is the actual vapour pressure derived from RHUM and
the mean temperature.

.. note:: vapour-pressure fix. The legacy ``5a`` computed
   ``vapr = 1000 * rh * 0.01 * esat(tmean)`` where ``esat`` is
   ``plantecophys::esat`` — which returns saturation vapour pressure in **Pa**
   (its sibling ``RHtoVPD`` divides that ``esat`` by 1000 to reach kPa). So the
   correct actual vapour pressure in kPa is ``(rh/100) * esat_Pa / 1000``; the
   legacy ``* 1000`` made ``vapr`` 1e6x too large. We compute the physically
   correct kPa value directly (same Jones-1992/plantecophys ``esat`` curve,
   returned in kPa).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from . import soil as soil_w

# WOFOST daily weather columns, in order, with their units.
WOFOST_WEATHER_COLS = ["date", "srad", "tmin", "tmax", "vapr", "wind", "prec"]
WOFOST_WEATHER_UNITS = {
    "srad": "kJ m-2 day-1", "tmin": "degC", "tmax": "degC",
    "vapr": "kPa", "wind": "m s-1", "prec": "mm day-1",
}

# Canonical (short) inputs the weather table needs from the layer.
_WEATHER_INPUTS = ["TMAX", "TMIN", "SRAD", "RHUM", "WIND", "PRCP"]

# WOFOST top-meter soil-moisture parameters average over the first five
# SoilGrids intervals (0-5..60-100 cm); the layer thicknesses are the weights.
_TOPMETER_N = 5
_TOPMETER_W = np.array([5.0, 10.0, 15.0, 30.0, 40.0])  # cm; sum = 100


def esat_kpa(tdegc, pa: float = 101.0):
    """Saturation vapour pressure (kPa) from air temperature (degC).

    Direct port of ``plantecophys::esat`` (Jones 1992), which returns Pa
    (``a = 611.21``); here the coefficient is in kPa (``0.61121``) so the
    result is kPa. ``pa`` is atmospheric pressure in kPa (default sea level,
    ~101 kPa) and enters only through the small pressure-enhancement factor.
    Scalar or array input.
    """
    a, b, c = 0.61121, 17.502, 240.97
    f = 1.0007 + 3.46e-8 * pa * 1000.0
    return f * a * np.exp(b * np.asarray(tdegc, dtype="float64") / (c + np.asarray(tdegc, dtype="float64")))


def prepare_weather(daily: pd.DataFrame) -> pd.DataFrame:
    """Build the WOFOST weather table from a per-point daily frame.

    ``daily`` needs a date column (``DATE``/``date``/``time``) and the six
    short-name columns ``TMAX, TMIN, SRAD, RHUM, WIND, PRCP`` (``RAIN`` is
    accepted for ``PRCP``). Returns a frame with WOFOST's exact columns
    (:data:`WOFOST_WEATHER_COLS`) and units: SRAD scaled MJ->kJ, ``vapr`` the
    actual vapour pressure (kPa) from RHUM and mean temperature. Rows are
    sorted by date, any TMIN>TMAX day is swapped (as the legacy scripts did),
    and — matching the legacy ``complete.cases`` — only rows with every
    required value present are kept (WOFOST needs a gapless daily series).
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
            f"Weather frame is missing {missing}; WOFOST needs TMAX, TMIN, "
            "SRAD, RHUM, WIND and PRCP (RAIN accepted for PRCP)."
        )

    df["date"] = pd.to_datetime(df["date"])
    for c in _WEATHER_INPUTS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)

    # Guarantee TMIN <= TMAX (some pixels/days have them crossed).
    crossed = df["TMIN"] > df["TMAX"]
    if crossed.any():
        tmin = df.loc[crossed, "TMIN"].copy()
        df.loc[crossed, "TMIN"] = df.loc[crossed, "TMAX"]
        df.loc[crossed, "TMAX"] = tmin

    tmean = (df["TMIN"] + df["TMAX"]) / 2.0
    out = pd.DataFrame({
        "date": df["date"],
        "srad": df["SRAD"] * 1000.0,                 # MJ -> kJ m-2 day-1
        "tmin": df["TMIN"],
        "tmax": df["TMAX"],
        "vapr": (df["RHUM"] / 100.0) * esat_kpa(tmean),   # actual VP, kPa
        "wind": df["WIND"],
        "prec": df["PRCP"],
    })
    # WOFOST assumes consecutive days with no gaps; keep only complete rows.
    out = out.dropna(subset=[c for c in WOFOST_WEATHER_COLS if c != "date"])
    return out.reset_index(drop=True)


def write_weather(daily: pd.DataFrame, path) -> Path:
    """Write one WOFOST weather CSV (:data:`WOFOST_WEATHER_COLS`). Returns path."""
    df = prepare_weather(daily)
    if df.empty:
        raise ValueError("No complete weather rows to write")
    df = df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    # Per-column rounding so srad prints as plain kJ (not 1.9e+04).
    for col, nd in (("srad", 0), ("tmin", 1), ("tmax", 1),
                    ("vapr", 3), ("wind", 2), ("prec", 2)):
        df[col] = df[col].round(nd)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------
# Soil
# --------------------------------------------------------------------------
def _wmean_topmeter(values: np.ndarray) -> float:
    """Thickness-weighted mean over the top-meter layers, tolerant of NaN."""
    v = np.asarray(values, dtype="float64")[:_TOPMETER_N]
    w = _TOPMETER_W[: len(v)]
    ok = np.isfinite(v)
    if not ok.any():
        return float("nan")
    return float(np.sum(v[ok] * w[ok]) / np.sum(w[ok]))


# Site-independent WOFOST soil parameters. In the legacy 5c these came from the
# ``wofost_soil("ec1")`` template plus the explicit overrides listed here; only
# SMW/SMFCF/SM0/K0 are data-derived. Exposed as overridable defaults so a module
# can change them without editing the writer.
_SOIL_DEFAULTS = {
    "IDRAIN": (0, "-", "presence (1) / absence (0) of drains"),
    "WAV": (50, "cm", "initial water above wilting point"),
    "ZTI": (150, "cm", "initial groundwater-table depth"),
    "RDMSOL": (150, "cm", "maximum rooting depth of the soil"),
    "NOTINF": (0, "-", "non-infiltrating fraction of rainfall"),
    "SSI": (0, "cm", "initial surface storage"),
    "SMLIM": (1, "cm3 cm-3", "limiting moisture in the upper layer"),
}


def soil_params(
    soil: Mapping,
    depths: Sequence[str] = soil_w.DEPTH_LABELS,
    defaults: Optional[Mapping] = None,
) -> Dict[str, float]:
    """WOFOST soil moisture/conductivity parameters from a soil-point row.

    ``soil`` is one row of :func:`agwise_data.extract_static_points` (the six
    SoilGrids depths for CLAY/SAND/SILT/SOC/... — see :func:`.soil.build_profile`).
    Returns a dict with the data-derived parameters averaged over the top metre:

    * ``SMW``   – moisture at wilting point (cm3 cm-3) = mean Saxton PWP
    * ``SMFCF`` – moisture at field capacity (cm3 cm-3) = mean Saxton FC
    * ``SM0``   – moisture at saturation (cm3 cm-3) = mean Saxton SAT
    * ``K0``    – saturated hydraulic conductivity (cm day-1); Saxton KS
      (mm h-1) converted with the legacy factor ``0.1 * 24``

    plus the site-independent WOFOST defaults (:data:`_SOIL_DEFAULTS`,
    overridable via ``defaults``).
    """
    p = soil_w.build_profile(soil, depths)
    params: Dict[str, float] = {
        "SMW": round(_wmean_topmeter(p["pwp"]), 3),
        "SMFCF": round(_wmean_topmeter(p["fc"]), 3),
        "SM0": round(_wmean_topmeter(p["sat"]), 3),
        "K0": round(0.1 * 24.0 * _wmean_topmeter(p["ks"]), 2),  # mm h-1 -> cm d-1
    }
    d = dict(_SOIL_DEFAULTS)
    for k, v in (defaults or {}).items():
        base = d.get(k)
        d[k] = (v, base[1], base[2]) if base else (v, "", "")
    for k, (val, _unit, _note) in d.items():
        params[k] = val
    return params


def soil_table(
    soil: Mapping,
    depths: Sequence[str] = soil_w.DEPTH_LABELS,
    defaults: Optional[Mapping] = None,
) -> pd.DataFrame:
    """Long-format ``parameter, value, units, note`` table of :func:`soil_params`."""
    params = soil_params(soil, depths, defaults)
    derived_units = {"SMW": "cm3 cm-3", "SMFCF": "cm3 cm-3", "SM0": "cm3 cm-3",
                     "K0": "cm day-1"}
    derived_note = {
        "SMW": "top-meter mean Saxton PWP",
        "SMFCF": "top-meter mean Saxton FC",
        "SM0": "top-meter mean Saxton SAT",
        "K0": "top-meter mean Saxton KS",
    }
    rows = []
    for k, v in params.items():
        if k in derived_units:
            rows.append((k, v, derived_units[k], derived_note[k]))
        else:
            _val, unit, note = _SOIL_DEFAULTS.get(k, (v, "", "default"))
            rows.append((k, v, unit, note))
    return pd.DataFrame(rows, columns=["parameter", "value", "units", "note"])


def write_soil(
    soil: Mapping,
    path,
    depths: Sequence[str] = soil_w.DEPTH_LABELS,
    defaults: Optional[Mapping] = None,
) -> Path:
    """Write one WOFOST soil-parameter CSV (long format). Returns the path."""
    df = soil_table(soil, depths, defaults)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path
