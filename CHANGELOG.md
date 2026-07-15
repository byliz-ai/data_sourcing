# Changelog

All notable changes to `agwise-data`. Versions follow the `version` field in
`pyproject.toml`. Newest first.

## 0.9.1
- **Fix:** add `scipy` to dependencies — `bias_correct` regrids the forecast
  with `xarray.interp`, which needs scipy; a clean install failed without it.

## 0.9.0 — Seasonal-forecast bias correction
- `bias_correct(...)` — Quantile Delta Mapping (Cannon et al. 2015) of a SEAS5
  forecast against the hindcast-vs-observation bias, per variable (additive for
  temperatures, multiplicative for PRCP/SRAD). Returns corrected cubes.
- `forecast_to_dssat(...)` — samples the corrected forecast at points, reduces
  the ensemble, and writes DSSAT weather+soil files.

## 0.8.0 — Spatial scaffolding
- `make_grid(...)` — regular point grid clipped to a country/admin boundary,
  each point tagged with `country`/`NAME_1`/`NAME_2`.
- `tag_admin(...)` — assign admin unit names to arbitrary points
  (field ↔ geospatial link).

## 0.7.0 — Crop-model input files
- `to_dssat(...)` / `to_apsim(...)` — write DSSAT `.WTH`+`.SOL` and APSIM
  `.met`+soil-table per point (Saxton–Rawls hydraulics), verified against the
  DSSAT/apsimx readers.

## 0.6.0 — Season slice
- `get_season(...)` — climate and/or NDVI sliced to a planting→harvest season,
  cross-year aware; region-cube or per-trial-point modes.

## 0.5.0 — Crop mask
- `get_cropmask(...)` — ESA WorldCover cropland mask (1/NaN) on the MODIS grid.

## 0.4.0 — MODIS vegetation indices
- `get_modis(...)` / `get_ndvi(...)` — MOD13Q1 + MYD13Q1 16-day NDVI/EVI
  composites via Earth Engine, Terra+Aqua interleaved (46/year).

## 0.3.0 — Seasonal forecasts + soil gap-fill
- `get_seasonal(...)` — SEAS5 seasonal forecast / hindcast cubes.
- `extract_static_points(..., fill_nearest_m=...)` — fill masked soil pixels
  from the nearest valid pixel, with a traceability column.

## 0.2.0 — Soil & terrain
- `get_static(...)` / `get_dem(...)` / `get_soil(...)` and
  `extract_static_points(...)` — SoilGrids 2.0 soil properties and Copernicus
  GLO-30 elevation + terrain derivatives (slope/aspect/TPI/TRI).

## 0.1.0 — Climate layer
- `get_climate(...)`, `extract_points(...)`, `extract_growing_season(...)` —
  harmonized CHIRPS + AgERA5 cubes and point/growing-season extraction, with
  the shared cache, the `agwise-data` CLI and the R wrapper.
