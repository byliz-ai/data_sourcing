# HANDOFF — continue here

Session state for `agwise-data` (repo: `byliz-ai/data_sourcing`). Read this
first; it is written so the next session does not have to re-derive anything.
Last updated: 2026-07-14 (**scope-map #1 + #2 + P1 spatial scaffolding DONE,
v0.8.0**): `get_season` (season-sliced climate/NDVI, cross-year aware),
crop-model input writers `to_dssat`/`to_apsim` (DSSAT `.WTH`+`.SOL`, APSIM
`.met`+soil table), and `make_grid`/`tag_admin` (AOI grid + field↔geo admin
linking) BUILT + verified (writers round-tripped through the DSSAT/apsimx R
readers; grid/admin live-verified on real Rwanda). Also fixed a latent AgERA5
driver bug (missing `cache` import) that broke every fresh AgERA5 download.
Prior: 2026-07-07 (GEE unblocked `moodle-sites-440814`; MODIS + crop-mask
live-verified, v0.5.0).

## ⚠️ GROUND RULES ON CGLABS — read before touching anything

**Never modify or delete any original/existing file on CGLabs.** The
downloaded datasets, the existing scripts, the shared `common_data`, and
anything already on disk are **READ-ONLY inputs**. Treat them as immutable.

- **Only create or modify files inside a NEW dedicated folder** created for
  testing these flows (e.g. `~/agwise_data_test/` or a fresh working dir).
  All test outputs, clones, scratch files and edits live there and nowhere
  else.
- Point **`AGWISE_DATA_ROOT` at a NEW test folder**, NOT at the shared
  `common_data` that holds the original downloads — otherwise the cache
  would write into the originals. e.g.
  `export AGWISE_DATA_ROOT=~/agwise_data_test/cache`.
- Read the original data with read-only access; copy anything you need into
  the test folder rather than editing in place.
- If a task seems to require changing an original file, STOP and ask Lizeth
  first.

## The one-line scope

Build the **data-sourcing layer that generates analysis-ready INPUTS** for
the AgWise modules — and **stop there**. Do NOT build downstream analysis
(phenology methods, module ML, cross-validation). Deliverable = the initial
data each module consumes, nothing past it.

## Decisions recorded 2026-07-04 (from Lizeth)

1. **Seasons DO cross the calendar year** (e.g. Rwanda season B, Sep→Feb).
   Consequence: `script1` must label bands by real date instead of
   day-of-year — **FIXED 2026-07-04** on the copies in
   `~/agwise_data_test/sentinel_scripts/`: time axis is now days-since-
   season-start (monotonic across New Year), bands are named
   `EVI_20200914_SG` (real date), meta CSV has `date` + `doy` columns,
   checkpoint plots use real datetimes, and `parse_band_name` in the utils
   accepts both the legacy `DOY` and the new date labels. Verified with a
   synthetic Sep 2020→Feb 2021 season (guard still trips on the old DOY
   axis; offsets axis smooths correctly across the boundary).
   **Source of truth for these scripts is now `sentinel/` in THIS repo**
   (Lizeth 2026-07-04) — the modules run from git; OneDrive copies are
   historical.
2. **SoilGrids urban/water NaN → fill from the nearest valid pixel** —
   IMPLEMENTED (see below): bounded search radius (`fill_nearest_m`,
   default 1 km), traceability column `<VAR>_fill_m` per variable
   (0 = own pixel valid, >0 = donor distance in m, NaN = nothing valid in
   range so the value stays NaN).

## Immediate next step

**DONE 2026-07-14: scope-map #1 (crop-model geo-input writer) and #2
(`get_season`)** — see the "Crop-model inputs" section below. These retire
the most duplicated module code (`readGeo_CM_zone`). v0.7.0, 101 network-free
tests pass (was 71).

**NEXT: scope-map #3 (seasonal-forecast bias correction)** — both halves are
already here (`get_seasonal` + `get_climate`); add the hindcast-vs-obs
correction + point-sampling to DSSAT inputs (`Forecast/03_bias_correction_
forecast_multiVar.R`, `04_prepare_dssat_geo_inputs.R`). Then #4 (MODIS SG-
smoothing/gap-fill — the Sentinel SG code in `sentinel/script1` extended to
the MODIS stack) and #5 (RothC monthly-climate inputs incl. a PET layer).

1. ~~**Unblock GEE project access.**~~ **DONE 2026-07-07.** Lizeth's own
   working Cloud project is `moodle-sites-440814` (**an example — every
   teammate uses their OWN project, see docs/credentials_setup.md; nobody
   should try to use this ID**). `ee.Initialize(project="<your-project-id>",
   opt_url="https://earthengine-highvolume.googleapis.com")` succeeds with a
   valid `~/.config/earthengine/credentials` token and reads MOD13Q1. The
   earlier failures were just wrong names — a prefixed guess like
   `ee-moodle-sites` does not exist; use the project ID exactly as the Cloud
   console shows it (it need not start with `ee-`). Set it via
   `export AGWISE_GEE_PROJECT=<your-project-id>` (or `gee_project:` in
   `~/.config/agwise_data.yaml`).
