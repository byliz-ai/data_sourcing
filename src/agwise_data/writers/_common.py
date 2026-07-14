"""Shared helpers for the crop-model input writers.

These turn the layer's harmonized weather (daily TMAX/TMIN/SRAD/RAIN) into the
per-station quantities every crop-model weather file needs — the long-term
average temperature (TAV) and the annual temperature amplitude (AMP) — and
enforce the same sanity fixes the legacy AgWise ``readGeo_CM`` scripts applied
(swap any day where TMIN > TMAX). Kept engine-agnostic so the DSSAT and APSIM
writers share exactly one implementation.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

# Canonical daily weather columns the writers consume. PRCP (our short name)
# is accepted as an alias for RAIN.
WEATHER_COLS = ["TMAX", "TMIN", "SRAD", "RAIN"]


def prepare_weather(daily: pd.DataFrame) -> pd.DataFrame:
    """Clean a per-point daily weather frame for a crop-model writer.

    Accepts a frame with a date column (``DATE``/``date``/``time``) and the
    four weather columns (``PRCP`` accepted for ``RAIN``). Returns a frame
    with a ``DATE`` datetime column and ``TMAX, TMIN, SRAD, RAIN`` floats,
    sorted by date, with all-NaN rows dropped and any TMIN > TMAX day
    swapped (matching the legacy scripts, which crop models require).
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
    df = df[["DATE", *WEATHER_COLS]].sort_values("DATE").reset_index(drop=True)
    df = df.dropna(subset=WEATHER_COLS, how="all")

    # Guarantee TMIN <= TMAX (some pixels/days have them crossed).
    crossed = df["TMIN"] > df["TMAX"]
    if crossed.any():
        tmin = df.loc[crossed, "TMIN"].copy()
        df.loc[crossed, "TMIN"] = df.loc[crossed, "TMAX"]
        df.loc[crossed, "TMAX"] = tmin
    return df


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
