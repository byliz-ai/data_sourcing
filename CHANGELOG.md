# Changelog

All notable changes to `agwise-data`. Versions follow the `version` field in
`pyproject.toml`. Newest first.

## 0.11.1 — CHIRPS resilient to the UCSB host block
- **Fix:** `get_climate`/`get_season`/`extract_*`/`to_*` for `PRCP` no longer
  hard-fail when `data.chc.ucsb.edu` returns HTTP 403 (the UCSB host is
  currently blocking both the yearly NetCDF and the daily COGs). The CHIRPS
  driver now falls through **COG → Earth Engine mirror (`UCSB-CHG/CHIRPS/DAILY`)
  → NetCDF**: for a country/AOI-scale window it pulls CHIRPS daily from Earth
  Engine (needs GEE credentials + `AGWISE_GEE_PROJECT`, same as MODIS) and
  assembles the same harmonized mm/day cube. Quieted the expected GDAL 403
  INFO noise from the paced COG probing.

## 0.11.0 — ORYZA v3 crop-model inputs
- `to_oryza(...)` — write ORYZA v3 weather + soil files per point. Weather is
  the CABO format, one file per calendar year (`EXTE<n>/<code><n>.<yyy>`;
  `station, year, day, srad[kJ], tmin, tmax, vapr[kPa], wind, rain`; vapour
  pressure via FAO-56; missing `-99`). Soil is the 8-layer PADDY `.sol`
  (`soil_<n>.sol`): SoilGrids' six depths remapped onto ORYZA's fixed 8 layers
  and filled with the Saxton-Rawls hydraulics (WCST/WCFC/WCWP/WCAD, KST cm/day,
  CLAYX/SANDX fractions, BD, SOC/SON kg/ha), non-puddled template with
  overridable water-balance defaults. Also a `to-oryza` CLI subcommand and an
  `ad_to_oryza` R wrapper.

## 0.10.0 — WOFOST crop-model inputs
- `to_wofost(...)` — write WOFOST weather + soil-parameter CSVs per point
  (`EXTE<n>/weather_<n>.csv` with `date, srad, tmin, tmax, vapr, wind, prec`;
  `EXTE<n>/soil_<n>.csv` with the top-metre `SMW`/`SMFCF`/`SM0`/`K0` from the
  Saxton–Rawls hydraulics plus the WOFOST soil defaults). Sources relative
  humidity + wind on top of the crop-model four. Also a `to-wofost` CLI
  subcommand and an `ad_to_wofost` R wrapper.
- **Fix:** the weather vapour pressure is emitted in the physically-correct
  kPa. The legacy `5a_prepare_list_weather.r` multiplied `plantecophys::esat`
  (which returns **Pa**) by 1000, making `vapr` ~10⁶× too large; the correct
  actual vapour pressure is `(RH/100)·esat/1000`.

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
