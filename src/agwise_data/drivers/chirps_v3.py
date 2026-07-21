"""CHIRPS v3.0 rainfall driver — a local-only alternative to CHIRPS v2.0.

The complete CHIRPS v3.0 daily series (1981-2023, 0.05 degree, ~24 GB/year)
is staged in the AgWise ``Global_GeoData/Landing`` tree, so this source is
served **from the local adapter** (:mod:`.local`) — set ``AGWISE_LOCAL_ROOT``
(the CGLabs default). There is no network fetch wired here; without a local
file the driver raises a clear error rather than guessing a download. That
also makes it immune to the data.chc.ucsb.edu 403 blocking that limits the
v2.0 global-NetCDF path.

Choose it per call with ``source="chirps_v3"`` (v2.0 stays the default), e.g.
``extract_points(points, "PRCP", start, end, source="chirps_v3")``.
"""

from __future__ import annotations

from . import register
from .base import Driver


@register("chirps_v3")
class ChirpsV3Driver(Driver):
    def _fetch_year(self, variable: str, year: int, domain: str):
        # ensure_daily_year tries the local adapter first; reaching here means
        # no readable local file exists (or AGWISE_LOCAL_ROOT is unset).
        raise RuntimeError(
            "CHIRPS v3 is only served from the local data tree "
            "(Rainfall/chirps_v3/<year>.nc; staged years are 1981-2023). "
            "Set AGWISE_LOCAL_ROOT to the AgWise Global_GeoData/Landing "
            f"folder — no local file found for {year}. For downloadable "
            "rainfall use the default CHIRPS v2.0 source."
        )
