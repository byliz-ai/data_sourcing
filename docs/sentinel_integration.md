# Integrating the Sentinel-1/2 phenology pipeline

This is the design for folding the AgWise SAR/optical phenology pipeline
(`script1_Download_Stack_Smooth.py` + `agwise_phenology_utils.py` + the
threshold/derivative/cross-validation scripts) into `agwise-data`. It is
the concrete "start the integration" step; the driver code lands once it
can be built against live Google Earth Engine (GEE) credentials — see
*Why not merged yet* below.

## What is different from the climate sources

CHIRPS/AgERA5 are **daily climate cubes**: one variable, a regular daily
time axis, `Daily_<VAR>_<year>.nc`. Sentinel phenology is a different
shape and does **not** fit the `Driver._fetch_year(variable, year, domain)`
contract:

- **Composites, not days** — S2 (10 m) and S1 (20 m) composites at
  irregular ~10–12 day steps, per index (EVI, NDVI, NDRE, VHVV, RVI, …).
- **A season, not a calendar year** — the unit is a planting→harvest window
  that can cross the New Year.
- **Two grids** — S1 is resampled onto the S2 reference grid.
- **A processed product** — the useful artefact is the SG-smoothed
  multi-index stack, then downstream phenology rasters (sowing/harvest/
  duration), not the raw composites.

So Sentinel enters `agwise-data` as a **new product type** alongside the
climate cubes, sharing the cache/manifest/parallelism machinery but with
its own driver contract — not by pretending a composite stack is a daily
cube.

## The seam

```
catalog/sentinel.yaml         # Hub-core metadata + GEE access recipe
drivers/sentinel_gee.py       # SentinelDriver: builds the smoothed stack
  ->  reuses cache.locked / atomic_write / write_manifest
  ->  reuses the parallel-fetch pattern (already added to script1)
  ->  wraps get_s2_composite / get_s1_composite / vectorized_regrid_and_smooth
      from agwise_phenology_utils (moved into the package unchanged)
api.get_sentinel_stack(country|bbox, crop, season, indices, ...)
  -> harmonized/sentinel/<region>/<crop>/MultiIndex_<season>_SG.tif  (+ .meta.json)
r/agwise_data.R : ad_get_sentinel_stack(...)  # same CLI-shell pattern
```

The phenology **methods** (threshold, first/second derivative, SG
derivative, cross-validation) stay where they are — they consume the
stack. Only the *sourcing + harmonization* half moves into `agwise-data`.

## What the move fixes for free

Each maps to a finding from the script review:

| Script problem (review) | Fixed by the integration because… |
| --- | --- |
| Cross-year seasons corrupt the DOY axis | the package already works in real dates / days-since-planting; band labels become dates, not `DOY###` — no wrap at 365 |
| Weak cache key (index set only) | `.meta.json` manifests record the full config (indices, bands, scale, orbit, season, crop mask); the cache check compares the manifest, not a filename |
| Serial composite downloads | the parallel-prefetch machinery (`max_workers`, added to `script1` in this pass) is standard here |
| Band-count coupling (`step=8` duplicated) | one grid-planning function in the package, single source of truth |
| GADM/local-file boundary handling | `boundaries.py` (geoBoundaries, cached) replaces the ad-hoc GADM/shapefile split |
| `_tmp_download` grows unbounded | raw composites live under `raw/sentinel/` with the same `keep_raw` cleanup policy as the climate drivers |
| Global TLS disable / blanket warning filter | package code keeps verification on; opt-in only via env var (already applied to the shared utils in this pass) |

## Provenance / Hub handoff

A Sentinel stack manifest carries the same core as the climate products
(source, catalog version, region, creation time) plus Sentinel-specific
fields (indices, composite step, S1 orbit, crop-mask source, SG window/
poly). `agwise-data catalog stac sentinel` emits the STAC Collection, so a
curated stack is a candidate for Climate/Commodities Data Hub hosting on
the same path as CHIRPS/AgERA5.

## Why not merged yet

The driver calls GEE (`earthengine-api`, `geemap`) and can only be built
and validated with a project's GEE credentials, which are not available in
this environment. Rather than commit unrunnable, untested GEE code that
looks finished, the driver is scheduled as the next focused step: move
`agwise_phenology_utils` into `src/agwise_data/sentinel/` unchanged, add
`drivers/sentinel_gee.py` + `catalog/sentinel.yaml`, and validate one
season end-to-end against a real GEE project before wiring the R/CLI
surface. The direct fixes to the standalone scripts (security,
parallel downloads, cross-year guard, cache-key validation) have already
been applied so the pipeline is safer and faster to run today, in or out
of the package.
