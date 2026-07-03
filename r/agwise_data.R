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

#' Where is the shared cache?
ad_cache_path <- function() {
  ad_run(c("cache", "path"))$root
}
