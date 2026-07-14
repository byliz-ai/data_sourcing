"""Soil inputs for crop models: pedotransfer + DSSAT .SOL + APSIM soil table.

The soil half of the "last mile". The layer sources SoilGrids texture/carbon/
etc. at points (``extract_static_points``); crop models need *hydraulic*
properties on top of that. This module applies the same Saxton & Rawls (2006)
pedotransfer functions and texture-derived parameters the legacy AgWise
``readGeo_CM`` scripts used, then writes:

* a DSSAT ``.SOL`` profile (text; DSSAT ``read_sol`` reads it back), and
* an APSIM soil-layer table (the per-layer values the apsimx soil profile
  needs — LL15/DUL/SAT/AirDry/KS/BD/Carbon/clay/silt/N/PH/CEC + Salb/CN2 —
  so the module slots them into its template instead of recomputing them).

Units in (from the harmonized layer): clay/sand/silt %, SOC & N g/kg, PH,
CEC cmol(c)/kg, BDOD g/cm3. See ``harmonize.STATIC_VARS``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

# SoilGrids depth intervals -> the DSSAT layer bottom depth (cm) and the
# extract_static_points column suffix.
DEPTH_LABELS = ["0_5cm", "5_15cm", "15_30cm", "30_60cm", "60_100cm", "100_200cm"]
SLB_BOTTOM = [5, 15, 30, 60, 100, 200]
# APSIM layer thickness (mm) per interval.
THICKNESS_MM = [50, 100, 150, 300, 400, 1000]

# Texture triangle classes and their DSSAT-side properties, in the class order
# the legacy readGeo_CM used (so the lookups line up).
_TEXTURE_NAMES = [
    "clay", "silty clay", "sandy clay", "clay loam", "silty clay loam",
    "sandy clay loam", "loam", "silty loam", "sandy loam", "silt",
    "loamy sand", "sand", "NO DATA",
]
_TEXTURE_CODES = [
    "C", "SIC", "SC", "CL", "SICL", "SCL", "L", "SIL", "SL", "SI", "LS", "S",
    "NO DATA",
]
_ALBEDO = [0.12, 0.12, 0.13, 0.13, 0.12, 0.13, 0.13, 0.14, 0.13, 0.13, 0.16,
           0.19, 0.13]
_CN2 = [73.0, 73.0, 73.0, 73.0, 73.0, 73.0, 73.0, 73.0, 68.0, 73.0, 68.0,
        68.0, 73.0]


def saxton_rawls(clay, sand, som):
    """Saxton & Rawls (2006) hydraulics from texture + organic matter.

    ``clay``/``sand`` in percent (0-100), ``som`` = soil organic matter in
    percent. Returns ``(pwp, fc, sat, ks)``: permanent wilting point, field
    capacity and saturation (all cm3/cm3) and saturated conductivity (mm/h).
    Scalar or numpy-array inputs. This is a direct port of the equations in
    ``get_geoSpatialData_V2.R``.
    """
    s = np.asarray(sand, dtype="float64") / 100.0
    c = np.asarray(clay, dtype="float64") / 100.0
    om = np.asarray(som, dtype="float64")

    pwp = (-0.024 * s + 0.487 * c + 0.006 * om + 0.005 * (s * om)
           - 0.013 * (c * om) + 0.068 * (s * c) + 0.031)
    pwp = pwp + (0.14 * pwp - 0.02)

    fc = (-0.251 * s + 0.195 * c + 0.011 * om + 0.006 * (s * om)
          - 0.027 * (c * om) + 0.452 * (s * c) + 0.299)
    fc = fc + (1.283 * fc**2 - 0.374 * fc - 0.015)

    sat = (0.278 * s + 0.034 * c + 0.022 * om - 0.018 * (s * om)
           - 0.027 * (c * om) - 0.584 * (s * c) + 0.078)
    sat = sat + (0.636 * sat - 0.107)
    sat = fc + sat - 0.097 * s + 0.043

    with np.errstate(invalid="ignore", divide="ignore"):
        b = (math.log(1500) - math.log(33)) / (np.log(fc) - np.log(pwp))
        lam = 1.0 / b
        ks = 1930.0 * (sat - fc) ** (3.0 - lam)
    return pwp, fc, sat, ks


def texture_class(clay_frac: float, silt_frac: float) -> str:
    """USDA texture class name from clay/silt *fractions* (0-1).

    Port of the ``texture_class`` triangle in the AgWise scripts (apsimx).
    """
    if not (0.0 <= clay_frac <= 1.0) or not (0.0 <= silt_frac <= 1.0):
        return "NO DATA"
    ic, si = clay_frac, silt_frac
    sa = 1.0 - ic - si
    if (sa < 0.75 - ic) and (ic >= 0.40):
        return "silty clay"
    if (sa < 0.75 - ic) and (ic >= 0.26):
        return "silty clay loam"
    if sa < 0.75 - ic:
        return "silty loam"
    if (ic >= 0.40 + (0.305 - 0.40) / (0.635 - 0.35) * (sa - 0.35)) and (
        ic < 0.50 + (0.305 - 0.50) / (0.635 - 0.50) * (sa - 0.50)
    ):
        return "clay"
    if ic >= 0.26 + (0.305 - 0.26) / (0.635 - 0.74) * (sa - 0.74):
        return "sandy clay"
    if (ic >= 0.26 + (0.17 - 0.26) / (0.83 - 0.49) * (sa - 0.49)) and (
        ic < 0.10 + (0.305 - 0.10) / (0.635 - 0.775) * (sa - 0.775)
    ):
        return "clay loam"
    if ic >= 0.26 + (0.17 - 0.26) / (0.83 - 0.49) * (sa - 0.49):
        return "sandy clay loam"
    if (ic >= 0.10 + (0.12 - 0.10) / (0.63 - 0.775) * (sa - 0.775)) and (
        ic < 0.10 + (0.305 - 0.10) / (0.635 - 0.775) * (sa - 0.775)
    ):
        return "loam"
    if ic >= 0.10 + (0.12 - 0.10) / (0.63 - 0.775) * (sa - 0.775):
        return "sandy loam"
    if ic < 0.00 + (0.08 - 0.00) / (0.88 - 0.93) * (sa - 0.93):
        return "loamy sand"
    return "sand"


def texture_props(name: str):
    """(short code, albedo, runoff curve CN2) for a texture class name."""
    i = _TEXTURE_NAMES.index(name) if name in _TEXTURE_NAMES else -1
    return _TEXTURE_CODES[i], _ALBEDO[i], _CN2[i]


def slu1(clay1: float, sand1: float) -> float:
    """First-stage evaporation limit (Ritchie), from top-layer clay/sand %."""
    if sand1 >= 80:
        return 20 - 0.15 * sand1
    if clay1 >= 50:
        return 11 - 0.06 * clay1
    return 8 - 0.08 * clay1


def root_growth_factor() -> list:
    """SRGF per standard layer: 1 in the top 15 cm, then exp decay with depth."""
    centers = []
    prev = 0
    for bottom in SLB_BOTTOM:
        centers.append((bottom - prev) / 2.0 + prev)
        prev = bottom
    return [
        1.0 if b <= 15 else math.exp(-0.02 * c)
        for b, c in zip(SLB_BOTTOM, centers)
    ]


def _prop(soil: Mapping, short: str, label: str):
    """Read a soil property at a depth from an extract_static_points row/dict."""
    key = f"{short}_{label}"
    if key not in soil:
        raise KeyError(
            f"Soil input is missing column '{key}'. Provide the six SoilGrids "
            f"depths for {short} (from extract_static_points)."
        )
    v = soil[key]
    return float(v) if v is not None and not pd.isna(v) else np.nan


def build_profile(
    soil: Mapping, depths: Sequence[str] = DEPTH_LABELS
) -> Dict[str, object]:
    """Compute the full per-layer soil profile a crop model needs.

    ``soil`` is one row (dict or ``pandas.Series``) of
    :func:`agwise_data.extract_static_points` output — it must carry the six
    SoilGrids depths for CLAY, SAND, SILT, SOC, NITROGEN, PH, CEC and BDOD.
    Returns arrays keyed by DSSAT/APSIM meaning plus scalar metadata
    (texture, albedo, runoff curve, SLU1).
    """
    clay = np.array([_prop(soil, "CLAY", d) for d in depths])
    sand = np.array([_prop(soil, "SAND", d) for d in depths])
    silt = np.array([_prop(soil, "SILT", d) for d in depths])
    soc = np.array([_prop(soil, "SOC", d) for d in depths])       # g/kg
    nitro = np.array([_prop(soil, "NITROGEN", d) for d in depths])  # g/kg
    ph = np.array([_prop(soil, "PH", d) for d in depths])
    cec = np.array([_prop(soil, "CEC", d) for d in depths])
    bdod = np.array([_prop(soil, "BDOD", d) for d in depths])

    sloc = soc / 10.0            # organic carbon %
    som = sloc * 2.0             # organic matter %
    slni = nitro / 10.0
    pwp, fc, sat, ks = saxton_rawls(clay, sand, som)

    tname = texture_class(clay[0] / 100.0, silt[0] / 100.0)
    tcode, albedo, cn2 = texture_props(tname)

    return {
        "n_layers": len(depths),
        "slb": SLB_BOTTOM[: len(depths)],
        "thickness_mm": THICKNESS_MM[: len(depths)],
        "clay": clay, "sand": sand, "silt": silt,
        "sloc": sloc, "slni": slni, "ph": ph, "cec": cec, "bdod": bdod,
        "pwp": pwp, "fc": fc, "sat": sat, "ks": ks,
        "srgf": np.array(root_growth_factor()[: len(depths)]),
        "texture_name": tname, "texture_code": tcode,
        "albedo": albedo, "cn2": cn2,
        "slu1": slu1(clay[0], sand[0]),
    }


# --------------------------------------------------------------------------
# DSSAT .SOL
# --------------------------------------------------------------------------
_SITE_HEADER = "@SITE        COUNTRY          LAT     LONG SCS FAMILY"
_SCOM_HEADER = "@ SCOM  SALB  SLU1  SLDR  SLRO  SLNF  SLPF  SMHB  SMPX  SMKE"
_LAYER_NAMES = [
    "SLMH", "SLLL", "SDUL", "SSAT", "SRGF", "SSKS", "SBDM", "SLOC", "SLCL",
    "SLSI", "SLCF", "SLNI", "SLHW", "SLHB", "SCEC", "SADC",
]


def _f(x, fmt="{:>6.3f}"):
    """Format a value into a 6-wide DSSAT field, -99 for NaN/None."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "   -99"
    return fmt.format(x)


