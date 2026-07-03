# Roadmap

v0.1 deliberately covers only historical climate — the bottleneck every
module named in Nairobi. Everything below follows the same pattern (a
catalog YAML + a driver), so each addition is incremental.

## Near term

1. **Seasonal hindcast/forecast driver (SEAS5)** — implement the
   standardization proposal from the planting-date module (Jemal): one-time
   historical + hindcast download, common grid/calendar/naming, merged
   `Daily_<VAR>_<y0>_<y1>.nc` products ready for bias correction and DSSAT.
   The `harmonized/` layout and manifests here were designed to receive it.
2. **Soil & DEM driver** — SoilGrids / iSDAsoil / SRTM. Static layers, so
   `ensure_daily_year` generalizes to `ensure_static` (fetch once, clip per
   region). Reuses the download classes from
   [CGIAR-AgWise/agwise_data_sourcing](https://github.com/CGIAR-AgWise/agwise_data_sourcing).
3. **GEE driver (MODIS NDVI, crop masks)** — wrap the existing GEE toolkit
   so its outputs land in the shared cache with manifests, and the
   planting-date phenology scripts read from there.
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
