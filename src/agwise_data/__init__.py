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

from .api import extract_growing_season, extract_points, get_climate
from .config import Config

__version__ = "0.1.0"

__all__ = [
    "get_climate",
    "extract_points",
    "extract_growing_season",
    "Config",
    "__version__",
]