2. ~~**Live-verify the MODIS driver.**~~ **DONE 2026-07-07.** Ran
   `get_ndvi(years=2021, country="Rwanda", out_format=["nc","tif"])` on
   CGLabs → **PASSED** in 62 s (Terra cache-cold + Aqua cache-cold):
   - **46 composites**, `time` 2021-01-01 … 2021-12-27, every gap = 8 days.
   - **23 Terra-grid + 23 Aqua-grid** dates interleaved (DOY 1,9,17,25,…).
   - NDVI range **[-0.200, 0.999]** (inside [-0.2, 1]); no fabricated fill.
   - **Lake Kivu (-2.05, 29.2): 0/46 valid** — permanent water fully
     QA-masked to NaN, exactly as intended (SG smoothing fills gaps
     downstream). Kigali land pixel: 32/46 valid, mean NDVI 0.298.
   - GeoTIFF: 46 bands labelled `2021_01_01` … `2021_12_27` (year-based
     layer selection in the phenology preproc keeps working).
   Product written to `~/agwise_data_test/cache/products/RWA/
   Composite_NDVI_2021_2021.{nc,tif}` (test root per Ground Rules).
3. ~~**Crop-mask layer** (ESA WorldCover via GEE, static).~~ **DONE
   2026-07-07** — see the "Crop-mask layer" section below. Built,
   live-verified on CGLabs, and on `origin/main` (v0.5.0).
4. Housekeeping: rotate the leaked CDS key (see Backlog); rotate the
   GitHub PAT when convenient (pasted in chat 2026-07-04, now in
   `~/.git-credentials` chmod 600); NEW — rotate the **EOSDIS Earthdata
   password hardcoded in the legacy `get_MODISdata.R`** (see Backlog).

(Push backlog cleared 2026-07-04: everything through the credentials doc
is on `origin/main`; CI runs on push.)

(SEAS5 live verification: **DONE 2026-07-04** — real CDS smoke test
passed: PRCP i02/1995, Rwanda bbox → 25 members, 215 valid days starting
1995-02-02, mean 3.0 mm/day, max 36.7, no negatives, no NaNs. CDS creds
now in `~/.cdsapirc` on this machine.)

## Deliver analysis-ready, not raw — module data-processing to absorb (scope map, 2026-07-07)

Direction from Lizeth (2026-07-07): the data module should hand each AgWise
module inputs **ready to start its internal process** — no extra step for
the user (no slicing a season, reformatting, or re-aggregating). And it must
**maximize resources so download + processing time is minimal**.

Audited every module repo for the data-*processing* they still do by hand
(vs. their core method, which stays in the module). Finding: **every
crop-model module re-implements the same data-prep scaffolding**, copied per
use case — hundreds of near-identical `get_CM_geo_*` / `readGeo_CM_zone*` /
`01_GetSoilandWeather` scripts (fertilizer 113, potentialyield ~200 use-case
copies, cropping-innovation, planting-date). Removing that duplication is the
point of this layer.

**Boundary rule.** IN scope = anything that turns sourced data into the
analysis-ready INPUT a module consumes: harmonize, aggregate, extract to
points, mask, smooth, bias-correct, format to the engine's input files.
OUT of scope = the module's method: DSSAT/APSIM/Oryza/WOFOST simulation,
phenology/crop-type detection, RothC simulation, ML response functions,
ONI/optimum-sowing summaries, plots.

**Already delivered ready-to-use (done):**
- Harmonized daily/monthly climate cubes — `get_climate` (replaces per-module
  CHIRPS/AgERA5 download+stack+monthly aggregation).
- Trial/point growing-season climate — `extract_growing_season`
  (`Precipitation_m1..mN`, `totalRF`, `nrRainyDays`): the fertilizer ML input.
- Soil/terrain at points w/ nearest-valid fill — `extract_static_points`.
- SEAS5 hindcast/forecast cubes — `get_seasonal` (raw forecast half).
- MODIS NDVI/EVI composites (46/yr) — `get_ndvi`; crop mask — `get_cropmask`.

**Coverage check (does the layer let a module start with NO extra step?).**
For the **DSSAT & APSIM crop-model modules** (the largest duplication —
`readGeo_CM`/`get_CM_geo` appears **139×** in potentialyield alone, 113 in
fertilizer): YES — `get_climate`/`extract_growing_season`/
`extract_static_points`/`get_season`/`to_dssat`/`to_apsim` now produce the
season-sliced weather+soil files directly. AOI-grid/field linking is now done too
(`make_grid`/`tag_admin`, v0.8.0). Remaining gaps to full "no-extra-step"
coverage are queued below (Oryza/WOFOST writers, the DSSAT P block, bias
correction, NDVI smoothing, RothC inputs, PET, extra covariates).

