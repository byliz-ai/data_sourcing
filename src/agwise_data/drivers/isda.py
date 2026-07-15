"""iSDA Africa soil driver — a soil source alternative to SoilGrids.

iSDA (Innovative Solutions for Decision Agriculture) maps soil properties for
Africa at ~30 m, two depths (0-20 cm, 20-50 cm). AgWise keeps the layers in the
``Global_GeoData/Landing`` tree, so this source is served **from the local
adapter** (:mod:`.local`) — set ``AGWISE_LOCAL_ROOT``. There is no network fetch
wired here; without a local root the driver raises a clear error rather than
guessing a download.

Choose it per call with ``source="isda"`` (SoilGrids stays the default), e.g.
``extract_static_points(points, ["CLAY", "SOC", "PH"], source="isda")``. iSDA's
two depths differ from SoilGrids' six, so the crop-model writers (which expect
the SoilGrids depth set) still use SoilGrids.
"""

from __future__ import annotations

from . import register
from .static import StaticDriver


@register("isda")
class IsdaDriver(StaticDriver):
    def _fetch_static(self, variable: str, domain: str):
        # ensure_static tries the local adapter first; reaching here means no
        # local file was found (or AGWISE_LOCAL_ROOT is unset).
        raise RuntimeError(
            "iSDA soil is only served from the local data tree. Set "
            "AGWISE_LOCAL_ROOT to the AgWise Global_GeoData/Landing folder "
            f"(no local iSDA file found for {variable} in domain {domain})."
        )
