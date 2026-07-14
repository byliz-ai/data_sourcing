"""Write APSIM weather (.met) files from harmonized daily weather.

Reproduces the layout ``apsimx::write_apsim_met`` emits (so ``read_apsim_met``
and APSIM read it back), removing the weather half of the per-module
``01_readGeo_CM_zone_APSIM.R``. Column order is APSIM's:
``year day radn maxt mint rain``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from ._common import prepare_weather, tav_amp

_COLNAMES = "year day radn maxt mint rain"
_UNITS = "() () (MJ/m2/day) (oC) (oC) (mm)"


def _num(x: float) -> str:
    """APSIM prints whole numbers without a trailing '.0' (12, not 12.0)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "NaN"
    r = round(float(x), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def write_met(
    daily,
    lat: float,
    lon: float,
    path,
    site: str = "AGWISE",
    comments: Optional[str] = None,
) -> Path:
    """Write one APSIM ``.met`` file.

    ``daily`` needs a date column and TMAX/TMIN/SRAD/RAIN (or PRCP); see
    :func:`prepare_weather`. TAV and AMP are derived from the series and
    written into the header (APSIM requires both). Returns the written path.
    """
    df = prepare_weather(daily)
    if df.empty:
        raise ValueError("No weather rows to write")
    tav, amp = tav_amp(df)

    header = [
        comments or "! weather derived from AgWise harmonized climate",
        "[weather.met.weather]",
        f"site = {site}",
        f"latitude = {lat}",
        f"longitude = {lon}",
        f"tav = {tav}",
        f"amp = {amp}",
        _COLNAMES,
        _UNITS,
    ]
    rows = []
    for row in df.itertuples(index=False):
        rows.append(
            f"{row.DATE.year} {row.DATE.dayofyear} "
            f"{_num(row.SRAD)} {_num(row.TMAX)} {_num(row.TMIN)} {_num(row.RAIN)}"
        )

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header + rows) + "\n")
    return path
