# HANDOFF — continue here

Session state for `agwise-data` (repo: `byliz-ai/data_sourcing`). Read this
first; it is written so the next session does not have to re-derive anything.
Last updated: 2026-07-07 (GEE UNBLOCKED — project `moodle-sites-440814`;
MODIS NDVI/EVI driver live-verified; ESA WorldCover crop-mask layer BUILT
+ live-verified, v0.5.0).

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

1. ~~**Unblock GEE project access.**~~ **DONE 2026-07-07.** The working
   Cloud project is **`moodle-sites-440814`** (Lizeth supplied the real
   ID). `ee.Initialize(project="moodle-sites-440814",
   opt_url="https://earthengine-highvolume.googleapis.com")` succeeds with
   the existing `~/.config/earthengine/credentials` token
   (`llanoslizeth@gmail.com`) and reads MOD13Q1. The earlier failures were
   just wrong names — `ee-moodle-sites` / `ee-moodle-sites-440814` do not
   exist; `ee-pgd31792` still denies her but is no longer needed. Use
   `export AGWISE_GEE_PROJECT=moodle-sites-440814` (or `gee_project:` in
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
sentinel/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py + README.md
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,credentials_setup,pipeline_map,roadmap,sentinel_integration}.md
```
