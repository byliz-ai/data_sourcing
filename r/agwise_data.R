# agwise_data.R — R interface to the AgWise data access layer.
#
# The R functions shell out to the `agwise-data` CLI (installed with the
# Python package) and read back the files it reports, so R modules keep
# their terra-based workflow with a one-line call and no reticulate/conda
# wiring.
#
# Setup on CGLabs (once):
#   1. conda activate agwise_data                 # see docs/cglabs_setup.md
#   2. In R:  source("r/agwise_data.R")
#   3. Optionally set AGWISE_DATA_BIN if agwise-data is not on the PATH:
#      Sys.setenv(AGWISE_DATA_BIN = "/home/jovyan/.conda-envs/agwise_data/bin/agwise-data")
#
# Requires: jsonlite, terra

`%||%` <- function(a, b) if (is.null(a)) b else a

ad_bin <- function() {
  Sys.getenv("AGWISE_DATA_BIN", "agwise-data")
}

ad_run <- function(args) {
  out <- suppressWarnings(system2(ad_bin(), args, stdout = TRUE, stderr = ""))
  status <- attr(out, "status")
  json_lines <- grep("^\\{", out, value = TRUE)
  if (length(json_lines) == 0) {
    stop("agwise-data returned no result. Is the conda env active? Output:\n",
         paste(out, collapse = "\n"))
  }
  res <- jsonlite::fromJSON(tail(json_lines, 1), simplifyDataFrame = FALSE)
  if (!isTRUE(res$ok) || (!is.null(status) && status != 0)) {
    stop("agwise-data failed: ", res$error %||% "unknown error")
  }
  res
}

#' Fetch a harmonized climate cube for a region.
#'
#' Replaces the per-module download-and-stack scripts (e.g.
#' clim_genmonthlyAdmin). Data is downloaded once into the shared cache and
#' reused by everyone afterwards.
#'
#' @param vars     e.g. c("PRCP", "TMAX") or "AGRO.PRCP" or legacy "Precipitation" names
#' @param years    integer vector, e.g. 2005:2024
#' @param country  country name or ISO3 (alternative: bbox)
#' @param bbox     c(west, south, east, north)
#' @param admin_level,admin_name  restrict to one admin unit
#' @param freq     "monthly" (default) or "daily"
#' @return a named list of terra::SpatRaster (one per variable), or a single
#'   SpatRaster when one variable is requested
ad_get_climate <- function(vars, years, country = NULL, bbox = NULL,
                           admin_level = 0, admin_name = NULL,
                           freq = "monthly", source = NULL,
                           overwrite = FALSE) {
  args <- c("get",
            "--vars", paste(vars, collapse = ","),
            "--years", paste0(min(years), ":", max(years)),
            "--freq", freq,
            "--format", "tif")
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  if (!is.null(source))     args <- c(args, "--source", source)
  if (overwrite)            args <- c(args, "--overwrite")

  res <- ad_run(args)
  rasters <- lapply(res$outputs, function(o) terra::rast(o$tif))
  names(rasters) <- vapply(res$outputs, function(o) o$short, character(1))
  if (length(rasters) == 1) rasters[[1]] else rasters
}

#' Growing-season climate for trial data (fertilizer ML format).
#'
#' For each row of `points` (a data.frame or CSV path with lon/lat and
#' planting/harvest date columns) returns the original data plus
#' Precipitation_m1..mN / TemperatureMax_m1..mN style columns, totalRF and
#' nrRainyDays — the same columns the legacy get_geoSpatialClimate produced.
ad_extract_growing_season <- function(points, vars, planting_col, harvest_col,
                                      lon_col = NULL, lat_col = NULL,
                                      legacy_names = TRUE, source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  out_csv <- tempfile(fileext = ".csv")
  args <- c("extract",
            "--points", points_csv,
            "--vars", paste(vars, collapse = ","),
            "--planting-col", planting_col,
            "--harvest-col", harvest_col,
            "--out", out_csv)
  if (!is.null(lon_col)) args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col)) args <- c(args, "--lat-col", lat_col)
  if (!legacy_names)     args <- c(args, "--agwise-names")
  if (!is.null(source))  args <- c(args, "--source", source)

  res <- ad_run(args)
  utils::read.csv(res$outputs[[1]]$csv)
}

