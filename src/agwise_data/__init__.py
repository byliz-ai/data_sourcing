"""agwise_data — the AgWise data access layer.

One call to fetch, harmonize and cache the climate data every AgWise
module needs, with a shared cache so a dataset is downloaded once and
reused by everyone.

Public API::

    from agwise_data import get_climate, extract_points, extract_growing_season

    result = get_climate(
        variables=["AGRO.PRCP", "AGRO.TMAX"],
        country="Kenya",
        years=range(2015, 2025),
        freq="monthly",
    )
"""

from .api import (
    bias_correct,
    extract_growing_season,
    extract_points,
    extract_static_points,
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
    tag_admin,
    to_apsim,
    to_dssat,
)
from .config import Config

__version__ = "0.9.0"

__all__ = [
    "get_climate",
    "extract_points",
    "extract_growing_season",
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
    "make_grid",
    "tag_admin",
    "bias_correct",
    "Config",
    "__version__",
]
