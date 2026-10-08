"""Shared helpers for the crop-model input writers.

These turn the layer's harmonized weather (daily TMAX/TMIN/SRAD/RAIN) into the
per-station quantities every crop-model weather file needs — the long-term
average temperature (TAV) and the annual temperature amplitude (AMP) — and
enforce the same sanity fixes the legacy AgWise ``readGeo_CM`` scripts applied
(swap any day where TMIN > TMAX), plus the Phase 1 checks of
:func:`agwise_data.qc.check_weather` (continuous dates, SRAD <= Ra, short
gap-filling). Kept engine-agnostic so the DSSAT and APSIM
writers share exactly one implementation.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ..qc import GAPFILL_MAX_DAYS, check_weather

logger = logging.getLogger("agwise_data")

# Canonical daily weather columns the writers consume. PRCP (our short name)
# is accepted as an alias for RAIN.
WEATHER_COLS = ["TMAX", "TMIN", "SRAD", "RAIN"]


def require_data(df: pd.DataFrame, cols) -> None:
    """Raise if a required weather column is present but carries no data.

    An all-NaN column typically means the point's nearest cell on a *coarse*
    source grid is masked (e.g. it falls outside the requested admin polygon
    while the finer grids still cover the point). Such a point has no usable
    weather — it must be skipped by the caller, not written out as a file
    full of missing-value sentinels.
    """
    if not len(df):
        return  # a zero-row frame gets the writers' own "no rows" error
    empty = [c for c in cols if df[c].isna().all()]
    if empty:
        raise ValueError(
            f"weather columns {empty} have no data at this point (all NaN — "
            "its nearest coarse-grid cell is likely masked outside the region)"
        )


def prepare_weather(
    daily: pd.DataFrame,
    lat: Optional[float] = None,
    gapfill_days: int = GAPFILL_MAX_DAYS,
) -> pd.DataFrame:
    """Clean a per-point daily weather frame for a crop-model writer.

    Accepts a frame with a date column (``DATE``/``date``/``time``) and the
    four weather columns (``PRCP`` accepted for ``RAIN``). Returns a frame
    with a ``DATE`` datetime column and ``TMAX, TMIN, SRAD, RAIN`` floats on
    a continuous daily axis (leading/trailing all-NaN days trimmed, interior
    missing dates kept as missing rows), after :func:`agwise_data.qc.
    check_weather`: TMIN > TMAX days swapped (as the legacy scripts did),
    SRAD above the extraterrestrial radiation set to NaN (when ``lat`` is
    given) and TMAX/TMIN/SRAD gaps of up to ``gapfill_days`` days
    interpolated — never rainfall. What changed is in ``df.attrs["qc"]``.
    """
    df = daily.copy()
    # normalise the date column name
    date_col = next(
        (c for c in ("DATE", "date", "time", "Date") if c in df.columns), None
    )
    if date_col is None:
        raise ValueError(
            f"No date column found (looked for DATE/date/time); got {list(df.columns)}"
        )
    df = df.rename(columns={date_col: "DATE"})
    if "RAIN" not in df.columns and "PRCP" in df.columns:
        df = df.rename(columns={"PRCP": "RAIN"})
    missing = [c for c in WEATHER_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Weather frame is missing {missing}; needs TMAX, TMIN, SRAD and "
            "RAIN (or PRCP)."
        )

    df["DATE"] = pd.to_datetime(df["DATE"])
    for c in WEATHER_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    require_data(df, WEATHER_COLS)
    df = df[["DATE", *WEATHER_COLS]].sort_values("DATE").reset_index(drop=True)
    df = trim_empty_days(df, WEATHER_COLS)
    if df.empty:
        return df
    df, report = check_weather(df, "DATE", lat=lat, gapfill_days=gapfill_days)
    df.attrs["qc"] = report
    return df


def trim_empty_days(df: pd.DataFrame, cols) -> pd.DataFrame:
    """Drop leading/trailing days where every column in ``cols`` is missing."""
    has = df[list(cols)].notna().any(axis=1).to_numpy()
    if not has.any():
        return df.iloc[0:0]
    first, last = has.argmax(), len(has) - has[::-1].argmax()
    return df.iloc[first:last].reset_index(drop=True)


# Height (m) of the layer's WIND variable: AgERA5 reports 10 m wind speed.
WIND_SOURCE_HEIGHT_M = 10.0


def wind_to_2m(u, z: float = WIND_SOURCE_HEIGHT_M):
    """Convert wind speed measured at ``z`` metres to 2 m (FAO-56 eq. 47).

    ``u2 = uz * 4.87 / ln(67.8 z - 5.42)``; at 10 m the factor is ~0.748.
    WOFOST and ORYZA expect 2 m wind; a ``z`` of 2 returns ``u`` unchanged.
    """
    if z == 2.0:
        return u
    return u * (4.87 / np.log(67.8 * z - 5.42))


def tav_amp(daily: pd.DataFrame) -> Tuple[float, float]:
    """Long-term mean temperature (TAV) and amplitude (AMP), DSSAT/APSIM style.

    TAV = mean of daily (TMAX+TMIN)/2 over the record. AMP = half the spread
    between the warmest and coldest *calendar-month* mean temperature. Matches
    ``readGeo_CM_zone.R``.
    """
    mean_t = (daily["TMAX"] + daily["TMIN"]) / 2.0
    tav = float(np.nanmean(mean_t))
    monthly = mean_t.groupby(daily["DATE"].dt.month).mean()
    amp = float((monthly.max() - monthly.min()) / 2.0)
    return round(tav, 1), round(amp, 1)


def station_code(name: str, fallback: str = "AGWS") -> str:
    """A 4-character DSSAT INSI / APSIM site code from a place name."""
    if not name:
        return fallback
    code = "".join(ch for ch in str(name).upper() if ch.isalnum())[:4]
    return code or fallback


def record_written(report: Optional[dict], key: str, validation: dict,
                   weather_qc: Optional[dict] = None) -> None:
    """Log a failed post-write validation and fill the caller's ``report``."""
    if not validation["ok"]:
        logger.warning(
            "%s failed validation: %s", validation["file"],
            "; ".join(validation["problems"]),
        )
    if report is None:
        return
    report.setdefault("validation", {})[key] = validation
    if weather_qc is not None:
        report["weather"] = weather_qc
