"""Crop-model input-file writers (DSSAT, APSIM).

The "last mile" that turns the layer's harmonized weather + soil into the
files a crop model actually reads, so the AgWise modules stop re-implementing
``readGeo_CM_zone`` per use case. Weather writers live here; soil writers and
the Saxton-Rawls pedotransfer functions are in ``soil.py``.
"""

from __future__ import annotations

from . import apsim, dssat, soil
from ._common import prepare_weather, tav_amp

__all__ = ["dssat", "apsim", "soil", "prepare_weather", "tav_amp"]