#' Point time series between two dates (long format).
ad_extract_points <- function(points, vars, start, end, freq = "daily",
                              lon_col = NULL, lat_col = NULL, source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  out_csv <- tempfile(fileext = ".csv")
  args <- c("extract",
            "--points", points_csv,
            "--vars", paste(vars, collapse = ","),
            "--start", start, "--end", end,
            "--freq", freq,
            "--out", out_csv)
  if (!is.null(lon_col)) args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col)) args <- c(args, "--lat-col", lat_col)
  if (!is.null(source))  args <- c(args, "--source", source)

  res <- ad_run(args)
  utils::read.csv(res$outputs[[1]]$csv)
}

#' Fetch harmonized static layers (soil, DEM) for a region.
#'
#' The static counterpart of ad_get_climate: elevation and terrain
#' derivatives from the Copernicus GLO-30 DEM, soil properties from
#' SoilGrids 2.0. Soil rasters have one band per depth interval.
#'
#' @param vars   e.g. c("ELEV", "SLOPE") or c("CLAY", "PH") — short,
#'   canonical ("SOIL.CLAY") or legacy ("clay", "altitude") names
#' @param depths soil depth subset, e.g. c("0-5cm", "5-15cm"); NULL = all six
#' @return a named list of terra::SpatRaster (one per variable), or a single
#'   SpatRaster when one variable is requested
ad_get_static <- function(vars, country = NULL, bbox = NULL,
                          admin_level = 0, admin_name = NULL,
                          depths = NULL, source = NULL, overwrite = FALSE) {
  args <- c("get-static",
            "--vars", paste(vars, collapse = ","),
            "--format", "tif")
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  if (!is.null(depths))     args <- c(args, "--depths", paste(depths, collapse = ","))
  if (!is.null(source))     args <- c(args, "--source", source)
  if (overwrite)            args <- c(args, "--overwrite")

  res <- ad_run(args)
  rasters <- lapply(res$outputs, function(o) terra::rast(o$tif))
  names(rasters) <- vapply(res$outputs, function(o) o$short, character(1))
  if (length(rasters) == 1) rasters[[1]] else rasters
}

#' Elevation + terrain derivatives (ELEV, SLOPE, ASPECT, TPI, TRI).
ad_get_dem <- function(vars = c("ELEV", "SLOPE", "ASPECT", "TPI", "TRI"), ...) {
  ad_get_static(vars, ...)
}

#' SoilGrids soil properties (default: the fertilizer-module set).
ad_get_soil <- function(vars = c("CLAY", "SAND", "SILT", "PH", "SOC",
                                 "NITROGEN", "CEC", "BDOD", "CFVO"),
                        depths = NULL, ...) {
  ad_get_static(vars, depths = depths, ...)
}

#' ESA WorldCover cropland mask, aligned to the MODIS NDVI/EVI grid.
#'
#' Returns a terra SpatRaster of 1 (cropland) / NA (non-cropland) on the
#' same ~250 m grid as ad_get_modis(), so the phenology workflow can mask
#' non-cropland by multiplying the composite stack by it.
ad_get_cropmask <- function(...) {
  ad_get_static("CROPLAND", source = "esa_worldcover", ...)
}

#' Seasonal forecast/hindcast cubes (SEAS5) for a region.
#'
#' One initialization month across a range of years (e.g. the 1993:2016
#' hindcast) — the input the planting-date module bias-corrects against
#' the ad_get_climate observations. The product is a NetCDF cube with
#' dims (member, time, lat, lon) where time is the valid date; terra has
#' no ensemble axis, so this returns the NetCDF path(s) instead of a
#' SpatRaster (read with ncdf4/stars, or reduce with ensemble = "mean").
#'
#' @param vars       e.g. c("PRCP", "TMAX") — same AGRO names as observations
#' @param init_month initialization month (1-12)
#' @param years      e.g. 1993:2016
#' @param ensemble   "members" (default), "mean" or "median"
#' @return named list of NetCDF paths (one per variable)
ad_get_seasonal <- function(vars, init_month, years, country = NULL,
                            bbox = NULL, admin_level = 0, admin_name = NULL,
                            ensemble = "members", source = NULL,
                            overwrite = FALSE) {
  args <- c("get-seasonal",
            "--vars", paste(vars, collapse = ","),
            "--init-month", as.character(init_month),
            "--years", paste0(min(years), ":", max(years)),
            "--ensemble", ensemble)
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  if (!is.null(source))     args <- c(args, "--source", source)
  if (overwrite)            args <- c(args, "--overwrite")

  res <- ad_run(args)
  paths <- lapply(res$outputs, function(o) o$nc)
  names(paths) <- vapply(res$outputs, function(o) o$short, character(1))
  paths
}

