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

from ._common import prepare_weather, station_code, tav_amp

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
) -> Path:
    """Write one DSSAT ``.WTH`` file.

    ``daily`` needs a date column and TMAX/TMIN/SRAD/RAIN (or PRCP); see
    :func:`prepare_weather`. TAV and AMP are derived from the series.
    Returns the written path.
    """
    df = prepare_weather(daily)
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
            f"{row.TMAX:>6.1f}"
            f"{row.TMIN:>6.1f}"
            f"{row.SRAD:>6.1f}"
            f"{row.RAIN:>6.1f}"
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
