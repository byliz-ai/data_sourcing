"""Season slices and crop-model input files (get_season, to_dssat, to_apsim).

Run inside the agwise_data conda env (see docs/cglabs_setup.md). Needs CDS
credentials for the weather half; the first run downloads into the shared
cache and later runs are cache hits. See docs/crop_model_inputs.md.
"""

import pandas as pd

from agwise_data import get_season, to_apsim, to_dssat

# --- 1. A cross-year season slice for a whole country (region mode) --------
# Rwanda season B runs Sep -> Feb; the slice crosses the New Year cleanly.
season = get_season(
    ["PRCP", "TMAX", "NDVI"],          # climate + remote sensing in one call
    planting_date="2020-09-14",
    harvest_date="2021-02-28",
    country="Rwanda",
)
print("NDVI season cube:", season["RS.NDVI"]["data"].sizes)
print("wrote:", season["AGRO.PRCP"]["nc"].name)

# --- 2. Trial points -> DSSAT weather (.WTH) + soil (.SOL) ------------------
trials = pd.DataFrame({
    "lon": [30.10, 30.25],
    "lat": [-2.00, -1.85],
    "site": ["Nyagatare", "Kayonza"],
    "planting": ["2021-01-01", "2021-02-15"],   # per-trial seasons
    "harvest":  ["2021-04-30", "2021-06-15"],
})

dssat = to_dssat(
    trials,
    planting_col="planting", harvest_col="harvest",
    out_dir="DSSAT", station_col="site", country="Rwanda",
)
for r in dssat:
    print(f"{r['dir'].name}: {r['wth'].name} + {r['sol'].name}")

# --- 3. The same points -> APSIM weather (.met) + soil-layer table ---------
apsim = to_apsim(
    trials,
    planting_col="planting", harvest_col="harvest",
    out_dir="APSIM", station_col="site",
)
for r in apsim:
    print(f"{r['dir'].name}: {r['met'].name} + {r['soil'].name}")