#' MODIS vegetation-index composites (planting-date phenology input).
#'
#' Terra (MOD13Q1) and Aqua (MYD13Q1) 16-day NDVI/EVI composites at ~250 m,
#' interleaved into the 46-images-per-year series the phenology workflow
#' smooths (satellite = "both", the default; "terra"/"aqua" keep one
#' satellite, 23 images/year). Band labels carry the composite date
#' ("2021_01_17"), so year-based layer selection keeps working.
#' Needs Earth Engine credentials + a registered Cloud project — see
#' docs/credentials_setup.md.
#'
#' @param vars      e.g. "NDVI" (default) or c("NDVI", "EVI")
#' @param years     integer vector, e.g. 2020:2023
#' @param satellite "both" (default), "terra" or "aqua"
#' @return a named list of terra::SpatRaster (one per variable), or a single
#'   SpatRaster when one variable is requested
ad_get_modis <- function(vars = "NDVI", years, country = NULL, bbox = NULL,
                         admin_level = 0, admin_name = NULL,
                         satellite = "both", overwrite = FALSE) {
  args <- c("get-modis",
            "--vars", paste(vars, collapse = ","),
            "--years", paste0(min(years), ":", max(years)),
            "--satellite", satellite,
            "--format", "nc,tif")
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  if (overwrite)            args <- c(args, "--overwrite")

  res <- ad_run(args)
  rasters <- lapply(res$outputs, function(o) terra::rast(o$tif))
  names(rasters) <- vapply(res$outputs, function(o) o$short, character(1))
  if (length(rasters) == 1) rasters[[1]] else rasters
}

#' Climate and/or NDVI already sliced to a growing season.
#'
#' The season-ready delivery: instead of fetching whole years and slicing
#' them yourself, ask for exactly the planting->harvest window. `vars` may
#' mix climate (PRCP, TMAX, ...) and remote-sensing (NDVI, EVI) names.
#' Seasons that cross the calendar year (Sep -> Feb) are handled naturally.
#' NOTE: this is distinct from ad_get_seasonal(), which fetches SEAS5
#' seasonal *forecasts*.
#'
#' Two modes:
#'  * Region: pass country/bbox + planting_date/harvest_date -> a named list
#'    of terra::SpatRaster (one per variable), each sliced to the season.
#'  * Points: pass `points` (data.frame/CSV with lon/lat) -> a data.frame in
#'    long format (point, lon, lat, time, variable, value). Use
#'    planting_col/harvest_col for per-row (per-trial) seasons, or the scalar
#'    planting_date/harvest_date for every point.
#'
#' @param vars          e.g. c("PRCP", "TMAX", "NDVI")
#' @param planting_date season start ISO date (scalar mode)
#' @param harvest_date  season end ISO date (scalar mode)
#' @param points        data.frame or CSV path (point mode); NULL = region
#' @param planting_col,harvest_col per-row date columns (point mode)
#' @return SpatRaster list (region) or a data.frame (points)
ad_get_season <- function(vars, planting_date = NULL, harvest_date = NULL,
                          country = NULL, bbox = NULL, admin_level = 0,
                          admin_name = NULL, points = NULL,
                          planting_col = NULL, harvest_col = NULL,
                          lon_col = NULL, lat_col = NULL, freq = "daily",
                          satellite = "both", source = NULL,
                          overwrite = FALSE) {
  args <- c("get-season",
            "--vars", paste(vars, collapse = ","),
            "--freq", freq,
            "--satellite", satellite)
  if (!is.null(planting_date)) args <- c(args, "--planting-date", planting_date)
  if (!is.null(harvest_date))  args <- c(args, "--harvest-date", harvest_date)
  if (!is.null(source))        args <- c(args, "--source", source)

  if (!is.null(points)) {
    points_csv <- points
    if (is.data.frame(points)) {
      points_csv <- tempfile(fileext = ".csv")
      utils::write.csv(points, points_csv, row.names = FALSE)
    }
    out_csv <- tempfile(fileext = ".csv")
    args <- c(args, "--points", points_csv, "--out", out_csv)
    if (!is.null(planting_col)) args <- c(args, "--planting-col", planting_col)
    if (!is.null(harvest_col))  args <- c(args, "--harvest-col", harvest_col)
    if (!is.null(lon_col))      args <- c(args, "--lon-col", lon_col)
    if (!is.null(lat_col))      args <- c(args, "--lat-col", lat_col)
    res <- ad_run(args)
    return(utils::read.csv(res$outputs[[1]]$csv))
  }

  args <- c(args, "--format", "nc,tif")
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  if (overwrite)            args <- c(args, "--overwrite")

  res <- ad_run(args)
  rasters <- lapply(res$outputs, function(o) terra::rast(o$tif))
  names(rasters) <- vapply(res$outputs, function(o) o$short, character(1))
  if (length(rasters) == 1) rasters[[1]] else rasters
}

