"""agwise_data — the AgWise data-sourcing layer.

One call to fetch, harmonize and cache the climate, soil, terrain and
remote-sensing data every AgWise module needs — and to turn it into
analysis-ready inputs — with a shared cache so a dataset is downloaded once
and reused by everyone. Full guide: README.md; function reference: REFERENCE.md.

Public API (all return a dict of products, a DataFrame, or a list of files):

* Gridded cubes — :func:`get_climate`, :func:`get_static`/:func:`get_dem`/
  :func:`get_soil`, :func:`get_seasonal`, :func:`get_modis`/:func:`get_ndvi`,
  :func:`get_cropmask`, :func:`get_season`.
* Point extraction — :func:`extract_points`, :func:`extract_growing_season`,
  :func:`extract_static_points`.
* Crop-model input files — :func:`to_dssat`, :func:`to_apsim`,
  :func:`to_wofost`, :func:`to_oryza`.
* Spatial scaffolding — :func:`make_grid`, :func:`tag_admin`.
* Seasonal-forecast bias correction — :func:`bias_correct`,
  :func:`forecast_to_dssat`.

    from agwise_data import get_climate
    result = get_climate(["PRCP", "TMAX"], years=range(2015, 2025),
                         country="Rwanda", freq="monthly")
"""

from .api import (
    bias_correct,
    extract_growing_season,
    extract_points,
    extract_static_points,
    forecast_to_dssat,
    get_climate,
    get_cropmask,
    get_dem,
    get_modis,
    get_ndvi,
    get_season,
    get_seasonal,
    get_soil,
    get_static,
    make_grid,
    rainy_days,
    tag_admin,
    to_apsim,
    to_dssat,
    to_oryza,
    to_wofost,
)
from .config import Config

__version__ = "0.11.2"

__all__ = [
    "get_climate",
    "extract_points",
    "extract_growing_season",
    "rainy_days",
    "get_static",
    "get_dem",
    "get_soil",
    "get_cropmask",
    "get_seasonal",
    "get_modis",
    "get_ndvi",
    "get_season",
    "extract_static_points",
    "to_dssat",
    "to_apsim",
    "to_wofost",
    "to_oryza",
    "make_grid",
    "tag_admin",
    "bias_correct",
    "forecast_to_dssat",
    "Config",
    "__version__",
]
