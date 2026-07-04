# HANDOFF — continue here

Session state for `agwise-data` (repo: `byliz-ai/data_sourcing`). Read this
first; it is written so the next session does not have to re-derive anything.
Last updated: 2026-07-04 (SoilGrids fill + SEAS5 driver done; decisions from
Lizeth recorded).

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

1. **PUSH TO ORIGIN** — `main` is 11 commits ahead of `origin/main` (soil/DEM,
   SEAS5 v0.3.0, CI workflow, sentinel/ move, dead-code cleanup). Push failed
   2026-07-04: **no GitHub credentials on this machine** (no `gh`, no
   `~/.git-credentials`, no token in env). Lizeth: run
   `git -C ~/agwise_data_test/data_sourcing push origin main` with your token
   (or store it via `git config credential.helper store` first).
2. **MODIS NDVI driver** — still blocked on GEE credentials; also the
   `earthengine-api` package is not installed in the `agwise_data` env yet.
3. Housekeeping: rotate the leaked CDS key (see Backlog).

(SEAS5 live verification: **DONE 2026-07-04** — real CDS smoke test
passed: PRCP i02/1995, Rwanda bbox → 25 members, 215 valid days starting
1995-02-02, mean 3.0 mm/day, max 36.7, no negatives, no NaNs. CDS creds
now in `~/.cdsapirc` on this machine.)

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
- MODIS NDVI driver (needs GEE credentials to build/validate).
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
src/agwise_data/catalog/{chirps,agera5,dem,soil,seas5}.yaml
src/agwise_data/drivers/{__init__,base,chirps,agera5,static,dem,soil,seasonal}.py
sentinel/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py + README.md
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,pipeline_map,roadmap,sentinel_integration}.md
```
