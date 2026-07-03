# HANDOFF — continue here

Session state for `agwise-data` (repo: `byliz-ai/data_sourcing`). Read this
first; it is written so the next session does not have to re-derive anything.
Last updated: 2026-07-03.

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

## Immediate next step (agreed with Lizeth)

**Build the soil + DEM driver.** This is the next input the fertilizer and
soil-health modules need after climate. Static layers (no time axis), so
they need a new `StaticDriver` path — NOT `ensure_daily_year`.

### Verified data sources (probed 200 OK on 2026-07-03)

- **DEM — Copernicus GLO-30** (AWS Open Data, per-tile COGs, **EPSG:4326**,
  clean windowed reads). Tile URL pattern:
  `https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif`
  where `{NS}`=N/S, `{EW}`=E/W, lat/lon = integer degree of the tile's SW
  corner (e.g. Kigali → `..._S02_00_E030_00_DEM...`). A bbox spans several
  tiles → mosaic them, then window. Read with GDAL env
  `AWS_NO_SIGN_REQUEST=YES`, `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`.
  Derive slope/aspect from elevation (richdem or numpy gradient).
  ⚠️ NOT YET run through a rasterio windowed read end-to-end — that probe was
  interrupted by the move to CGLabs. **Verify it first on CGLabs** (it has
  better network to AWS): open `/vsicurl/<tile>`, `from_bounds(...)`, read.
- **SoilGrids 2.0 — ISRIC**. Two access options:
  - **WCS (recommended for bbox):** `https://maps.isric.org/mapserv?map=/map/{property}.map`
    WCS 2.0.1 `GetCoverage` returns a 4326 subset directly — no reprojection.
  - COGs on `https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_{stat}.vrt`
    but these are in **Goode Homolosine (IGH)** projection → must transform
    the bbox to IGH, read window, reproject to 4326. Prefer the WCS.
  Properties: `bdod cec cfvo clay sand silt nitrogen soc phh2o wv0010 wv0033 wv1500`;
  depths `0-5 5-15 15-30 30-60 60-100 100-200` cm; stat usually `mean`.
- **iSDAsoil** (Africa, 30 m) — AWS `s3.eu-central-1` (301 → follow redirect).
  Optional higher-res alternative for African use cases.

### Design (fits the existing package)

1. `StaticDriver` base in `drivers/` with `ensure_static(variable, region)`
   returning a cached, harmonized 2-D/3-D (by depth) product + `.meta.json`
   manifest — mirror the climate `Driver` but with no time axis.
2. Catalog YAMLs `catalog/soil.yaml`, `catalog/dem.yaml` with Hub-core
   metadata (id/title/license/providers/extent/version) + access recipes.
3. API: `get_soil(...)`, `get_dem(...)` (or a unified `get_static`) + a
   **point extraction** path (fertilizer extracts soil/topo AT trial points,
   like `extract_growing_season` but static — no dates). Reuse
   `boundaries.py`, region-scoped domains, parallel prefetch, cache locks.
4. Network-free tests via a fake static driver (mirror `tests/conftest.py`'s
   `FakeDriver`); then ONE real bbox verified on CGLabs.
5. R wrapper: `ad_get_soil` / `ad_get_dem` via the CLI-shell pattern.

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

## Backlog (after soil/DEM)

- MODIS NDVI driver (needs GEE credentials to build/validate).
- SEAS5 seasonal hindcast driver (CDS; Jemal's standardization proposal).
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
src/agwise_data/{__init__,api,cache,catalog,config,boundaries,harmonize,spatial,stac,cli}.py
src/agwise_data/catalog/{chirps,agera5}.yaml        # add soil.yaml, dem.yaml
src/agwise_data/drivers/{__init__,base,chirps,agera5}.py   # add static.py, soil.py, dem.py
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,pipeline_map,roadmap,sentinel_integration}.md
```