#' Bias-correct a SEAS5 seasonal forecast (QDM) against hindcast-vs-obs.
#'
#' Scope-map #3: learns the model bias from hindcast vs observations over
#' `calib_years` and applies Quantile Delta Mapping to the `forecast_year`
#' forecast (additive for temperatures, multiplicative for PRCP/SRAD). Both
#' input halves come from the layer (`get_seasonal` + `get_climate`). Returns
#' a named list of bias-corrected NetCDF cube paths (member,time,lat,lon).
#' NOTE: distinct from `get_seasonal` (raw forecast) — this is the corrected,
#' analysis-ready output.
ad_bias_correct <- function(vars, init_month, forecast_year, calib_years,
                            country = NULL, bbox = NULL, admin_level = 0,
                            admin_name = NULL, window_days = NULL,
                            source = NULL, overwrite = FALSE) {
  args <- c("bias-correct",
            "--vars", paste(vars, collapse = ","),
            "--init-month", as.character(init_month),
            "--forecast-year", as.character(forecast_year),
            "--calib-years", paste0(min(calib_years), ":", max(calib_years)))
  if (!is.null(country))     args <- c(args, "--country", country)
  if (!is.null(bbox))        args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)       args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name))  args <- c(args, "--admin-name", admin_name)
  if (!is.null(window_days)) args <- c(args, "--window-days", as.character(window_days))
  if (!is.null(source))      args <- c(args, "--source", source)
  if (overwrite)             args <- c(args, "--overwrite")

  res <- ad_run(args)
  paths <- lapply(res$outputs, function(o) o$nc)
  names(paths) <- vapply(res$outputs, function(o) o$short, character(1))
  paths
}

#' Bias-corrected seasonal forecast -> DSSAT weather+soil files at points.
#'
#' Scope-map #3b: QDM-corrects the SEAS5 forecast (bias_correct), samples it at
#' each point, reduces the ensemble (mean/median), and writes EXTE<n>/WHTE<n>.WTH
#' + SOIL.SOL via the DSSAT writer. Returns a data.frame of the files written.
ad_forecast_to_dssat <- function(points, init_month, forecast_year, calib_years,
                                 out_dir = NULL, ensemble = "mean",
                                 window_days = NULL, country = NULL, bbox = NULL,
                                 admin_level = 0, admin_name = NULL,
                                 lon_col = NULL, lat_col = NULL, id_col = NULL,
                                 station_col = NULL, country_name = NULL,
                                 soil_source = NULL, weather_source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  args <- c("forecast-to-dssat", "--points", points_csv,
            "--init-month", as.character(init_month),
            "--forecast-year", as.character(forecast_year),
            "--calib-years", paste0(min(calib_years), ":", max(calib_years)),
            "--ensemble", ensemble)
  if (!is.null(out_dir))        args <- c(args, "--out-dir", out_dir)
  if (!is.null(window_days))    args <- c(args, "--window-days", as.character(window_days))
  if (!is.null(country))        args <- c(args, "--country", country)
  if (!is.null(bbox))           args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)          args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name))     args <- c(args, "--admin-name", admin_name)
  if (!is.null(lon_col))        args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col))        args <- c(args, "--lat-col", lat_col)
  if (!is.null(id_col))         args <- c(args, "--id-col", id_col)
  if (!is.null(station_col))    args <- c(args, "--station-col", station_col)
  if (!is.null(country_name))   args <- c(args, "--country-name", country_name)
  if (!is.null(soil_source))    args <- c(args, "--soil-source", soil_source)
  if (!is.null(weather_source)) args <- c(args, "--weather-source", weather_source)

  res <- ad_run(args)
  do.call(rbind, lapply(res$outputs, function(o) as.data.frame(o, stringsAsFactors = FALSE)))
}

