"""agwise-data quickstart (Python).

Run from the repo root after installing the package (see README §2.2):

    python examples/quickstart.py

Step 1 needs only network access — **no accounts** (SoilGrids). Step 2 (rainfall)
currently needs Earth Engine because the UCSB CHIRPS host is 403-blocked and the
driver falls back to CHIRPS on Earth Engine — it is guarded so the script still
runs. Steps 3-4 need credentials (see ``docs/credentials_setup.md``) and are
left commented out.
"""

from __future__ import annotations

import os

import pandas as pd

from agwise_data import extract_static_points, get_climate

# Where downloads and products are cached. Use your own folder for testing;
# on the shared server point this at the common cache (see docs/cglabs_setup.md).
os.environ.setdefault("AGWISE_DATA_ROOT", os.path.expanduser("~/agwise_data/cache"))

POINTS = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                       "site": ["Kigali", "Nyagatare"]})


def main() -> None:
    # 1. Soil at your own points — SoilGrids, NO account needed. Runs as-is.
    #    (Pass source="isda" for iSDA Africa instead; needs AGWISE_LOCAL_ROOT.)
    print("1. extract_static_points: soil at 2 points (SoilGrids, no account) ...")
    soil = extract_static_points(POINTS, ["CLAY", "SAND", "PH", "SOC"])
    print(soil.to_string(index=False), "\n")

    # 2. Rainfall cube — CHIRPS. While the UCSB host is 403-blocked the driver
    #    uses CHIRPS on Earth Engine, so this needs AGWISE_GEE_PROJECT + GEE
    #    credentials today. Guarded so the script still finishes without them.
    print("2. get_climate: monthly rainfall for Rwanda 2023 (CHIRPS) ...")
    try:
        res = get_climate("PRCP", years=range(2023, 2024), country="Rwanda",
                          freq="monthly")
        rain = res["AGRO.PRCP"]["data"]
        print(f"   -> cube dims {dict(rain.sizes)}; cached {res['AGRO.PRCP']['nc']}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"   -> skipped: {type(exc).__name__}. CHIRPS needs Earth Engine "
              "right now (set AGWISE_GEE_PROJECT); see docs/credentials_setup.md\n")

    print("Done. Steps 3-4 below need credentials — see docs/credentials_setup.md.")

    # 3. Crop-model input files (DSSAT) — needs AgERA5 (Copernicus CDS token).
    #    Uncomment once ~/.cdsapirc is set:
    # from agwise_data import to_dssat
    # to_dssat(POINTS, planting_date="2023-01-01", harvest_date="2023-04-30",
    #          out_dir="DSSAT_quickstart", station_col="site", country="Rwanda")

    # 4. NDVI — needs Google Earth Engine (AGWISE_GEE_PROJECT + credentials):
    # from agwise_data import get_ndvi
    # ndvi = get_ndvi(years=2023, country="Rwanda")["RS.NDVI"]["data"]


if __name__ == "__main__":
    main()
