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

# ---------------------------------------------------------------------------
# Canonical STATIC variables (no time axis): topography and soil properties.
#   depth      : True for layers with a depth dimension (SoilGrids depths)
#   derived    : computed from another static variable instead of fetched
#                (slope/aspect/TPI/TRI come from elevation)
STATIC_VARS = {
    "TOPO.ELEV": {
        "short": "ELEV",
        "long_name": "Elevation above sea level",
        "units": "m",
        "legacy": "altitude",
    },
    "TOPO.SLOPE": {
        "short": "SLOPE",
        "long_name": "Terrain slope",
        "units": "degree",
        "legacy": "slope",
        "derived": "TOPO.ELEV",
    },
    "TOPO.ASPECT": {
        "short": "ASPECT",
        "long_name": "Terrain aspect (clockwise from north)",
        "units": "degree",
        "legacy": "aspect",
        "derived": "TOPO.ELEV",
    },
    "TOPO.TPI": {
        "short": "TPI",
        "long_name": "Topographic position index",
        "units": "m",
        "legacy": "TPI",
        "derived": "TOPO.ELEV",
    },
    "TOPO.TRI": {
        "short": "TRI",
        "long_name": "Terrain ruggedness index",
        "units": "m",
        "legacy": "TRI",
        "derived": "TOPO.ELEV",
    },
    "SOIL.CLAY": {
        "short": "CLAY",
        "long_name": "Clay content",
        "units": "%",
        "legacy": "clay",
        "depth": True,
    },
    "SOIL.SAND": {
        "short": "SAND",
        "long_name": "Sand content",
        "units": "%",
        "legacy": "sand",
        "depth": True,
    },
    "SOIL.SILT": {
        "short": "SILT",
        "long_name": "Silt content",
        "units": "%",
        "legacy": "silt",
        "depth": True,
    },
    "SOIL.PH": {
        "short": "PH",
        "long_name": "Soil pH in water",
        "units": "pH",
        "legacy": "pH",
        "depth": True,
    },
    "SOIL.SOC": {
        "short": "SOC",
        "long_name": "Soil organic carbon content",
        "units": "g kg-1",
        "legacy": "SOC",
        "depth": True,
    },
    "SOIL.NITROGEN": {
        "short": "NITROGEN",
        "long_name": "Total nitrogen content",
        "units": "g kg-1",
        "legacy": "N",
        "depth": True,
    },
    "SOIL.CEC": {
        "short": "CEC",
        "long_name": "Cation exchange capacity",
        "units": "cmol(c) kg-1",
        "legacy": "CEC",
        "depth": True,
    },
    "SOIL.BDOD": {
        "short": "BDOD",
        "long_name": "Bulk density of the fine earth fraction",
        "units": "kg dm-3",
        "legacy": "BD",
        "depth": True,
    },
    "SOIL.CFVO": {
        "short": "CFVO",
        "long_name": "Coarse fragments volumetric fraction",
        "units": "vol%",
        "legacy": "CF",
        "depth": True,
    },
    "SOIL.WV0010": {
        "short": "WV0010",
        "long_name": "Volumetric water content at 10 kPa",
        "units": "vol%",
        "legacy": "WV0010",
        "depth": True,
    },
    "SOIL.WV0033": {
        "short": "WV0033",
        "long_name": "Volumetric water content at 33 kPa (field capacity)",
        "units": "vol%",
        "legacy": "WV0033",
        "depth": True,
    },
    "SOIL.WV1500": {
        "short": "WV1500",
        "long_name": "Volumetric water content at 1500 kPa (wilting point)",
        "units": "vol%",
        "legacy": "WV1500",
        "depth": True,
    },
    "LC.CROPLAND": {
        "short": "CROPLAND",
        "long_name": "Cropland mask (ESA WorldCover class 40, 1 = cropland)",
        "units": "1",
        "legacy": "cropmask",
    },
}

_STATIC_SHORT = {v["short"]: k for k, v in STATIC_VARS.items()}
_STATIC_LEGACY = {v["legacy"].lower(): k for k, v in STATIC_VARS.items()}

