# Sentinel-1/2 phenology input generator

**This directory in git is the source of truth** for the Sentinel scripts
(decision: Lizeth, 2026-07-04) — the AgWise modules run what is here, not
the OneDrive copies.

- `script1_Download_Stack_Smooth.py` — **the input generator** for the
  phenology/planting-date work: downloads Sentinel-2 index and Sentinel-1
  band composites from GEE, stacks them on a common grid and SG-smooths
  the per-pixel time series. Within the data-sourcing scope, this script
  IS the deliverable; the downstream phenology scripts (1b/2/3/4) consume
  its stack and are out of scope here.
- `agwise_phenology_utils.py` — shared helpers (boundaries, GEE
  composites, season/date math, regrid + smoothing, band-name parsing).

Key conventions (since 2026-07-04):

- **Time axis = days since season start**, so seasons that cross the
  calendar year (e.g. Rwanda season B, Sep→Feb) work; day-of-year wrapped
  365→1 and is no longer used as the axis.
- **Bands are labelled with the real date** (`EVI_20200914_SG`); the meta
  CSV carries both `date` (ISO) and `doy`. `parse_band_name` accepts both
  the new date labels and the legacy `EVI_DOY152_SG` style, so stacks
  built before this change still parse.

Requirements beyond the package: `ee` (earthengine-api), `geemap`, and an
authenticated GEE account (`earthengine authenticate`). Headless runs fail
loud instead of hanging on interactive auth.

Longer-term plan: fold this into the package as a proper driver/product
type — see `docs/sentinel_integration.md`.