**Audit basis (2026-07-14).** Inventoried the data-*processing* steps across
every module repo (fertilizer, fertilizerrequirement, potentialyield,
planting-date-and-cultivar, cropping-innovation, soilhealth,
organic-fertilizer, responsefunctions, datasourcing, datacuration,
farm-bundled-advisories) and cross-checked against the **latest** module
script versions Lizeth keeps in `~/agwise_data_test/{fertilizer scrips,
planting date, sentinel_scripts}` (authoritative — newer than the module-repo
copies; the fertilizer `2.get_geoSpatialClimate_soilTopo 3.R` is a bug-fixed
consolidation; sentinel `script1` is functionally identical to the repo copy,
only example creds differ). Empty repos (nothing to absorb): organic-
fertilizer, fertilizerrequirement, farm-bundled-advisories.

**Still done by the modules → candidates to absorb, prioritized by
duplication × #modules × leverage.**

*P1 — highest leverage (foundational or extreme duplication):*
- ✅ **Crop-model geo-input assembly (DSSAT/APSIM)** — DONE 2026-07-14
  (`to_dssat`/`to_apsim`, v0.7.0), see "Crop-model inputs" below.
- ✅ **Season-ready delivery** — DONE 2026-07-14 (`get_season`, v0.6.0).
- ✅ **AOI point-grid generation + field↔geospatial admin linking** — DONE
  2026-07-14 (`make_grid`/`tag_admin`, v0.8.0), see "Spatial scaffolding"
  below. Was the single biggest duplication (~105 `get_geoSpatialData*` /
  `get_GridCoordinates` copies).
- **Oryza + WOFOST input writers** — extend the `to_dssat`/`to_apsim`
  pattern: `to_oryza` (potentialyield `generic/Oryza/OryzaDataFiles.R` +
  `SoilGrids.R`) and `to_wofost` (potentialyield `generic/WOFOST/grid/
  5a–5d_prepare_list_{weather,crop,soil,control}.r`; season-slice + rh→vapr
  unit conversion; ~26 duplicated files). Same ingredients we already produce.
- **Soil hydraulic PTFs + Mehlich-3→Olsen P, exposed as soil enrichment** —
  the Saxton-Rawls hydraulics now live in `writers/soil.py` (used by
  to_dssat/to_apsim); expose them (and the DSSAT P block) as an
  `extract_static_points(..., derive="hydraulics")` option. Mehlich-3→Olsen:
  `datasourcing/Scripts/generic/get_geoSpatialData_V2_phosphorus.R`
  (`olsen = 0.47*M3 + 2.4`) — fills the P-block gap noted in "Crop-model
  inputs / Not included yet".

*P2 — clear per-module wins:*
- **MODIS NDVI SG-smoothing + gap-fill** — `planting date/get_MODISts_
  PreProc.R` (LATEST): reads the 46/yr stack (we deliver via `get_ndvi`),
  masks ESA cropland class-40 resampled to the NDVI grid (we deliver
  `get_cropmask` already on that grid → straight multiply), then **NA→mean
  gap-fill + Savitzky-Golay `sgolayfilt(p=3, n=9)`** per pixel. Port to a
  `smooth_ndvi()` on the MODIS stack (SG code exists in `sentinel/script1`).
  Add VIIRS as an alt source (`get_MODISData_VIIRS.R`).
- **Seasonal-forecast bias correction** — planting-date `Forecast/
  03_bias_correction_forecast_multiVar.R`: hindcast-vs-obs → bias-adjusted
  fields; both halves here (`get_seasonal`+`get_climate`); add the correction
  + point-sampling to DSSAT inputs (`04_prepare_dssat_geo_inputs.R`).
- **RothC inputs (soilhealth pipeline)** — beyond monthly climate (delivered):
  `calculate_socStock.R` (SOC stock 0–30 cm from OC/BDOD/CFVO + AfSIS),
  `calculate_NPP.R` (Miami-model NPP from monthly climate), `download_PET_
  function.R` (**PET** layer), `generateTargetPoints.R` (cropland target-point
  grid), and `generate_SpinupWarmpForward_Input.R` (RothC input-table
  assembly → a `to_rothc` writer). Historical monthly climatology
  (`generate_historicalMean.R`, multi-year monthly means) also here.
- **Extra covariates at points** — `extract_SocClayNDVI.R` needs
  **NDVI-at-points**; responsefunctions repeatedly does **AEZ-at-points**
  (`raster::extract(RW_aez, pts)`) and QUEFT_ML builds a **WorldClim/
  elevation covariate stack for a prediction grid** (`utils_covariates.R`,
  `prepare_PredictionGrid.R`). Add NDVI/AEZ layers + `extract_*_points` on a
  grid; consider WorldClim as a source.