# ---------------------------------------------------------------------------
# Canonical REMOTE-SENSING variables: vegetation-index composite stacks
# (irregular ~8/16-day time axis, not daily cubes). The legacy names match
# the layer labels the planting-date phenology scripts already use.
RS_VARS = {
    "RS.NDVI": {
        "short": "NDVI",
        "long_name": "Normalized difference vegetation index (16-day composite)",
        "units": "1",
        "legacy": "NDVI",
    },
    "RS.EVI": {
        "short": "EVI",
        "long_name": "Enhanced vegetation index (16-day composite)",
        "units": "1",
        "legacy": "EVI",
    },
}

_RS_SHORT = {v["short"]: k for k, v in RS_VARS.items()}
_RS_LEGACY = {v["legacy"].lower(): k for k, v in RS_VARS.items()}

# Default source for each variable when the caller does not specify one.
DEFAULT_SOURCE = {name: "agera5" for name in CANONICAL_VARS}
DEFAULT_SOURCE["AGRO.PRCP"] = "chirps"

def _default_static_source(name: str) -> str:
    if name.startswith("TOPO."):
        return "cop_dem30"
    if name.startswith("LC."):
        return "esa_worldcover"
    return "soilgrids"


DEFAULT_STATIC_SOURCE = {name: _default_static_source(name) for name in STATIC_VARS}

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
def static_canonical_name(variable: str) -> str:
    """Resolve 'SOIL.CLAY', 'CLAY' or legacy 'clay' to the canonical static name."""
    v = variable.strip()
    if v in STATIC_VARS:
        return v
    if v.upper() in _STATIC_SHORT:
        return _STATIC_SHORT[v.upper()]
    if v.lower() in _STATIC_LEGACY:
        return _STATIC_LEGACY[v.lower()]
    raise ValueError(
        f"Unknown static variable '{variable}'. Known: "
        f"{sorted(STATIC_VARS)} (or short/legacy names)"
    )


def static_short_name(variable: str) -> str:
    return STATIC_VARS[static_canonical_name(variable)]["short"]


def static_has_depth(variable: str) -> bool:
    return bool(STATIC_VARS[static_canonical_name(variable)].get("depth"))


def static_derived_from(variable: str):
    """The canonical static variable this one is derived from, or None."""
    return STATIC_VARS[static_canonical_name(variable)].get("derived")


# ---------------------------------------------------------------------------
def rs_canonical_name(variable: str) -> str:
    """Resolve 'RS.NDVI', 'NDVI' or a legacy label to the canonical RS name."""
    v = variable.strip()
    if v in RS_VARS:
        return v
    if v.upper() in _RS_SHORT:
        return _RS_SHORT[v.upper()]
    if v.lower() in _RS_LEGACY:
        return _RS_LEGACY[v.lower()]
    raise ValueError(
        f"Unknown remote-sensing variable '{variable}'. Known: "
        f"{sorted(RS_VARS)} (or short names)"
    )


def rs_short_name(variable: str) -> str:
    return RS_VARS[rs_canonical_name(variable)]["short"]


