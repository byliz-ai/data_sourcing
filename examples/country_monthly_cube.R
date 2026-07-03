# Drop-in replacement for the legacy clim_genmonthlyCountry() workflow.
#
# Legacy: ~300 lines re-coded per use case — download global AgERA5 year by
# year with ecmwfr, read CHIRPS from ~/common_data, crop/mask with GADM,
# tapp() to monthly, write one .tif per year.
#
# Now: one call per variable. Data is fetched once into the shared cache;
# re-running (or a colleague running the same thing) is a cache hit.

source("/home/jovyan/data_sourcing/r/agwise_data.R")  # adjust to your clone

# Monthly rainfall for Rwanda, 1981-2024 (CHIRPS), as a terra SpatRaster
# with layers named 1981_01 ... 2024_12. "PRCP", "AGRO.PRCP" and the legacy
# "Precipitation" all refer to the same variable:
rain <- ad_get_climate(
  vars    = "PRCP",
  years   = 1981:2024,
  country = "Rwanda",
  freq    = "monthly"
)
print(rain)

# All the AgERA5 variables the fertilizer pipeline uses, for Kenya:
clim <- ad_get_climate(
  vars    = c("TemperatureMax", "TemperatureMin", "TemperatureMean",
              "SolarRadiation", "RelativeHumidity", "WindSpeed"),
  years   = 2005:2024,
  country = "Kenya",
  freq    = "monthly"
)
names(clim)      # "TMAX" "TMIN" "TEMP" "SRAD" "RHUM" "WIND"
plot(clim$TMAX[[1]])

# One admin unit only (e.g. a district at ADM2):
idodi <- ad_get_climate(
  vars = "PRCP", years = 2015:2024, country = "Tanzania",
  admin_level = 2, admin_name = "Idodi", freq = "monthly"
)