*P3 — lower duplication / adjacent:*
- **Soil-moisture layer** — Copernicus SM cube → points
  (`get_geoSpatialData_V3_with_soil_moisture.R`). A new source + `extract`.
- **Weather QC/merge/splice + rainfall-source comparison** —
  `cleaning_tmin_tmax_DSSAT_files.R`, `merging_weather_data_time_series.R`,
  `download_wth.R`, and CHIRPS-vs-AgERA5 `rainfall_dataComparison_*.R`: a QC/
  splice step before the writers.
- **Analysis-ready cube builders** (fertilizer `skills/build_{climate,soil}_
  cube.py`) — a Python re-implementation of what `get_climate`/`get_static`
  already deliver; consolidate onto this layer rather than the ad-hoc "skills".

**Separate workstream — AGRONOMIC / field-trial data (NOT this environmental
data layer; flag for a parallel effort).** Heavy duplication but different
domain: trial-yield QC + BLUP noise reduction (responsefunctions, ~6 copies),
validation-survey compilation/QC (~7 copies), ONA/SAnDMan trial download+
compile, field-trial observation harmonization (`load_trial_data`,
`prep_inputs.R`, `useCase_Africa_Maize/{1_aggregate,2_format,3_format_date}`),
ground-truth crop-type compilation for RS (datacuration CMRS), and the
**carob/`carobiner` curation engine** (datacuration, ~380 dataset scripts +
5 `_functions.R` libs — the reusable asset is the framework, not the one-offs).

**Out of scope (stays in the modules — the method):** `run_DSSAT_*` /
`dssat_exec*` / DSSAT X-file & APSIM `.apsimx` experiment/factorial assembly /
APSIM `03_RunSim` / Oryza & WOFOST *runs*, `get_RS_Phenology` /
`get_phenology` / `get_RStoCM_Phenology` / crop-type detection, RothC
spin/warm/forward *runs*, ML fitting (GBM/RF) & QUEFTS solvers,
`dssat_summary_ONI*`, optimum-sowing, lime/recommendation logic, all plots/EDA.

## Performance / resource use — minimize download + processing time

Already in place (keep leaning on these):
- **Region-scoped caches** (`rg_*`): a country request fetches only its
  window, not a continent (~0.03% of a global CHIRPS file for Rwanda).
- **Download once, shared**: append-only per-year cache on the shared root;
  the 2nd request (anyone) is a cache hit; cached years are never refetched.
- **Parallel prefetch** across (variable, year) — `AGWISE_DATA_WORKERS`;
  segmented HTTP range downloads (`download_parts`); per-composite GEE
  parallelism (`cog_workers`); aligned NetCDF chunking.