#' Regular AOI point grid clipped to a country/admin boundary.
#'
#' Replaces per-module get_GridCoordinates: a ~res_km grid over the boundary,
#' clipped to it, each point tagged with country/NAME_1/NAME_2. Returns a
#' data.frame (lon, lat, country, NAME_1, NAME_2). With bbox only, returns the
#' full rectangular grid (no clip, no admin tags).
ad_make_grid <- function(country = NULL, bbox = NULL, admin_level = 0,
                         admin_name = NULL, res_km = 5, tag_admin_level = 2) {
  out_csv <- tempfile(fileext = ".csv")
  args <- c("make-grid", "--out", out_csv,
            "--res-km", as.character(res_km),
            "--tag-admin-level", as.character(tag_admin_level))
  if (!is.null(country))    args <- c(args, "--country", country)
  if (!is.null(bbox))       args <- c(args, "--bbox", paste(bbox, collapse = ","))
  if (admin_level > 0)      args <- c(args, "--admin-level", admin_level)
  if (!is.null(admin_name)) args <- c(args, "--admin-name", admin_name)
  res <- ad_run(args)
  utils::read.csv(res$outputs[[1]]$csv)
}

#' Assign admin unit names to points (field↔geospatial link).
#'
#' The reusable half of the modules' extract_geoSpatialPointData: tag each
#' trial/point coordinate with country and NAME_1 (and NAME_2 when
#' admin_level >= 2) via point-in-polygon against geoBoundaries. Returns the
#' input data.frame with those columns added.
ad_tag_admin <- function(points, country, admin_level = 2,
                         lon_col = NULL, lat_col = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  out_csv <- tempfile(fileext = ".csv")
  args <- c("tag-admin", "--points", points_csv, "--country", country,
            "--out", out_csv, "--admin-level", as.character(admin_level))
  if (!is.null(lon_col)) args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col)) args <- c(args, "--lat-col", lat_col)
  res <- ad_run(args)
  utils::read.csv(res$outputs[[1]]$csv)
}

#' Write DSSAT weather (.WTH) + soil (.SOL) files for trial/AOI points.
#'
#' The "last mile" that retires the per-module readGeo_CM_zone.R: for each
#' row of `points` (data.frame/CSV with lon/lat) it writes
#' out_dir/EXTE<n>/WHTE<n>.WTH (season-sliced weather with TAV/AMP) and
#' out_dir/EXTE<n>/SOIL.SOL (SoilGrids + Saxton-Rawls hydraulics). Use
#' planting_col/harvest_col for per-row seasons or scalar planting_date/
#' harvest_date for all points. Returns a data.frame of the files written.
ad_to_dssat <- function(points, planting_date = NULL, harvest_date = NULL,
                        out_dir = NULL, planting_col = NULL, harvest_col = NULL,
                        lon_col = NULL, lat_col = NULL, id_col = NULL,
                        station_col = NULL, country = NULL,
                        weather_source = NULL, soil_source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  args <- c("to-dssat", "--points", points_csv)
  if (!is.null(out_dir))        args <- c(args, "--out-dir", out_dir)
  if (!is.null(planting_date))  args <- c(args, "--planting-date", planting_date)
  if (!is.null(harvest_date))   args <- c(args, "--harvest-date", harvest_date)
  if (!is.null(planting_col))   args <- c(args, "--planting-col", planting_col)
  if (!is.null(harvest_col))    args <- c(args, "--harvest-col", harvest_col)
  if (!is.null(lon_col))        args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col))        args <- c(args, "--lat-col", lat_col)
  if (!is.null(id_col))         args <- c(args, "--id-col", id_col)
  if (!is.null(station_col))    args <- c(args, "--station-col", station_col)
  if (!is.null(country))        args <- c(args, "--country", country)
  if (!is.null(weather_source)) args <- c(args, "--weather-source", weather_source)
  if (!is.null(soil_source))    args <- c(args, "--soil-source", soil_source)

  res <- ad_run(args)
  do.call(rbind, lapply(res$outputs, function(o) as.data.frame(o, stringsAsFactors = FALSE)))
}

