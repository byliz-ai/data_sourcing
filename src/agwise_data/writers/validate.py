"""Post-write validation: read a written crop-model file back and check it.

Each ``validate_*`` re-parses the file exactly as written (not the frame it
came from), so a formatting slip — a ``nan`` in a fixed-width row, a missing
day, a value pushed out of its column — is caught before a model run fails on
it. Every validator returns the same record::

    {"file", "format", "ok", "problems": [...], ...format-specific counts}

``ok`` is ``False`` when ``problems`` is non-empty. The writers call these
after writing; they can also be run on any existing file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .. import qc

_MISSING = -99.0


def _physical(canonical: str, scale: float = 1.0):
    lo, hi = qc.ranges_for(canonical)["physical"]
    return (None if lo is None else lo * scale, None if hi is None else hi * scale)


def _range_count(values: np.ndarray, bounds) -> int:
    lo, hi = bounds
    v = values[np.isfinite(values)]
    n = 0
    if lo is not None:
        n += int((v < lo).sum())
    if hi is not None:
        n += int((v > hi).sum())
    return n


def _daily_checks(
    dates: pd.DatetimeIndex, cols: Dict[str, np.ndarray], bounds: Dict[str, tuple],
    problems: List[str],
) -> dict:
    """Shared checks for a daily weather table: continuity, missing, ranges."""
    out = {
        "n_days": len(dates),
        "first": dates.min().strftime("%Y-%m-%d") if len(dates) else None,
        "last": dates.max().strftime("%Y-%m-%d") if len(dates) else None,
    }
    if len(dates):
        dup = int(dates.duplicated().sum())
        diffs = np.diff(dates.values).astype("timedelta64[D]").astype(int)
        gaps = int((diffs > 1).sum())
        backwards = int((diffs < 1).sum()) - dup
        out.update({"duplicate_dates": dup, "date_gaps": gaps})
        if dup:
            problems.append(f"{dup} duplicated dates")
        if gaps:
            problems.append(f"{gaps} gaps in the daily dates")
        if backwards > 0:
            problems.append("dates are not in ascending order")
    else:
        problems.append("no data rows")
    out["missing"] = {k: int(np.isnan(v).sum()) for k, v in cols.items()}
    oor = {k: _range_count(cols[k], b) for k, b in bounds.items() if k in cols}
    out["out_of_physical_range"] = oor
    for k, n in oor.items():
        if n:
            problems.append(f"{n} {k} values outside the physical range {bounds[k]}")
    if "tmax" in cols and "tmin" in cols:
        tmax, tmin = cols["tmax"], cols["tmin"]
        both = np.isfinite(tmax) & np.isfinite(tmin)
        crossed = int((tmin[both] > tmax[both]).sum())
        out["tmin_gt_tmax"] = crossed
        if crossed:
            problems.append(f"{crossed} days with TMIN > TMAX")
    return out


def _record(path, fmt: str, problems: List[str], **fields) -> dict:
    return {"file": str(path), "format": fmt, "ok": not problems,
            "problems": problems, **fields}


def _as_float(token: str) -> float:
    try:
        v = float(token)
    except ValueError:
        return float("nan")
    return float("nan") if v == _MISSING else v


# ---------------------------------------------------------------------------
def validate_wth(path) -> dict:
    """Validate a DSSAT ``.WTH`` written by :func:`dssat.write_wth`."""
    path = Path(path)
    problems: List[str] = []
    lines = path.read_text().splitlines()
    try:
        head = next(i for i, ln in enumerate(lines) if ln.startswith("@  DATE"))
        site = next(i for i, ln in enumerate(lines) if ln.startswith("@ INSI"))
    except StopIteration:
        return _record(path, "dssat_wth", ["missing @ INSI or @  DATE header"])
    general = lines[site + 1].split()
    # ELEV may legitimately be -99; TAV and AMP must be real numbers.
    if len(general) != 8 or any(np.isnan(_as_float(t)) for t in general[4:6]):
        problems.append(f"station line has no numeric TAV/AMP: {lines[site + 1]!r}")

    dates, rows = [], []
    for ln in lines[head + 1:]:
        if not ln.strip():
            continue
        # fixed width: 7 (YYYYDDD) + 4 x 6
        fields = [ln[0:7], ln[7:13], ln[13:19], ln[19:25], ln[25:31]]
        if len(ln) != 31 or any("nan" in f.lower() for f in fields):
            problems.append(f"malformed data row: {ln!r}")
            continue
        try:
            dates.append(pd.Timestamp(year=int(fields[0][:4]), month=1, day=1)
                         + pd.Timedelta(days=int(fields[0][4:]) - 1))
        except ValueError:
            problems.append(f"bad DSSAT date {fields[0]!r}")
            continue
        rows.append([_as_float(f) for f in fields[1:]])
    arr = np.array(rows, dtype="float64").reshape(-1, 4)
    cols = {"tmax": arr[:, 0], "tmin": arr[:, 1], "srad": arr[:, 2], "rain": arr[:, 3]}
    bounds = {"tmax": _physical("TMAX"), "tmin": _physical("TMIN"),
              "srad": _physical("SRAD"), "rain": _physical("PRCP")}
    info = _daily_checks(pd.DatetimeIndex(dates), cols, bounds, problems)
    return _record(path, "dssat_wth", problems, **info)


def validate_met(path) -> dict:
    """Validate an APSIM ``.met`` written by :func:`apsim.write_met`."""
    path = Path(path)
    problems: List[str] = []
    lines = path.read_text().splitlines()
    header = {}
    data_start = None
    for i, ln in enumerate(lines):
        if "=" in ln and not ln.startswith("!"):
            k, v = (s.strip() for s in ln.split("=", 1))
            header[k.lower()] = v
        if ln.split() == ["year", "day", "radn", "maxt", "mint", "rain"]:
            data_start = i + 2  # skip the units row
            break
    for key in ("latitude", "tav", "amp"):
        if np.isnan(_as_float(header.get(key, "x"))):
            problems.append(f"header {key} is missing or not numeric")
    if data_start is None:
        return _record(path, "apsim_met", problems + ["missing column header"])

    dates, rows = [], []
    for ln in lines[data_start:]:
        tok = ln.split()
        if not tok:
            continue
        if len(tok) != 6:
            problems.append(f"malformed data row: {ln!r}")
            continue
        try:
            dates.append(pd.Timestamp(year=int(tok[0]), month=1, day=1)
                         + pd.Timedelta(days=int(tok[1]) - 1))
        except ValueError:
            problems.append(f"bad year/day in row {ln!r}")
            continue
        rows.append([float("nan") if t == "NaN" else _as_float(t) for t in tok[2:]])
    arr = np.array(rows, dtype="float64").reshape(-1, 4)
    cols = {"srad": arr[:, 0], "tmax": arr[:, 1], "tmin": arr[:, 2], "rain": arr[:, 3]}
    bounds = {"tmax": _physical("TMAX"), "tmin": _physical("TMIN"),
              "srad": _physical("SRAD"), "rain": _physical("PRCP")}
    info = _daily_checks(pd.DatetimeIndex(dates), cols, bounds, problems)
    if any(info["missing"].values()):
        problems.append(f"missing values (APSIM needs a complete series): {info['missing']}")
    return _record(path, "apsim_met", problems, **info)


def validate_wofost_weather(path) -> dict:
    """Validate a WOFOST weather CSV written by :func:`wofost.write_weather`."""
    from .wofost import WOFOST_WEATHER_COLS

    path = Path(path)
    problems: List[str] = []
    df = pd.read_csv(path)
    if list(df.columns) != WOFOST_WEATHER_COLS:
        return _record(path, "wofost_weather",
                       [f"columns {list(df.columns)} != {WOFOST_WEATHER_COLS}"])
    dates = pd.DatetimeIndex(pd.to_datetime(df["date"], errors="coerce"))
    if dates.isna().any():
        problems.append(f"{int(dates.isna().sum())} unparsable dates")
        dates = dates[~dates.isna()]
    cols = {c: df[c].to_numpy(dtype="float64") for c in WOFOST_WEATHER_COLS[1:]}
    bounds = {"srad": _physical("SRAD", 1000.0), "tmin": _physical("TMIN"),
              "tmax": _physical("TMAX"), "wind": _physical("WIND"),
              "prec": _physical("PRCP"), "vapr": (0.0, 10.0)}
    info = _daily_checks(dates, cols, bounds, problems)
    if any(info["missing"].values()):
        problems.append(f"missing values (WOFOST needs a complete series): {info['missing']}")
    return _record(path, "wofost_weather", problems, **info)


def validate_oryza_weather(paths: Sequence) -> dict:
    """Validate the yearly ORYZA CABO weather files of one station together."""
    paths = [Path(p) for p in paths]
    problems: List[str] = []
    dates, rows = [], []
    for path in paths:
        body = [ln for ln in path.read_text().splitlines()
                if ln.strip() and not ln.startswith("*")]
        if not body or len(body[0].split(",")) != 5:
            problems.append(f"{path.name}: missing station line (lon,lat,elev,A,B)")
            continue
        for ln in body[1:]:
            tok = ln.split(",")
            if len(tok) != 9:
                problems.append(f"{path.name}: malformed row {ln!r}")
                continue
            try:
                dates.append(pd.Timestamp(year=int(tok[1]), month=1, day=1)
                             + pd.Timedelta(days=int(tok[2]) - 1))
            except ValueError:
                problems.append(f"{path.name}: bad year/day in {ln!r}")
                continue
            rows.append([_as_float(t) for t in tok[3:]])
    arr = np.array(rows, dtype="float64").reshape(-1, 6)
    cols = {"srad": arr[:, 0], "tmin": arr[:, 1], "tmax": arr[:, 2],
            "vapr": arr[:, 3], "wind": arr[:, 4], "rain": arr[:, 5]}
    bounds = {"srad": _physical("SRAD", 1000.0), "tmin": _physical("TMIN"),
              "tmax": _physical("TMAX"), "wind": _physical("WIND"),
              "rain": _physical("PRCP"), "vapr": (0.0, 10.0)}
    info = _daily_checks(pd.DatetimeIndex(dates), cols, bounds, problems)
    return _record(paths[0] if len(paths) == 1 else [str(p) for p in paths],
                   "oryza_weather", problems, **info)


def validate_sol(path) -> dict:
    """Validate a DSSAT ``.SOL`` profile written by :func:`soil.write_sol`."""
    path = Path(path)
    problems: List[str] = []
    lines = path.read_text().splitlines()
    try:
        head = next(i for i, ln in enumerate(lines) if ln.startswith("@  SLB  SLMH"))
    except StopIteration:
        return _record(path, "dssat_sol", ["missing @  SLB  SLMH layer header"])
    names = lines[head][1:].split()
    layers = []
    for ln in lines[head + 1:]:
        if not ln.strip() or ln.startswith("@"):
            break
        width = 6 * len(names)
        if len(ln) != width or "nan" in ln.lower():
            problems.append(f"malformed layer row: {ln!r}")
            continue
        layers.append([_as_float(ln[i:i + 6]) for i in range(0, width, 6)])
    if not layers:
        return _record(path, "dssat_sol", problems + ["no soil layers"])
    t = pd.DataFrame(layers, columns=names)

    slb = t["SLB"].to_numpy()
    if not (np.diff(slb) > 0).all():
        problems.append("layer depths (SLB) are not increasing")
    ll, dul, sat = (t[c].to_numpy() for c in ("SLLL", "SDUL", "SSAT"))
    ok = np.isfinite(ll) & np.isfinite(dul) & np.isfinite(sat)
    bad_order = int((~((ll < dul) & (dul < sat)))[ok].sum())
    if bad_order:
        problems.append(f"{bad_order} layers without SLLL < SDUL < SSAT")
    checks = {
        "SBDM": (0.0, 2.65), "SLHW": (0.0, 14.0), "SLOC": (0.0, 100.0),
        "SLCL": (0.0, 100.0), "SLSI": (0.0, 100.0), "SLCF": (0.0, 100.0),
    }
    oor = {c: _range_count(t[c].to_numpy(), b) for c, b in checks.items() if c in t}
    for c, n in oor.items():
        if n:
            problems.append(f"{n} {c} values outside {checks[c]}")
    clay_silt = (t["SLCL"] + t["SLSI"]).to_numpy()
    over = int((clay_silt[np.isfinite(clay_silt)] > 100.5).sum())
    if over:
        problems.append(f"{over} layers with clay + silt > 100 %")
    missing = {c: int(t[c].isna().sum()) for c in t.columns if c != "SLB"}
    return _record(path, "dssat_sol", problems, n_layers=len(t),
                   out_of_range=oor, layers_missing=missing,
                   bad_water_order=bad_order)


def summarize(records: Sequence[Optional[dict]]) -> dict:
    """Counts across per-point QC records (for ``qc_report.json``)."""
    recs = [r for r in records if r]
    files = [v for r in recs for v in r.get("validation", {}).values() if v]
    return {
        "n_points": len(recs),
        "files_checked": len(files),
        "files_with_problems": sum(1 for f in files if not f["ok"]),
        "days_gapfilled": sum(
            sum(r.get("weather", {}).get("gapfilled", {}).values()) for r in recs
        ),
        "tmin_gt_tmax_swapped": sum(
            r.get("weather", {}).get("tmin_gt_tmax_swapped", 0) for r in recs
        ),
        "texture_layers_over_5pct": sum(
            (r.get("texture") or {}).get("large_deviation_over_5pct", 0) for r in recs
        ),
    }
