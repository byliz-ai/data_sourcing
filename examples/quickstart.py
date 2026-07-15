"""agwise-data quickstart (Python).

Run from the repo root after installing the package
(``conda activate agwise_data`` then ``pip install -e ".[all]"``):

    python examples/quickstart.py

Steps 1-2 need only network access — **no accounts** (CHIRPS + SoilGrids).
Steps 3-4 need credentials (see ``docs/credentials_setup.md``) and are left
commented out so this script runs end-to-end out of the box.
"""

from __future__ import annotations

import os

import pandas as pd

from agwise_data import extract_static_points, get_climate  # noqa: F401
# Also available once credentials are set: get_ndvi, to_dssat, to_wofost, ...

# Where downloads and products are cached. Use your own folder for testing;
# on the shared server point this at the common cache (see docs/cglabs_setup.md).
os.environ.setdefault("AGWISE_DATA_ROOT", os.path.expanduser("~/agwise_data/cache"))


def main() -> None:
    # 1. A rainfall cube for a whole country — CHIRPS, no credentials.
    print("1. get_climate: monthly rainfall for Rwanda (2023) ...")
    res = get_climate("PRCP", years=range(2023, 2024), country="Rwanda",
                      freq="monthly")
    rain = res["AGRO.PRCP"]["data"]
    print(f"   -> cube dims {dict(rain.sizes)}")
    print(f"   -> cached NetCDF: {res['AGRO.PRCP']['nc']}\n")

    # 2. Soil at your own points — SoilGrids, no credentials.
    print("2. extract_static_points: soil at 2 points ...")
    points = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                           "site": ["Kigali", "Nyagatare"]})
    soil = extract_static_points(points, ["CLAY", "SAND", "PH", "SOC"])
    print(soil.to_string(index=False), "\n")

    print("Done. Steps 3-4 below need credentials — see docs/credentials_setup.md.")

    # 3. Crop-model input files (DSSAT) — needs AgERA5 (Copernicus CDS token).
    #    Uncomment once ~/.cdsapirc is set:
    # from agwise_data import to_dssat
    # written = to_dssat(points, planting_date="2023-01-01",
    #                    harvest_date="2023-04-30", out_dir="DSSAT_quickstart",
    #                    station_col="site", country="Rwanda")
    # print("DSSAT files:", written)

    # 4. NDVI — needs Google Earth Engine (AGWISE_GEE_PROJECT + credentials):
    # from agwise_data import get_ndvi
    # ndvi = get_ndvi(years=2023, country="Rwanda")["RS.NDVI"]["data"]
    # print("NDVI cube dims:", dict(ndvi.sizes))


if __name__ == "__main__":
    main()