#' Write APSIM weather (.met) + soil-layer table for trial/AOI points.
#'
#' APSIM counterpart of ad_to_dssat: writes out_dir/EXTE<n>/wth_loc_<n>.met
#' and out_dir/EXTE<n>/soil_<n>.csv (the per-layer LL15/DUL/SAT/AirDry/KS/BD/
#' Carbon/clay/silt/N/PH/CEC table the apsimx soil template needs, with
#' Salb/CN2Bare in a header comment). Returns a data.frame of files written.
ad_to_apsim <- function(points, planting_date = NULL, harvest_date = NULL,
                        out_dir = NULL, planting_col = NULL, harvest_col = NULL,
                        lon_col = NULL, lat_col = NULL, id_col = NULL,
                        station_col = NULL,
                        weather_source = NULL, soil_source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  args <- c("to-apsim", "--points", points_csv)
  if (!is.null(out_dir))        args <- c(args, "--out-dir", out_dir)
  if (!is.null(planting_date))  args <- c(args, "--planting-date", planting_date)
  if (!is.null(harvest_date))   args <- c(args, "--harvest-date", harvest_date)
  if (!is.null(planting_col))   args <- c(args, "--planting-col", planting_col)
  if (!is.null(harvest_col))    args <- c(args, "--harvest-col", harvest_col)
  if (!is.null(lon_col))        args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col))        args <- c(args, "--lat-col", lat_col)
  if (!is.null(id_col))         args <- c(args, "--id-col", id_col)
  if (!is.null(station_col))    args <- c(args, "--station-col", station_col)
  if (!is.null(weather_source)) args <- c(args, "--weather-source", weather_source)
  if (!is.null(soil_source))    args <- c(args, "--soil-source", soil_source)

  res <- ad_run(args)
  do.call(rbind, lapply(res$outputs, function(o) as.data.frame(o, stringsAsFactors = FALSE)))
}

#' Write WOFOST weather + soil-parameter CSVs for trial/AOI points.
#'
#' WOFOST counterpart of ad_to_dssat: for each row of `points` writes
#' out_dir/EXTE<n>/weather_<n>.csv (WOFOST columns date, srad [kJ/m2/day],
#' tmin, tmax, vapr [kPa], wind, prec) and out_dir/EXTE<n>/soil_<n>.csv
#' (SMW/SMFCF/SM0/K0 from the Saxton-Rawls hydraulics over the top metre,
#' plus the WOFOST soil defaults). WOFOST reads weather/soil as R lists, so
#' these tidy CSVs are the deliverable. Returns a data.frame of files written.
ad_to_wofost <- function(points, planting_date = NULL, harvest_date = NULL,
                         out_dir = NULL, planting_col = NULL, harvest_col = NULL,
                         lon_col = NULL, lat_col = NULL, id_col = NULL,
                         station_col = NULL,
                         weather_source = NULL, soil_source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  args <- c("to-wofost", "--points", points_csv)
  if (!is.null(out_dir))        args <- c(args, "--out-dir", out_dir)
  if (!is.null(planting_date))  args <- c(args, "--planting-date", planting_date)
  if (!is.null(harvest_date))   args <- c(args, "--harvest-date", harvest_date)
  if (!is.null(planting_col))   args <- c(args, "--planting-col", planting_col)
  if (!is.null(harvest_col))    args <- c(args, "--harvest-col", harvest_col)
  if (!is.null(lon_col))        args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col))        args <- c(args, "--lat-col", lat_col)
  if (!is.null(id_col))         args <- c(args, "--id-col", id_col)
  if (!is.null(station_col))    args <- c(args, "--station-col", station_col)
  if (!is.null(weather_source)) args <- c(args, "--weather-source", weather_source)
  if (!is.null(soil_source))    args <- c(args, "--soil-source", soil_source)

  res <- ad_run(args)
  do.call(rbind, lapply(res$outputs, function(o) as.data.frame(o, stringsAsFactors = FALSE)))
}

