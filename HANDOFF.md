# HANDOFF — continue here

Session state for `agwise-data` (repo: `byliz-ai/data_sourcing`). Read this
first; it is written so the next session does not have to re-derive anything.
Last updated: 2026-07-03 (evening — soil+DEM layer done on CGLabs).

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

## Immediate next step (to agree with Lizeth)

The soil + DEM driver is **DONE and verified on CGLabs** (see next section).
Natural candidates for the next step, in rough priority order:

1. **The cross-year decision on `script1`** (Sentinel) — cheap to resolve:
   ask Lizeth whether any use-case season crosses the calendar year; if yes,
   switch band labels to real dates (self-contained fix).
2. **SEAS5 seasonal hindcast driver** (CDS; Jemal's proposal) — same
   catalog+driver pattern.
3. **MODIS NDVI driver** — blocked on GEE credentials.
4. Housekeeping: rotate the leaked CDS key; enable CI (see Backlog).

### Open science decision from the soil verification

SoilGrids masks urban areas and water (NoData): central Kigali and Musanze
town return **NaN** for soil properties while ELEV works fine (≈35% NaN
pixels in a 0.1° window around Kigali city vs ~6% over the full test domain
— verified 2026-07-03). Trial points on masked pixels get NaN columns.
Whether to fill from the nearest unmasked pixel (and how far to search) is
a **science decision for Lizeth** — same category as the `replace_outliers`
question. The extraction itself is correct.

## Soil + DEM layer (DONE 2026-07-03, verified on CGLabs)

Both pending probes passed on CGLabs before building: DEM rasterio windowed
read (single-tile Kigali + cross-tile mosaic, sub-second, EPSG:4326) and
SoilGrids WCS GetCoverage (200 OK, 4326 GeoTIFF, plausible clay values).

What was built (all network-free-tested; **45 tests pass**, up from 31):

- `drivers/static.py` — `StaticDriver` base: `ensure_static(variable,
  domain)` → cached `Static_<VAR>.nc` + `.meta.json`, file-locked, mirrors
  the climate `Driver` with no time axis. Derived variables (slope, aspect,
  TPI, TRI) are computed from the *cached* elevation — DEM fetched once.
- `drivers/dem.py` — Copernicus GLO-30: enumerates 1° tiles intersecting
  the bbox, `rasterio.merge(bounds=...)` does the windowed mosaic (missing
  ocean tiles → NaN). Pixel guard `MAX_PIXELS` (450 Mpx) rejects
  continent-scale requests at 30 m with a clear message.
- `drivers/soil.py` — SoilGrids via ISRIC WCS 2.0.1 (native 4326, ~250 m).
  One cached file per property holds **all six depths** (depth dim), so any
  later depth subset is a cache hit. Requests >2° are chunked and
  mosaicked. NoData 0 masked → NaN; scaled-integer conversions (`d10`,
  `d100`) declared per property in the catalog.
- `terrain.py` — slope/aspect/TPI/TRI with per-latitude meter conversion
  (cos(lat)); unit-tested against analytic planes.
- `harmonize.py` — `STATIC_VARS` registry (`TOPO.*`, `SOIL.*` + legacy
  names like `altitude`, `clay`), `standardize_static`.
- API: `get_static` / `get_dem` / `get_soil` (products + GeoTIFF export;
  depth subsets get their own product filename) and
  `extract_static_points` (wide columns `ELEV`, `CLAY_0_5cm`, ...; points
  grouped into 1° cells when their bbox is large so a national trial set
  cannot blow memory — each cell's window is cached and reused).
- CLI `get-static` / `extract-static`; R `ad_get_static` / `ad_get_dem` /
  `ad_get_soil` / `ad_extract_static_points`; catalog `dem.yaml` /
  `soil.yaml`; STAC export covers static vars; version bumped to 0.2.0.

**Real-bbox verification on CGLabs** (test root, never common_data):
central-Rwanda 1°×1° bbox → `get_dem` 48 s cold (ELEV/SLOPE/ASPECT, 3960²,
elevations 1287–4507 m), `get_soil` 10 s (CLAY/PH/SOC × 2 depths, clay
mean 36%, pH 3.7–7.3), point extraction instant from cache with plausible
values (Kigali 1510 m, Huye 1765 m, clay 37%, pH 5.7). iSDAsoil (Africa,
30 m) remains an optional alternative source (not needed now).

Env note: a fresh `agwise_data` conda env from `environment.yml` on CGLabs
had a broken rasterio (`libjxl.so.0.11` missing — conda-forge pulled
libjxl 0.12 against a GDAL built for 0.11). Fix:
`conda install -n agwise_data -c conda-forge libjxl=0.11`.

## What is already DONE (tested + pushed to main)

- **Climate layer**: CHIRPS + AgERA5 drivers → harmonized `AGRO.*` cubes.
  `get_climate` / `extract_points` / `extract_growing_season` (the last
  reproduces the legacy fertilizer columns `Precipitation_m1..mN`,
  `totalRF`, `nrRainyDays`). CLI `agwise-data`, R wrapper `r/agwise_data.R`,
  STAC export, provenance manifests. **31 network-free tests pass.**
- **Performance**: region-scoped caches (`rg_*` domains) so a country
  request fetches only its window (CHIRPS via daily-COG windowed reads,
  AgERA5 via bbox-cropped CDS); parallel prefetch of all (variable, year);
  segmented multi-connection downloads; aligned NetCDF chunking. Env knobs
  `AGWISE_DATA_WORKERS`, `AGWISE_DATA_SCOPE`. Verified end-to-end on Rwanda.
- **Robustness**: UCSB rate-limits aggressively (HTTP 403 → temporary IP
  ban). Code treats 403 as "blocked" → falls back to the yearly NetCDF, and
  a driver invariant refuses to cache an incomplete past year.

## Sentinel phenology scripts (in OneDrive, fixed, standalone — NOT in repo)

`data sourcing scripts/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py`.
For Lizeth's scope, **script1 IS the input generator** (the smoothed
multi-index stack). Scripts 1b/2/3/4 consume it and are **out of scope**.
Already fixed (both compile): parallel composite downloads
(`max_download_workers`), TLS verification opt-in only (`AGWISE_INSECURE_SSL`),
scoped warning filter, cross-year DOY **fail-loud guard**, leap-year Feb,
cache-key validation by index set, headless gating of `ee.Authenticate`/
`input()`, GEE 5xx retry.
- **Open decision**: do any use-case seasons cross the calendar year (e.g.
  Rwanda season B, Sep→Feb)? If yes, the ONLY remaining work is to make
  `script1` label bands by **real date** instead of day-of-year — fully
  self-contained (no downstream scripts to touch, since 2-4 are out of
  scope). If all seasons are single-year, it already works.
- Integration design (folding Sentinel into the package as a distinct
  *product type*, needs live GEE creds): `docs/sentinel_integration.md`.

## Backlog

- MODIS NDVI driver (needs GEE credentials to build/validate).
- SEAS5 seasonal hindcast driver (CDS; Jemal's standardization proposal).
- Nearest-unmasked-pixel fill option for SoilGrids urban/water NaN in
  `extract_static_points` (pending Lizeth's science decision, see above).
- Cross-year date-based band labels in `script1` (if seasons cross the year).
- Cleanups flagged in review: dead code in
  `agwise_phenology_utils.combine_indices_pixelwise` (~lines 951-1037,
  unreachable after the `raise`); `replace_outliers` fabricates data
  (replaces ~13% of pixels with the regional mean — consider NaN; science
  decision for Lizeth).
- **Security**: rotate the CDS key hardcoded in the legacy
  `chirps_download 1.R` (cds.climate.copernicus.eu → regenerate token).
- **CI**: move `ci/tests.yml` → `.github/workflows/tests.yml` after
  `gh auth refresh -h github.com -s workflow` (the initial push token lacked
  the `workflow` scope).

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
src/agwise_data/catalog/{chirps,agera5,dem,soil}.yaml
src/agwise_data/drivers/{__init__,base,chirps,agera5,static,dem,soil}.py
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,pipeline_map,roadmap,sentinel_integration}.md
```