Next perf work (priority order):
1. **CHIRPS windowed COG path is the minimal-download route — make sure it is
   actually used.** `chirps.yaml` has a `https-cog` alternative (daily COGs,
   range-request reads of just the country window) that the driver prefers for
   small regions; the global yearly NetCDF is a ~1.1 GB/year fallback. Two
   real problems found 2026-07-14: (a) the **global NetCDF URL now 403s**
   (`.../global_daily/netcdf/p05/chirps-v2.0.{year}.days_p05.nc`) — the
   fallback is effectively dead, so if the COG path fails there is no weather
   rainfall; (b) on a **broken-GDAL/rasterio env** the COG path throws and
   falls back to that dead NetCDF (this is what killed the full `to_dssat`
   e2e here — AgERA5 downloaded fine after the cache-import fix, then CHIRPS
   403'd). Actions: verify the COG path works on CGLabs (healthy GDAL); switch
   the NetCDF fallback to the working `by_month` URL or GEE; make a broken-COG
   env fail loudly instead of silently downloading a global file.
2. **Warm the cache** for the common use-case countries/years once
   (`warm_cache(country, years, vars)`) so module runs are all cache hits.
3. **Parallelize across sources** (climate + soil + NDVI concurrently for one
   request), not just across (var, year). The pieces already run per-source;
   fan them out together for a single `to_dssat`/`get_season` call.
4. **MODIS/GEE**: crop-mask `reduceResolution` cold-fetch ≈150 s (cached
   once/country, so amortized) — batch composite pulls and cache the EE
   listing to cut MODIS cold time.
5. Deliver the **season slice / crop-model files server-side** so modules
   neither re-download whole years nor re-extract points (ties to #1–#2 above).
6. **Reuse extractions across writers**: `to_dssat`/`to_apsim` already accept
   `weather=`/`soil=` — a module wanting both engines (or DSSAT+Oryza+WOFOST)
   should extract once and pass the frames to each writer, not re-fetch.
7. **Windowed/point-native soil**: for a handful of trial points prefer the
   SoilGrids point/WCS path over caching a whole regional soil cube (the
   Oryza `SoilGrids.R` REST-per-point approach) when the point count is small.

## Spatial scaffolding (scope-map P1, BUILT + VERIFIED 2026-07-14, v0.8.0)

The AOI grid + field↔geospatial admin linker every module re-implements
(~105 copies). Thin wrappers over the cached geoBoundaries; no new source.

- **`make_grid`** (`api`, CLI `make-grid`, R `ad_make_grid`) — regular
  ~`res_km` point grid (default 5 km; 1.0/0.25 for AOIs) clipped to a
  country/admin boundary (or a bbox), each point tagged `country` +
  `NAME_1`/`NAME_2` up to `tag_admin_level`. Replaces
  `get_GridCoordinates.R`/`getCoordinates()`.
- **`tag_admin`** (`api`, CLI `tag-admin`, R `ad_tag_admin`) — assign
  `country`/`NAME_1`/`NAME_2` to arbitrary points via point-in-polygon
  (geoBoundaries per level). The reusable half of the modules'
  `extract_geoSpatialPointData` field↔geo link.
- Helpers in `api.py`: `_grid_points` (cos-lat degree spacing),
  `_admin_names_for_points` (per-level `gpd.sjoin` within; missing level →
  all-None column + warning, never fails the call).
- **8 tests** (`tests/test_grid.py`, network-free via synthetic polygons).
  **Live-verified**: `make_grid("Rwanda", res_km=10)` → 238 points, all
  tagged (5 provinces incl. City of Kigali + districts); `tag_admin` correct
  (Kigali→City of Kigali/Nyarugenge, etc.). 108 network-free tests pass.
- Note: geoBoundaries has no built-in NAME_1↔NAME_2 hierarchy, so each level
  is an independent point-in-polygon lookup (both correct for the point).

## Crop-model inputs (scope-map #1 + #2, BUILT + VERIFIED 2026-07-14, v0.7.0)

The "last mile" so modules start from analysis-ready inputs instead of
re-running `readGeo_CM_zone`. Full docs: `docs/crop_model_inputs.md`. Adds no
new data source — it is season-slicing + the engine file writers on top of
`get_climate`/`extract_points`/`extract_static_points`/`get_modis`.

**`get_season`** (`api.get_season`, CLI `get-season`, R `ad_get_season`) —
climate + NDVI sliced to `[planting_date, harvest_date]`, **cross-year aware**
(Sep→Feb is just `slice(pl, hv)` on the continuous axis). Two modes: region
(cube per var → `Season_<SHORT>_<pl>_<hv>` product) and points (long df;
`planting_col`/`harvest_col` give each trial its own season). Mixes AGRO.* and
RS.* vars in one call (auto-routed). **NOT** `get_seasonal` (that is SEAS5
forecasts) — names are close, meanings differ; documented in both docstrings.
Live-verified: cross-year Rwanda NDVI from the cached `Composite_NDVI_2020_
2021.nc` → 21 composites in-window (from 92), both years, plausible range.

**`to_dssat` / `to_apsim`** (`api`, CLI `to-dssat`/`to-apsim`, R
`ad_to_dssat`/`ad_to_apsim`) — per point `n`, write under `out_dir/EXTE<n>/`:
DSSAT `WHTE<n>.WTH` + `SOIL.SOL`; APSIM `wth_loc_<n>.met` + `soil_<n>.csv`.
Accept `weather=`/`soil=` to reuse already-extracted frames (skips the fetch;
this is also how they are unit-tested network-free).

**Writers live in `src/agwise_data/writers/`**:
- `_common.py` — `prepare_weather` (PRCP→RAIN alias, TMIN≤TMAX swap, NaN drop)
  and `tav_amp` (TAV = mean daily-mean temp; AMP = ½ the month-to-month
  range), shared by both engines. `station_code` → 4-char INSI/site code.
- `dssat.py` — `write_wth`: fixed-width `.WTH` matching `DSSAT::write_wth`
  (`$WEATHER:`, `@ INSI…` GENERAL block, `@ DATE…` with `YYYYDDD` dates).
- `apsim.py` — `write_met`: `year day radn maxt mint rain` + `tav`/`amp`
  header, whole numbers bare (matches `apsimx::write_apsim_met`).
- `soil.py` — the science: `saxton_rawls` (PWP/FC/SAT/KS, exact port of
  `get_geoSpatialData_V2.R`), `texture_class` triangle + `texture_props`
  (albedo/CN2), `slu1`, `root_growth_factor`; `write_sol` (DSSAT layered
  profile) and `apsim_soil_table` (LL15/DUL/SAT/AirDry/KS/BD/Carbon/clay/
  silt/N/PH/CEC + Salb/CN2Bare attrs). **Unit mapping** (our layer → .SOL):
  SLOC=SOC(g/kg)/10, SLNI=N(g/kg)/10, SLCL=CLAY%, SLSI=SILT%, SBDM=BDOD,
  SLHW=PH, SCEC=CEC; SOM% for Saxton = SOC/5. SoilGrids units are already
  reconciled in `harmonize.STATIC_VARS`.

**Verification (all passed):** file layouts matched byte-for-byte against
reference files the R packages emit; every output round-tripped through the
packages' own readers — `DSSAT::read_wth`/`read_sol` (correct header, layers,
cross-year span, `PWP<FC<SAT` monotonic) and `apsimx::read_apsim_met` +
`check_apsim_met` (no fatal issues). End-to-end `to_dssat` on **real
SoilGrids** Rwanda points → `SOIL.SOL` that `read_sol` parses cleanly
(texture "C"/clay, monotonic hydraulics). 101 network-free tests pass
(`tests/test_season.py`, `tests/test_writers.py`).

**Not included yet:** DSSAT phosphorus block (SLPX/SLPT… — layer has no P
source; optional in DSSAT); `SLDR`/`SLNF`/`SLPF` are neutral metadata
defaults a module can override.

**Latent bug fixed:** `drivers/agera5.py` used `cache.NC_LOCK` without
`from .. import cache` — every fresh AgERA5 download raised `NameError`
(only surfaced now because a `to_dssat` weather fetch pulled an uncached
year). One-line import added; all other drivers already had it.

## Crop-mask layer (ESA WorldCover, BUILT + LIVE-VERIFIED 2026-07-07)

Roadmap item 3, second half. Reference studied:
`agwise-planting-date-and-cultivar/main/RS/get_ESACropland_fromGEE.Rmd`
(WorldCover class 40 → binary crop mask, reproject to 250 m) and the
consumer `get_MODISts_PreProc.R` §2.4 (reclassify 40→1, others→NA,
resample to the NDVI grid, multiply the composite stack).

- `drivers/worldcover.py` — `WorldCoverGeeDriver(StaticDriver)`, registered
  `worldcover_gee`. Server-side it takes `ESA/WorldCover/v200`, `.mosaic()`
  (pins the native 10 m projection with `setDefaultProjection` first),
  `.eq(40).unmask(0)`, `reduceResolution(mean)` to the **cropland
  fraction** per cell, `reproject`s onto the MODIS 1/480° grid, then
  thresholds at `crop_fraction_min` (catalog, default 0.5) → 1.0 cropland
  / NaN otherwise (pure `cropland_mask`, unit-tested). Same grid as the
  NDVI/EVI composites, so masking non-crop is a straight multiply.
- **Shared GEE machinery**: `drivers/gee.py` now holds the client init,
  request tiling (`plan_tiles`) and tiled `computePixels`
  (`fetch_image_grid`); both `modis.py` and `worldcover.py` use it
  (`modis.plan_tiles` still re-exported so nothing downstream broke).
- Catalog `esa_worldcover.yaml`; new canonical static var `LC.CROPLAND`
  (harmonize `STATIC_VARS`, `DEFAULT_STATIC_SOURCE` LC.* → esa_worldcover).
- API `get_cropmask(country/bbox, ...)` (thin over `get_static`); CLI
  `get-cropmask`; R `ad_get_cropmask` (returns a 1/NaN terra SpatRaster);
  STAC export works (`LC.CROPLAND`, unit "1").
- Version bumped to **0.5.0**. **71 network-free tests pass** (was 64).
- **Live check (CGLabs, project `moodle-sites-440814`)**: Kigali bbox →
  binary mask exactly {1.0, NaN}, 34% cropland (plausible), grid aligned
  to the NDVI stack to 1e-9 in lat & lon; `NDVI*mask` drops non-crop to
  NaN cleanly. Note: the crop-mask fetch took ~150 s (reduceResolution over
  10 m WorldCover is heavy) but it is a static, cached-once layer, so
  repeat calls are instant.

## MODIS NDVI/EVI layer (BUILT 2026-07-06, LIVE-VERIFIED on GEE 2026-07-07)

Roadmap item 3 (NDVI half). Sources studied: legacy
`agwise-planting-date-and-cultivar/main/RS/get_MODISdata.R` (modisfast/
Earthdata download, MOD13Q1.061 + MYD13Q1.061) and
`get_MODISts_PreProc.R` (the consumer: expects **46 images per civil
year**, selects layers by year in the name, SG-smooths, masks by ESA
WorldCover cropland==40).

- `drivers/modis.py` — `ModisDriver` base (`ensure_composite_year` →
  file-locked, append-only `Composite_<VAR>_<year>.nc`, refuses to cache
  an incomplete past year: 23 composites expected once the year is fully
  inside the collection's coverage) + `ModisGeeDriver` (GEE
  `ee.data.computePixels` on the high-volume endpoint, ≤2048 px tiled
  pulls via pure `plan_tiles`, per-composite fetches parallelized with
  `config.cog_workers`). Grid: 1/480° (~250 m) pixel-edge aligned to the
  domain bbox.
- **Masking never fabricates**: fill (−3000), out-of-valid-range and
  QA-rejected pixels (SummaryQA not in `keep: [0,1]`) → NaN (pure
  `mask_invalid`); the downstream SG smoothing fills gaps. The QA policy
  lives in the catalog YAML and is recorded in every manifest — changing
  it means bumping the entry `version`.
- Data model: dims `(time, lat, lon)`, `time` = composite start dates;
  new `RS.*` namespace in `harmonize.py` (`RS.NDVI`, `RS.EVI`, scaled by
  new `d10000` conversion) + `standardize_composite`.
- API `get_modis(variables, years, country/bbox, satellite="both"|"terra"
  |"aqua", ...)` + `get_ndvi(...)` — default interleaves Terra (DOY 1,
  17, ...) + Aqua (DOY 9, 25, ...) into the 46-composites-per-year series
  the phenology preproc checks for; GeoTIFF band labels are composite
  dates (`2021_01_17`) so year-based layer selection keeps working.
- CLI `get-modis`; R `ad_get_modis` (returns terra SpatRaster); catalog
  `mod13q1.yaml` + `myd13q1.yaml`; STAC export works (`RS.*` vars);
  GEE project comes from `AGWISE_GEE_PROJECT` env or `gee_project:` in
  `~/.config/agwise_data.yaml` (`Config.gee_project`).
- Version bumped to **0.4.0**. **64 network-free tests pass** (was 54).

## Seasonal (SEAS5) layer (BUILT + LIVE-VERIFIED on CDS 2026-07-04)

Implements Jemal's standardization proposal (reference scripts studied:
`CGIAR-AgWise/agwise-planting-date-and-cultivar` → `Forecast/AgWise_download.py`).
The observation/historical half of that proposal is already `get_climate`.