#' Write ORYZA v3 weather + PADDY soil files for trial/AOI points.
#'
#' ORYZA counterpart of ad_to_dssat: for each row of `points` writes, under
#' out_dir/EXTE<n>/, the CABO weather files <code><n>.<yyy> (one per calendar
#' year the season spans; columns station, year, day, srad[kJ], tmin, tmax,
#' vapr[kPa], wind, rain) and the 8-layer PADDY soil_<n>.sol (SoilGrids
#' remapped to ORYZA's fixed layers with the Saxton-Rawls hydraulics).
#' Returns a data.frame with one row per point (weather = the per-year files,
#' ';'-separated).
ad_to_oryza <- function(points, planting_date = NULL, harvest_date = NULL,
                        out_dir = NULL, planting_col = NULL, harvest_col = NULL,
                        lon_col = NULL, lat_col = NULL, id_col = NULL,
                        station_col = NULL,
                        weather_source = NULL, soil_source = NULL) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  args <- c("to-oryza", "--points", points_csv)
  if (!is.null(out_dir))        args <- c(args, "--out-dir", out_dir)
  if (!is.null(planting_date))  args <- c(args, "--planting-date", planting_date)
  if (!is.null(harvest_date))   args <- c(args, "--harvest-date", harvest_date)
  if (!is.null(planting_col))   args <- c(args, "--planting-col", planting_col)
  if (!is.null(harvest_col))    args <- c(args, "--harvest-col", harvest_col)
  if (!is.null(lon_col))        args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col))        args <- c(args, "--lat-col", lat_col)
  if (!is.null(id_col))         args <- c(args, "--id-col", id_col)
  if (!is.null(station_col))    args <- c(args, "--station-col", station_col)
  if (!is.null(weather_source)) args <- c(args, "--weather-source", weather_source)
  if (!is.null(soil_source))    args <- c(args, "--soil-source", soil_source)

  res <- ad_run(args)
  do.call(rbind, lapply(res$outputs, function(o) {
    data.frame(point = o$point, dir = o$dir,
               weather = paste(unlist(o$weather), collapse = ";"),
               soil = o$soil, stringsAsFactors = FALSE)
  }))
}

#' Soil/topography values at point locations (wide format).
#'
#' Returns the input data plus ELEV/SLOPE/... columns and one column per
#' soil property and depth (CLAY_0_5cm, CLAY_5_15cm, ...) — the static
#' counterpart of ad_extract_growing_season for trial data.
#' Points on masked pixels (SoilGrids NoData over urban/water) are filled
#' from the nearest valid pixel within fill_nearest_m meters (0 disables);
#' each variable gets a <VAR>_fill_m traceability column (0 = own pixel,
#' >0 = donor distance, NA = nothing valid in range).
ad_extract_static_points <- function(points, vars, depths = NULL,
                                     lon_col = NULL, lat_col = NULL,
                                     source = NULL, fill_nearest_m = 1000,
                                     derive = NULL, calcareous = FALSE) {
  points_csv <- points
  if (is.data.frame(points)) {
    points_csv <- tempfile(fileext = ".csv")
    utils::write.csv(points, points_csv, row.names = FALSE)
  }
  out_csv <- tempfile(fileext = ".csv")
  args <- c("extract-static",
            "--points", points_csv,
            "--vars", paste(vars, collapse = ","),
            "--out", out_csv)
  if (!is.null(depths))  args <- c(args, "--depths", paste(depths, collapse = ","))
  if (!is.null(lon_col)) args <- c(args, "--lon-col", lon_col)
  if (!is.null(lat_col)) args <- c(args, "--lat-col", lat_col)
  if (!is.null(source))  args <- c(args, "--source", source)
  if (!is.null(derive))  args <- c(args, "--derive", paste(derive, collapse = ","))
  if (isTRUE(calcareous)) args <- c(args, "--calcareous")
  args <- c(args, "--fill-nearest-m", as.character(fill_nearest_m))

  res <- ad_run(args)
  utils::read.csv(res$outputs[[1]]$csv)
}

#' Where is the shared cache?
ad_cache_path <- function() {
  ad_run(c("cache", "path"))$root
}
