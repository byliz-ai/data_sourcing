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
   Consequence: `script1` (Sentinel, in OneDrive — NOT in this repo or on
   this machine) must label bands by **real date** instead of day-of-year.
   Self-contained fix, still pending because the script lives in OneDrive;
   do it next time the OneDrive scripts are at hand. The fail-loud guard
   currently in place means nothing silently breaks meanwhile.
2. **SoilGrids urban/water NaN → fill from the nearest valid pixel** —
   IMPLEMENTED (see below): bounded search radius (`fill_nearest_m`,
   default 1 km), traceability column `<VAR>_fill_m` per variable
   (0 = own pixel valid, >0 = donor distance in m, NaN = nothing valid in
   range so the value stays NaN).

## Immediate next step

1. **Live-verify the SEAS5 driver on CGLabs** — the driver is built and
   unit-tested but has NOT hit the real CDS API (no `~/.cdsapirc` on the
   machine used this session). Smoke test: 1 variable, 1 year, small bbox:
   `get_seasonal("PRCP", init_month=2, years=1995, bbox=(29,-3,31,-1))`.
   Check: 25 members, valid dates start Feb 2, plausible mm/day values,
   PRCP de-accumulation sane (no negatives).
2. **MODIS NDVI driver** — still blocked on GEE credentials.
3. **CI**: `.github/workflows/tests.yml` — the workflow file move is
   blocked because the fine-grained GitHub token lacks the **Workflows**
   permission; add "Workflows: read and write" to the token (or add the
   file via the GitHub web UI). The file currently lives in `ci/tests.yml`.
4. Housekeeping: rotate the leaked CDS key (see Backlog).

## Seasonal (SEAS5) layer (BUILT 2026-07-04, network-free tested; NOT yet live-verified against CDS)

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

## Sentinel phenology scripts (in OneDrive, fixed, standalone — NOT in repo)

`data sourcing scripts/{script1_Download_Stack_Smooth,agwise_phenology_utils}.py`.
For Lizeth's scope, **script1 IS the input generator**; scripts 1b/2/3/4
are out of scope. Already fixed: parallel composite downloads, TLS opt-in,
cross-year DOY fail-loud guard, leap-year Feb, cache-key validation,
headless gating, GEE 5xx retry.
- **Decision closed 2026-07-04**: seasons DO cross the calendar year → the
  remaining work on script1 is real-date band labels (see Decisions above).
- Integration design: `docs/sentinel_integration.md`.

## Backlog

- **script1 real-date band labels** (decision made; script in OneDrive).
- MODIS NDVI driver (needs GEE credentials to build/validate).
- Live CDS smoke test of the SEAS5 driver on CGLabs (see Immediate next step).
- Cleanups flagged in review: dead code in
  `agwise_phenology_utils.combine_indices_pixelwise` (~lines 951-1037,
  unreachable after the `raise`); `replace_outliers` fabricates data
  (replaces ~13% of pixels with the regional mean — consider NaN; science
  decision for Lizeth, still open).
- **Security**: rotate the CDS key hardcoded in the legacy
  `chirps_download 1.R` (cds.climate.copernicus.eu → regenerate token).
- **CI**: move `ci/tests.yml` → `.github/workflows/tests.yml`; needs the
  GitHub token to have the Workflows write permission (fine-grained) or
  `workflow` scope (classic). Pushing it was rejected 2026-07-04.

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
r/agwise_data.R          tests/            examples/
docs/{architecture,cglabs_setup,pipeline_map,roadmap,sentinel_integration}.md
```