- `drivers/seasonal.py` — `SeasonalDriver` base (`ensure_seasonal(variable,
  init_month, year, domain)` → file-locked cached
  `Seasonal_<VAR>_i<MM>_<year>.nc`) + `Seas5Driver` (CDS
  `seasonal-original-single-levels`, ecmwf/system 51). Cache is per-year →
  append-only: adding a year never refetches the others. Full lead range
  (24..5160 h = 215 days) always fetched so lead subsets are cache hits.
- Data model: dims `(member, time, lat, lon)`; `time` is the **valid date**
  (init + lead, daily steps) — Jemal's stacking. `number` → `member`.
- `deaccumulate_leads` (pure, unit-tested): accumulated-from-step-0 fields
  (PRCP, SRAD) → per-day increments, zero baseline, negatives clipped.
- Units land on the same `AGRO.*` conventions as observations (mm/day, °C,
  MJ m⁻² day⁻¹; new `m_to_mm` conversion) so hindcast/obs pair by variable
  name for bias correction. `harmonize.standardize_seasonal` added.
- API `get_seasonal(variables, init_month, years, country/bbox, ensemble=
  "members"|"mean"|"median", ...)` → product `Seasonal_<VAR>_i<MM>_<y0>_
  <y1>[_mean].nc`; GeoTIFF only for reduced ensemble. Hindcast (25 members)
  and real-time (51) years concat with outer join (extra members NaN).
