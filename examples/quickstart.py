"""agwise-data quickstart (Python).

Run from the repo root after activating the shared env (or installing off
CGLabs) — see README §2.2:

    python examples/quickstart.py

Step 1 needs only network access — **no accounts** (SoilGrids). Step 2 (rainfall)
needs no account on CGLabs (local CHIRPS v3, 1981-2025); elsewhere CHIRPS v2
comes through Earth Engine while the UCSB host is 403-blocked, so the step is
guarded. Steps 3-4 need credentials (see ``docs/credentials_setup.md``) and are
left commented out. Every result is quality-checked; step 2 prints its report
(see ``docs/quality_control.md``).
"""

from __future__ import annotations

import json

import pandas as pd

from agwise_data import extract_static_points, get_climate

# Downloads/products are cached automatically — the shared Global_GeoData/Processed
# on CGLabs, or ~/agwise_data off it. Leave AGWISE_DATA_ROOT unset on CGLabs to use
# the shared cache; set it to relocate (see docs/cglabs_setup.md §2).

POINTS = pd.DataFrame({"lon": [30.06, 30.10], "lat": [-1.95, -1.90],
                       "site": ["Kigali", "Nyagatare"]})


def main() -> None:
    # 1. Soil at your own points — SoilGrids, NO account needed. Runs as-is.
    #    (Pass source="isda" for iSDA Africa instead; needs AGWISE_LOCAL_ROOT.)
    print("1. extract_static_points: soil at 2 points (SoilGrids, no account) ...")
    soil = extract_static_points(POINTS, ["CLAY", "SAND", "PH", "SOC"])
    print(soil.to_string(index=False), "\n")

    # 2. Rainfall cube — CHIRPS. On CGLabs it is read from the staged CHIRPS v3
    #    (no account). Elsewhere CHIRPS v2 comes through Earth Engine while the
    #    UCSB host is 403-blocked. Guarded so the script still finishes.
    print("2. get_climate: monthly rainfall for Rwanda 2023 (CHIRPS) ...")
    try:
        res = get_climate("PRCP", years=range(2023, 2024), country="Rwanda",
                          freq="monthly")
        rain = res["AGRO.PRCP"]["data"]
        print(f"   -> cube dims {dict(rain.sizes)}; cached {res['AGRO.PRCP']['nc']}")
        # Quality report: how many values were impossible (set to NaN) or unusual.
        qc = json.loads(res["AGRO.PRCP"]["qc"].read_text())
        print(f"   -> QC: physical outliers {qc['physical']}, "
              f"plausible outliers {qc['plausible']}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"   -> skipped: {type(exc).__name__}. Off CGLabs, CHIRPS needs Earth "
              "Engine (set AGWISE_GEE_PROJECT); see docs/credentials_setup.md\n")

    print("Done. Steps 3-4 below need credentials — see docs/credentials_setup.md.")

    # 3. Crop-model input files (DSSAT) — needs AgERA5 (Copernicus CDS token).
    #    Uncomment once ~/.cdsapirc is set:
    # from agwise_data import to_dssat
    # to_dssat(POINTS, planting_date="2023-01-01", harvest_date="2023-04-30",
    #          out_dir="DSSAT_quickstart", station_col="site", country="Rwanda")
    # Then open DSSAT_quickstart/qc_report.json: gap-filled days, TMIN/TMAX
    # swaps, soil texture fixes and the validation of every written file.

    # 4. NDVI — needs Google Earth Engine (AGWISE_GEE_PROJECT + credentials):
    # from agwise_data import get_ndvi
    # ndvi = get_ndvi(years=2023, country="Rwanda")["RS.NDVI"]["data"]


if __name__ == "__main__":
    main()
