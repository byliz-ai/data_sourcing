# agwise-data quickstart (R).
#
# Run from the repo root after installing the package (see the main README):
#     Rscript examples/quickstart.R
# or open in RStudio and source it.
#
# Steps 1-2 need only network access — no accounts (CHIRPS + SoilGrids).
# Step 3 needs credentials (see docs/credentials_setup.md) and is commented out.

source("r/agwise_data.R")   # adjust the path if you run from another folder

# If `agwise-data` is not on the PATH R sees, point R at the console script:
# Sys.setenv(AGWISE_DATA_BIN = "/home/jovyan/.conda-envs/agwise_data/bin/agwise-data")

# 1. Rainfall for a whole country — CHIRPS, no credentials -> terra SpatRaster
cat("1. ad_get_climate: monthly rainfall for Rwanda (2023) ...\n")
rain <- ad_get_climate("PRCP", 2023, country = "Rwanda", freq = "monthly")
print(rain)

# 2. Soil at your own points — SoilGrids, no credentials -> data.frame
cat("\n2. ad_extract_static_points: soil at 2 points ...\n")
pts <- data.frame(lon = c(30.06, 30.10), lat = c(-1.95, -1.90),
                  site = c("Kigali", "Nyagatare"))
soil <- ad_extract_static_points(pts, c("CLAY", "SAND", "PH", "SOC"))
print(head(soil))

cat("\nDone. Step 3 below needs credentials — see docs/credentials_setup.md.\n")

# 3. DSSAT input files — needs AgERA5 (Copernicus CDS token). Uncomment when ready:
# ad_to_dssat(pts, planting_date = "2023-01-01", harvest_date = "2023-04-30",
#             out_dir = "DSSAT_quickstart", station_col = "site", country = "Rwanda")
