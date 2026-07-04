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