def write_sol(
    soil: Mapping,
    lat: float,
    lon: float,
    path,
    pedon: str = "AGWS000001",
    site: str = "-99",
    country: str = "-99",
    source: str = "ISRIC V2",
    sldr: float = 0.5,
    slnf: float = 1.0,
    slpf: float = 1.0,
    depths: Sequence[str] = DEPTH_LABELS,
) -> Path:
    """Write one DSSAT ``.SOL`` profile from a soil-point row.

    ``sldr`` (drainage rate), ``slnf`` (mineralization factor) and ``slpf``
    (photosynthesis factor) are model metadata not derivable from SoilGrids;
    the defaults are DSSAT-neutral and a module can override them. The
    hydraulics and layer chemistry are computed from ``soil``. Returns the path.
    """
    p = build_profile(soil, depths)
    lines = ["*SOILS: General DSSAT Soil Input File", ""]
    total_depth = p["slb"][-1]
    lines.append(
        f"*{pedon:<12}{source:<12}{p['texture_code']:<6}{total_depth:>5} "
        f"{p['texture_name']}"
    )
    lines.append(_SITE_HEADER)
    lines.append(
        f" {site:<11} {country:<13}{lat:>6.3f}{lon:>9.3f} Unclassified"
    )
    lines.append(_SCOM_HEADER)
    lines.append(
        f"   -99{_f(p['albedo'], '{:>6.2f}')}{_f(p['slu1'], '{:>6.2f}')}"
        f"{_f(sldr, '{:>6.2f}')}{_f(p['cn2'], '{:>6.1f}')}"
        f"{_f(slnf, '{:>6.2f}')}{_f(slpf, '{:>6.2f}')} IB001 IB001 IB001"
    )
    lines.append("@  SLB  SLMH  SLLL  SDUL  SSAT  SRGF  SSKS  SBDM  SLOC  SLCL"
                 "  SLSI  SLCF  SLNI  SLHW  SLHB  SCEC  SADC")
    for i in range(p["n_layers"]):
        lines.append(
            f"{p['slb'][i]:>6}"
            f"   -99"                                    # SLMH master horizon
            f"{_f(p['pwp'][i])}{_f(p['fc'][i])}{_f(p['sat'][i])}"
            f"{_f(p['srgf'][i])}{_f(round(p['ks'][i] / 10.0, 1))}"
            f"{_f(p['bdod'][i], '{:>6.2f}')}{_f(p['sloc'][i])}"
            f"{_f(p['clay'][i], '{:>6.1f}')}{_f(p['silt'][i], '{:>6.1f}')}"
            f"   -99"                                    # SLCF coarse fragments
            f"{_f(p['slni'][i], '{:>6.2f}')}{_f(p['ph'][i], '{:>6.2f}')}"
            f"   -99"                                    # SLHB pH in buffer
            f"{_f(p['cec'][i], '{:>6.2f}')}"
            f"   -99"                                    # SADC
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------
# APSIM soil-layer table
# --------------------------------------------------------------------------
def apsim_soil_table(
    soil: Mapping, depths: Sequence[str] = DEPTH_LABELS
) -> pd.DataFrame:
    """Per-layer soil table for the APSIM soil profile.

    The columns are the values ``01_readGeo_CM_zone_APSIM.R`` injects into its
    apsimx soil template: Thickness (mm), LL15, DUL, SAT, AirDry, KS, BD,
    Carbon, ParticleSizeClay/Silt, Nitrogen, PH, CEC. Salb and CN2Bare (whole
    profile) ride along as attributes on the frame (``frame.attrs``).
    """
    p = build_profile(soil, depths)
    sat = np.minimum(p["sat"], 0.697)  # APSIM caps SAT to avoid solver errors
    ks = np.round(p["ks"] / 10.0, 1)
    ks[ks == 0] = np.nan  # KS must not be 0; caller forward-fills
    df = pd.DataFrame({
        "Depth": [d.replace("_", "-") for d in depths],
        "Thickness": p["thickness_mm"],
        "LL15": np.round(p["pwp"], 3),
        "DUL": np.round(p["fc"], 3),
        "SAT": np.round(sat, 3),
        "AirDry": np.round(p["pwp"] - 0.02, 3),
        "KS": ks,
        "BD": np.round(p["bdod"], 2),
        "Carbon": np.round(p["sloc"], 3),
        "ParticleSizeClay": np.round(p["clay"], 1),
        "ParticleSizeSilt": np.round(p["silt"], 1),
        "Nitrogen": np.round(p["slni"] * 100.0, 2),
        "PH": np.round(p["ph"], 2),
        "CEC": np.round(p["cec"], 2),
    })
    df.attrs["Salb"] = p["albedo"]
    df.attrs["CN2Bare"] = p["cn2"]
    df.attrs["texture"] = p["texture_name"]
    return df
