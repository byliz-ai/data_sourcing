"""Variable naming, unit conversions and temporal aggregation.

This module encodes the AgWise conventions agreed by the data sourcing
group (see Jemal's standardization proposal): every dataset, whatever its
source, is renamed to a canonical ``AGRO.*`` variable, converted to the
agreed units, and stored with standard dimension names (``time``, ``lat``,
``lon``) so that downstream modules never have to care where the data
came from.
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd
import xarray as xr

# ---------------------------------------------------------------------------
# Canonical variables.
#   short      : storage/file name (netCDF variable name, file labels)
#   monthly    : how a daily series aggregates to monthly ("sum" or "mean")
#   units      : units after harmonization
#   legacy     : name used by the pre-2026 AgWise scripts (kept so existing
#                module code can keep its column names)
CANONICAL_VARS = {
    "AGRO.PRCP": {
        "short": "PRCP",
        "long_name": "Daily precipitation",
        "units": "mm day-1",
        "monthly": "sum",
        "legacy": "Precipitation",
    },
    "AGRO.TMAX": {
        "short": "TMAX",
        "long_name": "Daily maximum air temperature (2 m)",
        "units": "degC",
        "monthly": "mean",
        "legacy": "TemperatureMax",
    },
    "AGRO.TMIN": {
        "short": "TMIN",
        "long_name": "Daily minimum air temperature (2 m)",
        "units": "degC",
        "monthly": "mean",
        "legacy": "TemperatureMin",
    },
    "AGRO.TEMP": {
        "short": "TEMP",
        "long_name": "Daily mean air temperature (2 m)",
        "units": "degC",
        "monthly": "mean",
        "legacy": "TemperatureMean",
    },
    "AGRO.SRAD": {
        "short": "SRAD",
        "long_name": "Daily solar radiation",
        "units": "MJ m-2 day-1",
        "monthly": "mean",
        "legacy": "SolarRadiation",
    },
    "AGRO.RHUM": {
        "short": "RHUM",
        "long_name": "Relative humidity (2 m, 12:00 local)",
        "units": "%",
        "monthly": "mean",
        "legacy": "RelativeHumidity",
    },
    "AGRO.WIND": {
        "short": "WIND",
        "long_name": "Daily mean wind speed (10 m)",
        "units": "m s-1",
        "monthly": "mean",
        "legacy": "WindSpeed",
    },
}

_SHORT_TO_CANONICAL = {v["short"]: k for k, v in CANONICAL_VARS.items()}
_LEGACY_TO_CANONICAL = {v["legacy"].lower(): k for k, v in CANONICAL_VARS.items()}

# Default source for each variable when the caller does not specify one.
DEFAULT_SOURCE = {name: "agera5" for name in CANONICAL_VARS}
DEFAULT_SOURCE["AGRO.PRCP"] = "chirps"

RAINY_DAY_THRESHOLD_MM = 2.0  # same threshold as the fertilizer ML pipeline


def canonical_name(variable: str) -> str:
    """Resolve 'AGRO.PRCP', 'PRCP' or legacy 'Precipitation' to the canonical name."""
    v = variable.strip()
    if v in CANONICAL_VARS:
        return v
    if v.upper() in _SHORT_TO_CANONICAL:
        return _SHORT_TO_CANONICAL[v.upper()]
    if v.lower() in _LEGACY_TO_CANONICAL:
        return _LEGACY_TO_CANONICAL[v.lower()]
    raise ValueError(
        f"Unknown variable '{variable}'. Known: "
        f"{sorted(CANONICAL_VARS)} (or short/legacy names)"
    )


def short_name(variable: str) -> str:
    return CANONICAL_VARS[canonical_name(variable)]["short"]


def legacy_name(variable: str) -> str:
    return CANONICAL_VARS[canonical_name(variable)]["legacy"]


def monthly_how(variable: str) -> str:
    return CANONICAL_VARS[canonical_name(variable)]["monthly"]


# ---------------------------------------------------------------------------
# Unit conversions declared in the catalog ("conversion" field).
_CONVERSIONS = {
    None: lambda x: x,
    "none": lambda x: x,
    "k_to_degc": lambda x: x - 273.15,
    "jm2_to_mjm2": lambda x: x / 1_000_000.0,
}


def apply_conversion(da: xr.DataArray, conversion: Union[str, None]) -> xr.DataArray:
    key = conversion.lower() if isinstance(conversion, str) else conversion
    if key not in _CONVERSIONS:
        raise ValueError(
            f"Unknown unit conversion '{conversion}'. Known: "
            f"{[k for k in _CONVERSIONS if k]}"
        )
    return _CONVERSIONS[key](da)


# ---------------------------------------------------------------------------
_DIM_RENAMES = {
    "latitude": "lat",
    "longitude": "lon",
    "Latitude": "lat",
    "Longitude": "lon",
    "y": "lat",
    "x": "lon",
}


def standardize(da: xr.DataArray, variable: str, source_id: str) -> xr.DataArray:
    """Apply the AgWise conventions to a raw daily DataArray.

    Renames dims to (time, lat, lon), sorts latitude ascending, floors the
    time coordinate to daily dates, renames the variable to its short name
    and attaches provenance attributes. Unit conversion is NOT done here —
    it is declared per-source in the catalog and applied by the driver.
    """
    canonical = canonical_name(variable)
    meta = CANONICAL_VARS[canonical]

    renames = {k: v for k, v in _DIM_RENAMES.items() if k in da.dims}
    if renames:
        da = da.rename(renames)
    missing = {"time", "lat", "lon"} - set(da.dims)
    if missing:
        raise ValueError(
            f"Cannot standardize '{variable}' from {source_id}: missing dims {missing} "
            f"(found {list(da.dims)})"
        )

    # Daily timestamps at midnight, latitude ascending.
    da = da.assign_coords(time=pd.DatetimeIndex(da["time"].values).normalize())
    if da.lat.size > 1 and float(da.lat[0]) > float(da.lat[-1]):
        da = da.sortby("lat")

    da = da.rename(meta["short"]).astype("float32")
    da.attrs.update(
        {
            "agwise_name": canonical,
            "long_name": meta["long_name"],
            "units": meta["units"],
            "source": source_id,
        }
    )
    return da


# ---------------------------------------------------------------------------
def to_monthly(da: xr.DataArray, variable: str) -> xr.DataArray:
    """Aggregate a daily DataArray to monthly (sum for PRCP, mean otherwise).

    Cells that are all-NaN in a month stay NaN (matches the terra
    ``na.rm=TRUE`` behaviour of the legacy scripts for masked areas).
    """
    how = monthly_how(variable)
    resampler = da.resample(time="MS")
    if how == "sum":
        out = resampler.sum(skipna=True, min_count=1)
    else:
        out = resampler.mean(skipna=True)
    out.attrs.update(da.attrs)
    out.attrs["temporal_aggregation"] = f"monthly {how} of daily values"
    return out


def rainy_days(daily_precip: xr.DataArray, threshold: float = RAINY_DAY_THRESHOLD_MM):
    """Count of days with precipitation >= threshold along time."""
    wet = daily_precip >= threshold
    # keep NaN where the whole series is NaN (e.g. ocean/masked cells)
    count = wet.sum(dim="time")
    all_nan = daily_precip.isnull().all(dim="time")
    return count.where(~all_nan)


def time_labels(da: xr.DataArray, freq: str) -> list:
    """Band labels for exported rasters: '2005_01' (monthly) / '2005_01_15' (daily)."""
    fmt = "%Y_%m" if freq == "monthly" else "%Y_%m_%d"
    return [pd.Timestamp(t).strftime(fmt) for t in np.atleast_1d(da["time"].values)]
