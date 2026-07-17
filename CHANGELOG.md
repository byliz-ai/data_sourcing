# Changelog

All notable changes to `agwise-data`. Versions follow the `version` field in
`pyproject.toml`. Newest first.

## 0.18.0 — CGLabs data folders are the built-in defaults (zero-config reuse)
- **A new user on CGLabs now reuses the already-downloaded data with no setup.**
  `Config.load()` defaults the download cache to the shared
  `…/Global_GeoData/Processed` and the reusable raw inputs to
  `…/Global_GeoData/Landing` whenever that tree exists — so `AGWISE_DATA_ROOT`
  and `AGWISE_LOCAL_ROOT` become *optional overrides*, not required setup.
  Off CGLabs (tree absent) it falls back to `~/agwise_data` with local reuse
  off, exactly as before. New `config.default_data_root()`/`default_local_root()`
  + `CGLABS_LANDING`/`CGLABS_PROCESSED` constants (edit those to relocate the
  whole team).
- **NFS file locking handled automatically:** `HDF5_USE_FILE_LOCKING=FALSE` is
  now set by default (the shared folders are on NFS), removing a step a new user
  had to know. A value you set yourself is left untouched.
- Verified live as a brand-new user (nothing exported): soil at 10 Kenya points
  came from `Landing` in **7 s** and CHIRPS rainfall in **6 s** (`Local source
  hit`), versus timing out while downloading before. +5 tests (`test_config.py`).
- Docs updated (README §2.2, cglabs_setup §2): on CGLabs there is nothing to
  export; env vars are for relocating only.

## 0.17.0 — upload your own area of interest (`geometry=` / `--aoi` / `aoi=`)
- **New region selector:** every gridded call (`get_climate`, `get_static` and
  its `get_dem`/`get_soil`/`get_cropmask` wrappers, `get_seasonal`, `get_modis`/
  `get_ndvi`, `get_season`, `smooth_ndvi`), plus `make_grid`, `bias_correct` and
  `forecast_to_dssat`, now accepts **`geometry=`** — a user-uploaded area of
  interest. Accepts a **file path** (shapefile, GeoJSON, GeoPackage — anything
  geopandas reads), a `GeoDataFrame`/`GeoSeries`, a shapely geometry, or a
  GeoJSON-like `dict`. CLI: **`--aoi <path>`**; R: **`aoi=`**.
- The AOI is reprojected to EPSG:4326 automatically (any input CRS), all
  features are kept (a multi-district / MultiPolygon selection clips as one
  AOI), and it takes priority over `country`/`bbox`. Products are cached under a
  stable, collision-free tag derived from the geometry (`aoi_<stem>_<hash>`), so
  two different shapes never share a cache entry.
- New `boundaries.load_aoi()` + `boundaries.aoi_tag()`; `_resolve_region` gained
  a `geometry` argument. Uses the geopandas/shapely stack the boundary loader
  already depends on — no new dependency. +7 tests. Verified live: an uploaded
  GeoJSON polygon clipped real Copernicus DEM (no credentials) to the exact
  polygon bounds.
- Docs updated: area-selection is now country / admin unit / bbox / **uploaded
  zone** / points (README, user guide §4.1 with Python/R/CLI examples, REFERENCE
  §6 `geometry` parameter on all region functions).

## 0.16.0 — smooth_ndvi (gap-fill + Savitzky-Golay of the MODIS NDVI stack)
- **New:** `smooth_ndvi(years, country=|bbox=, ...)` — turns the raw MODIS NDVI
  composites (with cloud/QA gaps left as NaN by the drivers) into the
  analysis-ready, smoothed time series the planting-date phenology workflow
  needs; the port of the legacy `get_MODISts_PreProc.R`. Per pixel the gaps are
  filled and a Savitzky-Golay filter (`window=9`, `polyorder=3` — the MODIS
  choice) is run along time; pixels with no valid observation stay NaN. Returns
  `{"RS.NDVI": {...}}` like `get_modis`, writing a `Smoothed_NDVI_*_SG` product.
- **Gap-fill method** is selectable via `gapfill=`:
  - `"linear"` (**default**) — linear interpolation along the *time*
    coordinate, edges carried from the nearest valid step. Tracks a peaked
    seasonal signal far better than the legacy mean (verified: RMSD-to-truth
    ~0.036 vs ~0.080 on a noisy season with ~20% gaps) and matches the MODIS
    driver's stated intent ("the downstream smoothing interpolates the gaps").
  - `"mean"` — per-pixel temporal mean, reproducing the legacy
    `substituteNA(type="mean")` for exact parity.
- **Optional cropland masking** (`cropmask=True`, default): the ESA WorldCover
  mask (`get_cropmask`) is aligned to the NDVI grid by nearest neighbour and
  non-cropland pixels are set to NaN before smoothing. `cropmask_source=`
  overrides the source; `cropmask=False` smooths every pixel.
- Non-default `window`/`polyorder` and a `"mean"` gap-fill are appended to the
  product name, so they never collide with a default-smoothed cache entry.
- New module `agwise_data.smoothing` (`smooth_stack`, `savgol_gapfill`,
  `apply_cropmask`); CLI `smooth-ndvi` and R `ad_smooth_ndvi`. +10 tests.
- Note (perf): the `"linear"` fill loops over pixels with gaps — fine at
  county/region scale; vectorize before continental-scale runs.

## 0.15.0 — soil hydraulics at points + Mehlich-3→Olsen P (DSSAT P block)
- **New:** `extract_static_points(..., derive=...)` adds pedotransfer columns,
  pulling in the base variables each needs:
  - `derive="hydraulics"` — Saxton & Rawls (2006) from CLAY/SAND/SOC, per
    depth: `PWP_<d>`, `FC_<d>`, `SAT_<d>` (cm³/cm³) and `KS_<d>` (mm/h). Same
    equations the crop-model writers already use (`writers/soil.saxton_rawls`),
    now exposed at points without writing a model file.
  - `derive="olsen_p"` — `OLSENP_<d>` (mg/kg) from Mehlich-3 extractable P via
    the new `mehlich3_to_olsen()` (`0.47·M3 + 2.4`; `calcareous=True` →
    `0.41·M3 + 1.1`; Steinfurth et al. 2023).
- **New:** `SOIL.EXTP` — Mehlich-3 extractable phosphorus (mg/kg), served from
  **iSDA** (`source="isda"`, layer `p`, depths `0-20cm`/`20-50cm`; Landing
  rasters are physical mg/kg, verified by sampling). SoilGrids has no P.
- **New:** the DSSAT `.SOL` writer now fills the **second-tier P block**
  (`SLPX` = Olsen P) when phosphorus is available — pass `write_sol(...,
  olsen_p=[...])` per layer, or let `to_dssat`/`write_sol` derive it from
  Mehlich-3 `EXTP_<depth>` columns on the soil frame. The extractable-P source
  has coarser depths than the six-layer profile, so each profile layer takes
  the `EXTP` value whose interval contains the layer midpoint (nearest by
  centre otherwise) — a transparent piecewise-constant depth mapping. With no
  P data the block is omitted (unchanged output).
  - The provisional 0-30 cm→multi-depth exponential P extrapolation in the
    legacy `get_geoSpatialData_V2_phosphorus.R` (flagged `# TODO: Revise`
    there) was **not** ported; the depth mapping above is used instead.
- CLI `extract-static` gains `--derive` / `--calcareous`; the R
  `ad_extract_static_points` gains `derive` / `calcareous`.

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
