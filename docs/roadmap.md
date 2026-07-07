# Roadmap

v0.1 deliberately covers only historical climate — the bottleneck every
module named in Nairobi. Everything below follows the same pattern (a
catalog YAML + a driver), so each addition is incremental.

## Near term

1. ~~**Seasonal hindcast/forecast driver (SEAS5)**~~ — **DONE (v0.3)**:
   `SeasonalDriver` base with `ensure_seasonal`, SEAS5 via CDS
   `seasonal-original-single-levels` (per-year append-only cache
   `Seasonal_<VAR>_i<MM>_<year>.nc`, dims `(member, time, lat, lon)` with
   valid-date time, de-accumulation of PRCP/SRAD, AGRO units so hindcast
   and observations pair by name for bias correction — Jemal's proposal;
   the historical/observation half is `get_climate`). API `get_seasonal`;
   CLI `get-seasonal`; R `ad_get_seasonal`; catalog `seas5.yaml`.
   Live-verified against CDS 2026-07-04 (PRCP i02/1995, Rwanda bbox:
   25 members, 215 valid days, plausible mm/day, clean de-accumulation).
2. ~~**Soil & DEM driver**~~ — **DONE (v0.2)**: `StaticDriver` base with
   `ensure_static`, Copernicus GLO-30 DEM (windowed COG reads, derived
   slope/aspect/TPI/TRI) and SoilGrids 2.0 via the ISRIC WCS (native 4326
   subsets, all six depths cached per property). API: `get_dem`, `get_soil`,
   `get_static`, `extract_static_points`; CLI `get-static`/`extract-static`;
   R `ad_get_dem`/`ad_get_soil`/`ad_extract_static_points`. iSDAsoil (30 m,
   Africa) remains a candidate alternative source.
3. **GEE driver (MODIS NDVI, crop masks)** — NDVI/EVI half **BUILT (v0.4)
   and LIVE-VERIFIED 2026-07-07**: `ModisGeeDriver` (MOD13Q1 + MYD13Q1 v6.1
   via `ee.data.computePixels`, tiled pulls, per-year append-only cache
   `Composite_<VAR>_<year>.nc`, QA/fill/range masking declared in the
   catalog and recorded in manifests). API `get_modis`/`get_ndvi`
   interleaves Terra+Aqua into the 46-composites-per-year phenology
   series; CLI `get-modis`; R `ad_get_modis`; STAC works (`RS.*` vars).
   Live smoke test (Rwanda 2021, GEE project `moodle-sites-440814`) passed:
   46 composites, 23+23 Terra/Aqua, NDVI [-0.2, 1], Lake Kivu fully masked,
   62 s cold. The ESA WorldCover **crop-mask** layer is now **BUILT
   (v0.5) and live-verified 2026-07-07**: `WorldCoverGeeDriver` (static,
   `esa_worldcover` catalog) aggregates class 40 to cropland fraction via
   `reduceResolution` and thresholds it (`crop_fraction_min`, in the
   catalog/manifests) to a 1/NaN mask on the *same 1/480° grid as the
   composites*, so masking non-crop is a straight multiply. API
   `get_cropmask` (`LC.CROPLAND`); CLI `get-cropmask`; R `ad_get_cropmask`.
   The MODIS and WorldCover drivers now share one GEE fetch module
   (`drivers/gee.py`: client init, request tiling, tiled `computePixels`).
3b. **Sentinel-1/2 phenology driver** — fold the SAR/optical phenology
   pipeline in as a new *product type* (composite stacks, not daily cubes).
   Design + fix-mapping in [sentinel_integration.md](sentinel_integration.md);
   needs live GEE credentials to build and validate.
4. **CHIRPS v3.0 catalog entry** — v3 dailies are being released; add as a
   separate catalog id so v2/v3 can be compared before switching the
   default.
5. **Current-season CHIRPS** — use the `by_month` files (already in the
   catalog as an alternative access block) and/or prelim data for
   near-real-time monitoring during the season.

## Medium term

6. **Common-grid regridding** (`align_to=`) — the "on-the-fly
   transformation" discussed with the Hub team: regrid multi-source stacks
   (soil 150 m, climate 1–5 km) to an agreed target grid, with honest
   metadata about native resolution. Needs the group to agree the target
   grid first.
7. **Cloud-optimized reads** — CHIRPS daily COGs are already cataloged as
   an alternative access block; a `https-cog` driver would fetch only the
   lat/lon window via range requests instead of yearly files. Same for
   Hub-hosted Zarr when it exists. New access `type` in the catalog; the
   API does not change.
8. **Pre-computed scenario library** — the Nairobi idea of pre-computing
   forecast × management scenarios before the season; `products/` +
   manifests is the natural home.

## Data Hub handoff (continuous)

- Keep catalog fields aligned with the Climate Data Hub metadata standard
  as it evolves past v0.1 (`agwise-data catalog stac <id>` already emits
  STAC Collections).
- Nominate reusable intermediate products (e.g. Africa-domain harmonized
  AgERA5) for Hub hosting once cross-program governance lands.
- Define the SFP/AoW1 agronomic metadata *extension* with the Hub team
  (action #7 from Nairobi) — candidate fields: crop, season definition,
  growing-window logic, trial-data linkage.
