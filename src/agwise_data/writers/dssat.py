"""Write DSSAT weather (.WTH) files from harmonized daily weather.

Reproduces the fixed-width layout the DSSAT ``write_wth`` R function emits (so
DSSAT's own ``read_wth`` and the model read it back), removing the need for the
per-module ``readGeo_CM_zone.R`` weather half. The soil (.SOL) half lives in
``writers/soil.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ..qc import GAPFILL_MAX_DAYS
from ._common import prepare_weather, record_written, station_code, tav_amp
from .validate import validate_wth

# The two column headers are fixed for the TMAX/TMIN/SRAD/RAIN weather set and
# are byte-aligned to the data field widths below.
_GENERAL_HEADER = "@ INSI      LAT     LONG  ELEV   TAV   AMP REFHT WNDHT"
_DATA_HEADER = "@  DATE  TMAX  TMIN  SRAD  RAIN"


def _fmt_general(insi, lat, lon, elev, tav, amp, refht, wndht) -> str:
    elev = -99 if elev is None or (isinstance(elev, float) and np.isnan(elev)) else elev
    return (
        f"{insi:>6}"
        f"{lat:>9.3f}"
        f"{lon:>9.3f}"
        f"{elev:>6.0f}"
        f"{tav:>6.1f}"
        f"{amp:>6.1f}"
        f"{refht:>6.1f}"
        f"{wndht:>6.1f}"
    )


def _fmt_val(v) -> str:
    """A 6-wide one-decimal data field; a missing value is DSSAT's -99."""
    return f"{-99.0 if pd.isna(v) else v:>6.1f}"


def _dssat_date(ts: pd.Timestamp) -> str:
    """YYYYDDD (4-digit year + zero-padded day-of-year), DSSAT's date field."""
    return f"{ts.year:04d}{ts.dayofyear:03d}"


def write_wth(
    daily: pd.DataFrame,
    lat: float,
    lon: float,
    path,
    station: str = "AGWS",
    elev: Optional[float] = None,
    refht: float = 2.0,
    wndht: float = 2.0,
    gapfill_days: int = GAPFILL_MAX_DAYS,
    report: Optional[dict] = None,
) -> Path:
    """Write one DSSAT ``.WTH`` file.

    ``daily`` needs a date column and TMAX/TMIN/SRAD/RAIN (or PRCP); see
    :func:`prepare_weather` (``gapfill_days`` is passed on). TAV and AMP are
    derived from the series. The written file is read back and checked
    (:func:`.validate.validate_wth`); pass a dict as ``report`` to receive
    the weather QC and the validation. Returns the written path.
    """
    df = prepare_weather(daily, lat=lat, gapfill_days=gapfill_days)
    if df.empty:
        raise ValueError("No weather rows to write")
    tav, amp = tav_amp(df)
    insi = station_code(station)

    lines = ["$WEATHER: ", "", ""]
    lines.append(_GENERAL_HEADER)
    lines.append(_fmt_general(insi, lat, lon, elev, tav, amp, refht, wndht))
    lines.append("")
    lines.append(_DATA_HEADER)
    for row in df.itertuples(index=False):
        lines.append(
            f"{_dssat_date(row.DATE):>7}"
            f"{_fmt_val(row.TMAX)}"
            f"{_fmt_val(row.TMIN)}"
            f"{_fmt_val(row.SRAD)}"
            f"{_fmt_val(row.RAIN)}"
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    record_written(report, "wth", validate_wth(path), df.attrs.get("qc"))
    return path