- CLI `get-seasonal`; R `ad_get_seasonal` (returns NetCDF paths — terra has
  no ensemble axis); catalog `seas5.yaml`; STAC export works (AGRO vars).
- Version bumped to **0.3.0**. **54 network-free tests pass** (was 45).

## SoilGrids nearest-pixel fill (DONE 2026-07-04)

`extract_static_points(..., fill_nearest_m=1000.0)` (CLI
`--fill-nearest-m`, R `fill_nearest_m=`; `None`/`0` disables). Points on
masked pixels get all depth columns from the **same donor pixel** (valid =
finite at all requested depths). Per-variable `<VAR>_fill_m` column as
described above. Search window is bounded by the radius (cos(lat)-scaled),
so cost is only paid for NaN points. Fake soil layer in tests now has a
3×3-pixel NoData "town" to exercise this.

## Soil + DEM layer (DONE 2026-07-03, verified on CGLabs)

See git history / docs for details. Highlights: `StaticDriver` base,
Copernicus GLO-30 DEM (windowed mosaics, slope/aspect/TPI/TRI derived from
cached elevation), SoilGrids 2.0 via ISRIC WCS (all six depths cached per
property, >2° requests chunked), `get_static`/`get_dem`/`get_soil`/
`extract_static_points`, CLI + R + STAC. Real-bbox verification on CGLabs
passed (central Rwanda: DEM 48 s cold, soil 10 s, plausible values).

Env note: fresh `agwise_data` conda env on CGLabs had broken rasterio
(`libjxl.so.0.11` missing). Fix:
`conda install -n agwise_data -c conda-forge libjxl=0.11`.

