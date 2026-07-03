# Drop-in replacement for the climate part of get_geoSpatialClimate()
# (fertilizer ML pipeline).
#
# Legacy: ~500 lines looping over growing periods, indexing monthly raster
# layers by position and hand-stitching cross-year seasons — the source of
# several 2026 bug fixes.
#
# Now: one call. Cross-year seasons are handled by the continuous time
# axis; output columns match the legacy names so the ML code is unchanged.

source("/home/jovyan/data_sourcing/r/agwise_data.R")

trials <- read.csv("~/shared-data/Data/Wheat/fieldData/fieldData2026/Wheat_cleaned_Feben2026_dates.csv")

trials_clim <- ad_extract_growing_season(
  points       = trials,
  vars         = c("Precipitation", "TemperatureMax", "TemperatureMin",
                   "SolarRadiation", "RelativeHumidity", "WindSpeed"),
  planting_col = "Pl_date",
  harvest_col  = "Hv_date",
  lon_col      = "X",
  lat_col      = "Y"
)

# trials_clim now has, per row:
#   Precipitation_m1..mN, TemperatureMax_m1..mN, ... (monthly values across
#   the planting->harvest window), plus totalRF and nrRainyDays computed
#   from daily CHIRPS between the exact planting and harvest dates.
# Rows with unparseable dates or planting>harvest keep their original data
# with NA climate columns (a warning reports how many).

saveRDS(trials_clim, "~/shared-data/Data/Wheat/geoSpatial/weather_wheat_2026.RDS")
