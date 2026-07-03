"""Python quickstart for the AgWise data access layer.

Run inside the agwise_data conda env (see docs/cglabs_setup.md). The first
run downloads into the shared cache; later runs are cache hits.
"""

import pandas as pd

from agwise_data import extract_growing_season, get_climate

# --- 1. Monthly rainfall cube for Rwanda (CHIRPS), NetCDF + GeoTIFF -------
res = get_climate(
    variables="AGRO.PRCP",
    years=range(2015, 2025),
    country="Rwanda",
    freq="monthly",
    out_format=["nc", "tif"],
)
prcp = res["AGRO.PRCP"]["data"]
print(prcp)
print("NetCDF:", res["AGRO.PRCP"]["nc"])
print("GeoTIFF:", res["AGRO.PRCP"]["tif"])

# --- 2. Growing-season climate at trial points -----------------------------
trials = pd.DataFrame(
    {
        "lon": [29.8, 30.1],
        "lat": [-1.9, -2.1],
        "Pl_date": ["2022-09-15", "2022-10-01"],
        "Hv_date": ["2023-01-20", "2023-02-05"],
    }
)
out = extract_growing_season(
    trials,
    variables=["Precipitation", "TemperatureMax"],
    planting_col="Pl_date",
    harvest_col="Hv_date",
)
print(out.filter(regex="_m1$|totalRF|nrRainyDays"))