## What is already DONE (tested + pushed to main)

- **Climate layer**: CHIRPS + AgERA5 drivers → harmonized `AGRO.*` cubes.
  `get_climate` / `extract_points` / `extract_growing_season` (legacy
  fertilizer columns `Precipitation_m1..mN`, `totalRF`, `nrRainyDays`).
  CLI `agwise-data`, R wrapper `r/agwise_data.R`, STAC export, manifests.
- **Performance**: region-scoped caches (`rg_*` domains), parallel
  prefetch, segmented downloads, aligned chunking. Env knobs
  `AGWISE_DATA_WORKERS`, `AGWISE_DATA_SCOPE`. Verified end-to-end on Rwanda.
- **Robustness**: CHIRPS 403 → yearly-NetCDF fallback; drivers refuse to
  cache incomplete past years.

## Sentinel phenology scripts (IN REPO: `sentinel/` — source of truth)

`sentinel/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py` (+
README). For Lizeth's scope, **script1 IS the input generator**; scripts
1b/2/3/4 are out of scope and stay in OneDrive. Already fixed: parallel
composite downloads, TLS opt-in, leap-year Feb, cache-key validation,
headless gating, GEE 5xx retry, and (2026-07-04) the cross-year date-based
axis with real-date band labels.
- Integration design (fold into the package): `docs/sentinel_integration.md`.

## Backlog

- ~~script1 real-date band labels~~ — DONE 2026-07-04 (in `sentinel/`
  in this repo, now the source of truth).
- ~~MODIS NDVI driver~~ — BUILT 2026-07-06, LIVE-VERIFIED 2026-07-07
  (Rwanda 2021 passed, see Immediate next step #2).
- ~~Crop-mask layer (ESA WorldCover via GEE)~~ — DONE 2026-07-07
  (see "Crop-mask layer" section; v0.5.0, live-verified).
- **Security**: rotate the EOSDIS Earthdata credentials hardcoded in the
  legacy `agwise-planting-date-and-cultivar/main/RS/get_MODISdata.R`
  (username+password in plain text in a shared repo, found 2026-07-06;
  urs.earthdata.nasa.gov → change password). Not our repo — flag to the
  AgWise maintainers.
- ~~Live CDS smoke test of the SEAS5 driver~~ — DONE 2026-07-04 (passed).
- ~~Dead code in `agwise_phenology_utils.combine_indices_pixelwise`~~ —
  DONE 2026-07-04: 87 unreachable lines after the closing `raise` removed
  (old pre-chunking implementation); smoke-tested both combine methods and
  the error path; `sentinel_scripts/` working copy synced.
- `replace_outliers` fabricates data
  (replaces ~13% of pixels with the regional mean — consider NaN; science
  decision for Lizeth, still open).
- **R wrapper bug (pre-existing, found 2026-07-14):** `ad_extract_growing_
  season` and `ad_extract_points` in `r/agwise_data.R` append
  `--fill-nearest-m <fill_nearest_m>` but neither has that parameter and the
  `extract` CLI has no such flag → both error at runtime. Drop those two
  lines (only `extract-static`/`ad_extract_static_points` take fill-nearest).
  Not touched this session to keep the crop-model change focused.
- **Security**: rotate the CDS key hardcoded in the legacy
  `chirps_download 1.R` (cds.climate.copernicus.eu → regenerate token).
- ~~CI~~ — DONE 2026-07-04: `.github/workflows/tests.yml` pushed (token
  got the Workflows permission); pytest matrix runs on push/PR to main.

## Environment (CGLabs)

- conda env `agwise_data`. In production `AGWISE_DATA_ROOT` → the shared
  `common_data` so one download serves everyone — but **for testing use a
  separate test root** (see Ground Rules above); never write into the
  originals. CDS creds in `~/.cdsapirc` (never in code). See
  `docs/cglabs_setup.md`.
- CGLabs already has downloaded data and better network to these servers —
  do the real end-to-end verifications there.

## Repo layout

```
src/agwise_data/{__init__,api,cache,catalog,config,boundaries,harmonize,spatial,stac,terrain,cli}.py
src/agwise_data/catalog/{chirps,agera5,dem,soil,seas5,mod13q1,myd13q1,esa_worldcover}.yaml
src/agwise_data/drivers/{__init__,base,chirps,agera5,static,dem,soil,seasonal,gee,modis,worldcover}.py
src/agwise_data/writers/{__init__,_common,dssat,apsim,soil}.py   # crop-model input files
sentinel/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py + README.md
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,credentials_setup,pipeline_map,roadmap,sentinel_integration,crop_model_inputs}.md
```

API surface now: get_climate, extract_points, extract_growing_season,
get_static/get_dem/get_soil, extract_static_points, get_seasonal, get_modis/
get_ndvi, get_cropmask, **get_season**, **to_dssat**, **to_apsim**.