# ---------------------------------------------------------------------------
# Unit conversions declared in the catalog ("conversion" field).
_CONVERSIONS = {
    None: lambda x: x,
    "none": lambda x: x,
    "k_to_degc": lambda x: x - 273.15,
    "jm2_to_mjm2": lambda x: x / 1_000_000.0,
    "m_to_mm": lambda x: x * 1000.0,
    # SoilGrids stores scaled integers; /10 and /100 recover mapped units.
    "d10": lambda x: x / 10.0,
    "d100": lambda x: x / 100.0,
    # MODIS vegetation indices store NDVI/EVI * 10000 as int16.
    "d10000": lambda x: x / 10000.0,
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

    # Daily timestamps at midnight, latitude ascending, canonical dim order.
    da = da.assign_coords(time=pd.DatetimeIndex(da["time"].values).normalize())
    if da.lat.size > 1 and float(da.lat[0]) > float(da.lat[-1]):
        da = da.sortby("lat")
    da = da.transpose("time", "lat", "lon")

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


def standardize_seasonal(
    da: xr.DataArray, variable: str, source_id: str
) -> xr.DataArray:
    """Apply the AgWise conventions to a raw seasonal-forecast DataArray.

    Same contract as :func:`standardize` but with an ensemble axis: dims
    become (``member``, ``time``, ``lat``, ``lon``) where ``time`` is the
    *valid* date (initialization + lead) and ``member`` the ensemble
    member. Variable names and units are the same ``AGRO.*`` conventions
    as the observations, so hindcast and reference data pair up by name
    for bias correction.
    """
    canonical = canonical_name(variable)
    meta = CANONICAL_VARS[canonical]

    renames = {k: v for k, v in _DIM_RENAMES.items() if k in da.dims}
    if "number" in da.dims:
        renames["number"] = "member"
    if renames:
        da = da.rename(renames)
    missing = {"member", "time", "lat", "lon"} - set(da.dims)
    if missing:
        raise ValueError(
            f"Cannot standardize seasonal '{variable}' from {source_id}: "
            f"missing dims {missing} (found {list(da.dims)})"
        )

    da = da.assign_coords(time=pd.DatetimeIndex(da["time"].values).normalize())
    if da.lat.size > 1 and float(da.lat[0]) > float(da.lat[-1]):
        da = da.sortby("lat")
    da = da.transpose("member", "time", "lat", "lon")

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


def standardize_composite(
    da: xr.DataArray, variable: str, source_id: str
) -> xr.DataArray:
    """Apply the AgWise conventions to a raw composite-stack DataArray.

    Same contract as :func:`standardize` but for remote-sensing composite
    products (``RS.*``): the time axis holds the composite start dates
    (~16-day steps, 23 per year per satellite), not daily values.
    """
    canonical = rs_canonical_name(variable)
    meta = RS_VARS[canonical]

    renames = {k: v for k, v in _DIM_RENAMES.items() if k in da.dims}
    if renames:
        da = da.rename(renames)
    missing = {"time", "lat", "lon"} - set(da.dims)
    if missing:
        raise ValueError(
            f"Cannot standardize composite '{variable}' from {source_id}: "
            f"missing dims {missing} (found {list(da.dims)})"
        )

    da = da.assign_coords(time=pd.DatetimeIndex(da["time"].values).normalize())
    if da.lat.size > 1 and float(da.lat[0]) > float(da.lat[-1]):
        da = da.sortby("lat")
    da = da.sortby("time").transpose("time", "lat", "lon")

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


def standardize_static(da: xr.DataArray, variable: str, source_id: str) -> xr.DataArray:
    """Apply the AgWise conventions to a raw static DataArray.

    Same contract as :func:`standardize` but with no time axis: dims become
    (``depth``,) ``lat``, ``lon`` with latitude ascending, the variable is
    renamed to its short name and provenance attributes are attached.
    """
    canonical = static_canonical_name(variable)
    meta = STATIC_VARS[canonical]

    renames = {k: v for k, v in _DIM_RENAMES.items() if k in da.dims}
    if renames:
        da = da.rename(renames)
    missing = {"lat", "lon"} - set(da.dims)
    if missing:
        raise ValueError(
            f"Cannot standardize '{variable}' from {source_id}: missing dims "
            f"{missing} (found {list(da.dims)})"
        )
    if meta.get("depth") and "depth" not in da.dims:
        raise ValueError(
            f"Static variable '{variable}' needs a 'depth' dimension "
            f"(found {list(da.dims)})"
        )

    if da.lat.size > 1 and float(da.lat[0]) > float(da.lat[-1]):
        da = da.sortby("lat")
    dims = ("depth", "lat", "lon") if "depth" in da.dims else ("lat", "lon")
    da = da.transpose(*dims)

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
    if how == "sum" and "day-1" in out.attrs.get("units", ""):
        out.attrs["units"] = out.attrs["units"].replace("day-1", "month-1")
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
