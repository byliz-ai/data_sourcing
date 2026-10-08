# agwise-data quickstart (R).
#
# Run from the repo root after activating the shared env (see README §2.2):
#     Rscript examples/quickstart.R
# or open in RStudio and source it.
#
# Step 1 needs only network access — no accounts (SoilGrids). Step 2 (rainfall)
# needs no account on CGLabs (local CHIRPS v3, 1981-2025); elsewhere CHIRPS v2
# comes through Earth Engine while the UCSB host is 403-blocked, so the step is
# wrapped. Step 3 needs credentials (see docs/credentials_setup.md).
# Every result is quality-checked — see docs/quality_control.md.

source("r/agwise_data.R")   # adjust the path if you run from another folder

# If `agwise-data` is not on the PATH R sees, point R at the console script:
# Sys.setenv(AGWISE_DATA_BIN = "/home/jovyan/agwise-datasourcing/envs/agwise_data/bin/agwise-data")

# 1. Soil at your own points — SoilGrids, NO account needed -> data.frame.
#    (Pass source = "isda" for iSDA Africa instead; needs AGWISE_LOCAL_ROOT.)
cat("1. ad_extract_static_points: soil at 2 points (SoilGrids, no account) ...\n")
pts <- data.frame(lon = c(30.06, 30.10), lat = c(-1.95, -1.90),
                  site = c("Kigali", "Nyagatare"))
soil <- ad_extract_static_points(pts, c("CLAY", "SAND", "PH", "SOC"))
print(head(soil))

# 2. Rainfall — CHIRPS. Local CHIRPS v3 on CGLabs (no account); elsewhere it
#    needs Earth Engine (AGWISE_GEE_PROJECT). Wrapped so the script finishes.
cat("\n2. ad_get_climate: monthly rainfall for Rwanda 2023 (CHIRPS) ...\n")
rain <- try(ad_get_climate("PRCP", 2023, country = "Rwanda", freq = "monthly"),
            silent = TRUE)
if (inherits(rain, "try-error")) {
  cat("   -> skipped: off CGLabs, CHIRPS needs Earth Engine",
      "(set AGWISE_GEE_PROJECT); see docs/credentials_setup.md\n")
} else {
  print(rain)
}

cat("\nDone. Step 3 below needs credentials — see docs/credentials_setup.md.\n")

# 3. DSSAT input files — needs AgERA5 (Copernicus CDS token). Uncomment when ready:
# ad_to_dssat(pts, planting_date = "2023-01-01", harvest_date = "2023-04-30",
#             out_dir = "DSSAT_quickstart", station_col = "site", country = "Rwanda")
# Then read the run's quality report:
# qc <- jsonlite::fromJSON("DSSAT_quickstart/qc_report.json"); qc$summary
