# Changelog

All notable changes to `agwise-data`. Versions follow the `version` field in
`pyproject.toml`. Newest first.

## 0.14.0 — iSDA as a selectable soil source
- **New:** soil can now be served from **iSDA Africa** as well as SoilGrids —
  choose per call with `source="isda"` (SoilGrids stays the default). New
  `catalog/isda.yaml` + `drivers/isda.py`. iSDA maps `SOIL.CLAY/SAND/SILT/PH/
  SOC/CEC/BDOD` to its `clay.tot.psa/sand.tot.psa/silt.tot.psa/ph.h2o/oc/ecec.f/
  db.od` layers at its two depths (`0-20cm`, `20-50cm`).
  - Units verified by inspection: texture/pH/OC/CEC are physical; **bulk
    density is stored ×100**, so `SOIL.BDOD` carries `conversion: d100`
    (121.7 → 1.217 g/cm³). Nitrogen is omitted — the `n.tot.ncs` values were
    implausible, so mapping it was left out rather than guessed.
  - iSDA is served **only from the local tree** (`AGWISE_LOCAL_ROOT`,
    `Soil/iSDA/isda_{var}_{depth}_v0.13_30s.tif`); with no local root it raises a
    clear error instead of attempting a download.
  - Note: iSDA's two depths differ from SoilGrids' six, so the crop-model
    writers (which expect the SoilGrids depth set) still use SoilGrids.
  - Verified live on Rwanda points against SoilGrids side by side.

## 0.13.0 — Local source extended to soil (SoilGrids) and MODIS
- The `AGWISE_LOCAL_ROOT` local source (0.12.0) now also covers the **soil**
  (`StaticDriver`) and **MODIS** (`ModisDriver`) code paths, not just the daily
  climate drivers.
  - **Soil:** `fetch_local_static` reads the SoilGrids depth rasters in
    `Soil/soilGrids/profile/{var}_{depth}_mean_30s.tif`, windowed to the region
    and stacked into the depth cube. These legacy tifs are already in physical
    units, so the `local` block is marked `preconverted: true` and the catalog
    conversion is skipped. Only the properties present locally are served; the
    rest fall back to the WCS. Verified live on Rwanda points (CLAY/SAND/SILT/
    SOC/PH/BDOD correct; urban points reflect the legacy tifs' per-property
    nodata gaps).
  - **MODIS:** `fetch_local_composite` assembles a composite year from
    per-composite GeoTIFFs at a **domain-tagged** `composite_path`
    (`modis/{domain}/{short}_{year}_{doy}.tif`) — the domain tag prevents ever
    serving one region's tiles for another. Legacy `Landing/MODISdata` files are
    region-baked to a single AOI/year and are *not* auto-matched; stage cubes in
    this layout to reuse them (MODIS otherwise stays on Earth Engine).
- New network-free tests for both paths.

## 0.12.0 — Local source: reuse already-downloaded geodata (no re-download)
- **New:** point `AGWISE_LOCAL_ROOT` at the AgWise `Global_GeoData/Landing`
  tree and the daily drivers read the matching legacy yearly file
  (`<Variable>/<Source>/<year>.nc`) — region-clipped — instead of downloading.
  It then flows through the normal harmonize + cache path, so the cached file
  is identical to one built from the network source. Added a `local` access
  block to the CHIRPS and AgERA5 catalog entries, a `drivers/local.py`
  (`fetch_local_year`), and a `local_root` config field. Opt-in: with the env
  var unset (default), nothing changes and drivers download as before.
  - Handles the legacy files' quirks: data variable named after the year, an
    extra `crs` variable, out-of-order time axis, and global extent (clipped
    before load to stay in memory). Legacy AgERA5 carries the same raw units as
    CDS (K, J m-2 day-1, %, m s-1), so the existing per-variable `conversion`
    applies unchanged.
  - Verified live: Rwanda 2020 RHUM/TMAX/SRAD read from the local tree in ~5 s
    (vs ~20 min from CDS), with K->degC and J->MJ conversions correct.

## 0.11.3 — Fix product cache-hit reopen for country-clipped data
- **Fix:** the second request for a **country-clipped** product failed with
  `ValueError: ... more than one data variable`. A geometry clip adds a
  `spatial_ref` CRS variable (rioxarray), so the cached product NetCDF has two
  variables, and the cache-hit path reopened it with `xr.open_dataarray`
  (single-variable only). Added `_open_product_da()` — picks the one real data
  variable, ignoring the CRS placeholder — and used it in all five affected
  functions (`get_climate`, `get_static`, `get_seasonal`, `get_modis`,
  `get_season`). First calls always worked (they return the in-memory cube);
  only repeat calls hit the bug. Found while ingesting local `common_data`
  files into the cache.

## 0.11.2 — AgERA5 v2; document CHIRPS-needs-Earth-Engine
- **AgERA5 → version 2.** ECMWF deprecated AgERA5 v1/v1.1 (no longer updated).
  The CDS request now uses `version: "2_0"` (same request schema and file
  structure — verified live). Note: the cache key is (source, variable, year,
  domain), *not* version, so already-cached years stay v1.1 until refetched —
  clear the AgERA5 cache dir to force a v2 re-pull.
- **Docs:** the README and `docs/credentials_setup.md` now state that while the
  UCSB host is 403-blocking, fetching CHIRPS `PRCP` needs Earth Engine
  credentials (the v0.11.1 fallback). The README "first success (no
  credentials)" fetch switched from CHIRPS to Copernicus **DEM elevation**,
  which needs no account regardless of the UCSB block.

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
