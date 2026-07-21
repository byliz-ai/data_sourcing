# Changelog

All notable changes to `agwise-data`. Versions follow the `version` field in
`pyproject.toml`. Newest first.

---

## 0.27.0 — CGLabs resource optimization, phase 2 (memory budget + streaming)
Builds the memory-budget foundation and stops the biggest per-call accumulator.
- **New `agwise_data.memory`** detects the process's real ceiling from the
  cgroup (`/sys/fs/cgroup/memory.max` v2 / `memory.limit_in_bytes` v1; a near-
  INT64 sentinel = unlimited) — **never** from `psutil`/`free`/`/proc/meminfo`,
  which report the ~220 GB *host*, 7x the real ~32 GB cap and a guaranteed OOM
  if sized against. `usable_budget_bytes` reserves headroom (default 8 GB,
  `AGWISE_MEM_HEADROOM_GB`); `AGWISE_MEM_LIMIT_GB` overrides the limit. Also
  `estimate_peak_bytes` and `warn_if_over_budget`. `Config` exposes
  `mem_limit_bytes` / `mem_budget_bytes`.
- **`max_workers` auto-derives from the budget** when not set explicitly: on a
  smaller container the pool shrinks so concurrent fetches can't OOM (a 6 GB
  container → 1 worker), while the standard box stays at the baseline of 4. It
  only ever *reduces* — raising concurrency for throughput is a separate,
  rate-limit-aware change. An explicit `AGWISE_DATA_WORKERS`/YAML value wins.
- **Product fetches no longer `.load()` the whole cube before writing.**
  `get_climate`/`get_static`/`get_seasonal`/`get_modis`/`get_season` now pass
  the lazy region cube straight to the (atomic) NetCDF write — which streams it
  chunk-by-chunk — then return it reopened lazily from disk. A multi-variable
  request now peaks at **one** variable's cube, not the sum of all of them (a
  9-variable `get_soil` no longer holds nine loaded cubes at once). The
  geometry-clip branch still materializes (rioxarray clip needs it).
- **A budget guard warns** before a materialization whose estimated peak exceeds
  the budget, pointing at a smaller region/period or `AGWISE_MEM_LIMIT_GB`
  (advisory — it does not block).
- +11 tests (`test_memory.py`). Suite 221 passing.

## 0.26.0 — CGLabs resource optimization, phase 1 (memory quick wins)
First batch of the memory/throughput plan for the shared ~32 GB CGLabs
container (see `CGLABS_OPTIMIZATION_PLAN.md`). Each item cuts peak memory or
tames uncontrolled concurrency; behaviour is numerically unchanged.
- **Bounded dask's hidden thread pool.** Every `.load()` used to fire dask's
  default threaded scheduler at `os.cpu_count()` (~40 threads on CGLabs) — a
  third pool invisible to `max_workers`/`cog_workers` that oversubscribed cores
  and inflated per-load peak. `Config.load()` now caps it to the fetch worker
  count (`config._cap_dask_scheduler`).
- **Terrain TPI/TRI no longer build an (8, H, W) neighbour stack** — they
  accumulate over the 8 shifted views instead (`terrain._neighbor_views`),
  cutting peak from ~9x the elevation array to ~2–3x (near the DEM pixel cap the
  stack alone exceeded 10 GB). Output is bit-identical.
- **Terrain derivatives run serially**, deriving from one loaded elevation,
  instead of concurrently under the static prefetch pool (each held its own
  copy). Network-bound soil/DEM layers still fetch in parallel
  (`_prefetch_static`). No I/O to overlap, so no speed cost.
- **Honest `cog_workers` default** — the constructor said 3 while `Config.load()`
  used 8; both are 8 now (the operative value), so memory-budget reasoning isn't
  understated 2.6x.
- **Env overrides for the memory-governing knobs:** `AGWISE_COG_WORKERS`,
  `AGWISE_REGION_MAX_AREA_DEG2`, `AGWISE_DOWNLOAD_PARTS` (previously YAML-only).
- **Windowed reads of the huge global NetCDFs no longer read the whole globe.**
  The local CHIRPS v3 (~24 GB/yr) and the CHIRPS v2 yearly-global fallback were
  opened with a dask chunk spanning the entire lat/lon plane, so a region window
  read/decompressed multi-GB blocks per time-step just to discard all but the
  window. They now open without dask chunks, so the netCDF4 backend reads an
  indexed hyperslab of exactly the window (`local.fetch_local_year`,
  `chirps._fetch_year_netcdf`). Verified on the real 24 GB file: same values,
  **~8 s vs a >3 min timeout**, and a tiny fraction of the memory.
- **Break the `max_workers x cog_workers` fan-out.** While the outer prefetch
  pool runs several climate fetches at once, the inner per-fetch fan-out
  (`cog_workers`, the CHIRPS COG/GEE window/tile reads) is pinned to 1
  (`api._pinned_cog_workers`), so peak concurrency is `max_workers` — not the
  4x8=32 in-flight reads plus four year-arrays that were the OOM shape. A serial
  (single-task) fetch keeps the full inner fan-out.
- **QDM regrid/bias-correction in float32.** `bias_correct_cube`'s downscaled
  member x time x fine-grid cubes are the largest allocation; the regrid now
  stays float32 (the per-pixel quantile mapping still upcasts to float64, so
  precision is unchanged) and skips building the second full nearest-fill cube
  when linear interpolation already covered every cell — roughly halving peak.
- **Preallocate-and-fill instead of `list + np.stack`** in the array assemblers
  (`modis`, `soil`, `local` static/composite), removing a full-cube transient
  copy at the tail of each fetch. Output is bit-identical.

## Session summary — 2026-07-21 (v0.19.0 → v0.25.0)

A hardening pass driven by three brand-new-user QA/QC walkthroughs (Kisumu, 10
points; Addis Ababa, 50 points, cross-year Dec 2022–Jul 2023; and the seasonal
**forecast** chain at Addis, including a real downloadable July 2026 SEAS5
init). Every fix below came from a failure or friction hit while following only
the repo docs end-to-end — download all variables, then write DSSAT/APSIM/
WOFOST/ORYZA. Per-version detail is in the entries beneath this summary.

**Reuse the data already on CGLabs (faster, fewer downloads)**
- DEM/terrain served from the staged Copernicus GLO-30 tiles, tile-by-tile,
  before AWS (v0.19.0); terrain derivatives in float32 (half the memory).
- CHIRPS v3.0 as a selectable local source (v0.20.0) and, on CGLabs, the
  **default** rainfall source for PRCP with a safe fall-back to v2.0 for
  uncovered years / off-CGLabs (v0.23.0).
- The CHIRPS→Earth-Engine fallback batched into a few requests — a full year of
  `PRCP` over a site window went from **>1 h to ~2 s** (v0.20.1).

**Don't crash — degrade gracefully**
- A malformed/truncated/partial local file falls back to downloading instead of
  failing the call (v0.19.0); the AgERA5 2023 Landing files were also
  reorganized to the flat yearly layout.
- Cross-year requests whose years come from different sources no longer trip
  `open_mfdataset` on ~1e-14 grid noise (v0.24.1).
- Seasonal fetch and bias-correction work for an AOI smaller than one SEAS5 1°
  cell (v0.24.2 empty-axis, v0.24.3 all-NaN downscale).
- Product NetCDF/GeoTIFF writes are atomic, so a failed write can't poison the
  cache (v0.24.4) — which also removed the `HDF5-DIAG` stderr spam.
- CDS downloads retry with backoff, so a network drop mid-download doesn't lose
  a whole cold forecast run (v0.25.0).

**Interface & feedback**
- `source=` / `weather_source=` accept a per-variable `{variable: source}`
  mapping, so one call can mix e.g. local rainfall + AgERA5 temperature
  (v0.21.0).
- Real elevation written into the DSSAT/ORYZA weather headers (v0.22.0).
- Progress bars for long fetches and per-point/per-tile loops, on stderr so the
  CLI's JSON stays clean (v0.24.0).
- Docs: README first-success needs `conda activate`; `--bbox w,s,e,n` form; the
  two point-output shapes; the ~32 GB container ceiling; `AGWISE_RAINFALL_SOURCE`
  and `AGWISE_CDS_RETRIES`.

Test suite grew from 172 to 207 passing (1 pre-existing env-only GDAL failure).

---

## 0.25.0 — resilient CDS downloads (retry + backoff)
- **A dropped CDS download no longer aborts the whole run.** Both the AgERA5 and
  SEAS5 drivers now go through a shared `cds.retrieve` helper that retries a
  failed `cdsapi` download with exponential backoff (default 3 attempts),
  deleting the partial file and rebuilding the client between tries. A cold
  seasonal-forecast run issues many CDS requests back to back, so a single
  network hiccup mid-download used to lose the entire run; it now retries just
  that request. Tunable with `AGWISE_CDS_RETRIES` (or `config.cds_retries`).
  New `agwise_data.cds` module. +4 tests; live-verified that a real AgERA5
  download still flows through the wrapper (279 s cold, correct cube).

## 0.24.4 — product writes are atomic (a failed write can't poison the cache)
- **Every product NetCDF/GeoTIFF is now written to a temp file and atomically
  renamed** (`get_climate`, `get_static`, `get_seasonal`, `get_modis`,
  `get_season`, `bias_correct`). Before, a crash mid-write left a half-written
  or zero-variable `.nc` that a later run treated as a cache hit and then failed
  to open (`expected one data variable, found []`) — a broken write poisoned
  every subsequent call for that product. The daily/static caches were already
  atomic; this extends the same guarantee to the user-facing products.
- **This also removes the `HDF5-DIAG … unable to open file` stderr spam** seen in
  the forecast QA run: it came from opening those leftover partial files, so with
  atomic writes there are none to trip over (a clean build now emits zero HDF5
  diagnostics). +1 test (`_write_nc_product` leaves nothing behind on failure).

## 0.24.3 — bias-corrected forecast no longer comes out all-NaN for a small AOI
- **`bias_correct` / `forecast_to_dssat` produced an all-NaN corrected cube when
  the SEAS5 forecast covered only one grid cell** (a small AOI on the coarse 1°
  grid — common for a district). `bias_correct_cube` downscaled the coarse
  hindcast/forecast onto the fine observation grid with linear interpolation
  only, and `xarray.interp` does not extrapolate: obs cells beyond the single
  source cell's centre stayed NaN, so QDM emitted all-NaN and every point was
  dropped with "no weather in season" (0 files written). The regrid now fills
  those NaNs with a nearest-cell downscaling (`reindex(method="nearest")`),
  which maps every obs cell to its nearest source cell — so the corrected cube
  is complete regardless of how few SEAS5 cells the AOI spans. Linear smoothing
  is still used where the source has ≥2 cells per axis. Found in the Addis QA
  run (first live `forecast_to_dssat`); the re-run then wrote all 50 points'
  bias-corrected DSSAT files in ~19 s. +1 test.

## 0.24.2 — seasonal-forecast fetch works for a small AOI on the coarse SEAS5 grid
- **`get_seasonal` (and the forecast → DSSAT chain) no longer fails for an AOI
  smaller than one SEAS5 cell.** SEAS5 is on a 1° grid; a sub-degree area of
  interest (e.g. a district around Addis Ababa) can fall entirely *between* cell
  centres, and `subset_bbox` — which kept only cells whose centre is inside the
  box — returned a zero-length axis, so writing the product raised
  `NetCDF: Invalid argument: (variable 'lat')`. `subset_bbox` now falls back to
  the nearest covering cell whenever a plain slice would empty an axis, so the
  cube is never degenerate. Behaviour is unchanged whenever the slice is
  non-empty (fine grids like CHIRPS/DEM are unaffected). Found in the Addis QA
  run — the first live SEAS5 fetch. +4 tests (`test_spatial.py`).

## 0.24.1 — fix cross-year fetch when years come from different sources
- **A multi-year request no longer fails when its years were fetched by
  different paths.** `open_years` combined the per-year cached files with
  `xr.open_mfdataset(combine="by_coords")`; when one year was read locally and
  another downloaded (e.g. the December 2022 slice from the local AgERA5 file
  and the 2023 slice from CDS because the local 2023 file was the unusable
  partial one), their lat/lon coordinates differed by ~1e-14 of floating-point
  noise, so `by_coords` treated them as distinct points and concatenated along
  `lon` — raising `Resulting object does not have monotonic global indexes
  along dimension lon`. It now concatenates strictly along `time` and takes the
  grid from the first year (`combine="nested", concat_dim="time",
  join="override"`), which is correct because all years of a (source, domain)
  share the same grid by construction. Same fix on the MODIS composite reader.
  Found in a new-user QA walkthrough on a Dec 2022 → Jul 2023 season. +1 test.

## 0.24.0 — progress bars for long fetches and per-point/per-tile loops
- **Long-running calls now show a progress bar** so a slow job is
  distinguishable from a hung one — the last gap from the new-user QA
  walkthrough. Instrumented: the parallel climate (variable, year) and
  soil/terrain fetches, MODIS composite pulls, the CHIRPS→Earth-Engine batches,
  Copernicus DEM tile reads, the per-point crop-model writers
  (`to_dssat`/`to_apsim`/`to_wofost`/`to_oryza`), and the two per-pixel compute
  loops (`bias_correct`, `smooth_ndvi`).
- **It never corrupts the CLI's output.** The bar is drawn on **stderr**; the
  CLI's single JSON line still goes to stdout untouched, so anything parsing it
  keeps working. By default the bar shows only when stderr is a terminal, so
  piped/cron/CI runs stay clean; force it with `AGWISE_PROGRESS=always` or
  silence it with `AGWISE_PROGRESS=never`.
- Uses `tqdm` when present (rate + ETA) and a built-in fallback otherwise, so it
  works with or without the dependency (now declared). New
  `agwise_data.progress` (`track`, `drain_futures`, `enabled`). +5 tests.

## 0.23.0 — CGLabs defaults rainfall to local CHIRPS v3.0 (user can still pick v2.0)
- **On CGLabs, `PRCP` now defaults to the local CHIRPS v3.0 series** staged in
  `Landing/Rainfall/chirps_v3` — fast, no network, no account — for the years it
  covers (1981–2023). For years outside that range, or off CGLabs, it falls back
  to the CHIRPS v2.0 default automatically, so a request never hard-fails on an
  unstaged year. The preference applies **only to rainfall and only when the
  caller pins no source**; every other variable is unchanged. This flows through
  everything that sources rainfall, including the crop-model writers — so
  `to_dssat(...)` with no `weather_source` now takes rainfall from local v3.0.
- **The user still chooses:** pass `source="chirps"` (v2.0) or
  `source="chirps_v3"` on any call to force a version, or set a site-wide default
  with the new `AGWISE_RAINFALL_SOURCE` env var (`config.rainfall_source`). Off
  CGLabs (no staged v3.0) the default is unchanged (CHIRPS v2.0).
- New `Config.rainfall_source` (auto-detects the staged v3.0 tree),
  `api._effective_source`/`_source_covers_years`. Docs updated (README §2.4,
  user_guide §4.2, cglabs_setup). +5 tests. Live-verified on Kisumu: default
  `PRCP` and `to_dssat` read `chirps_v3/2023.nc` locally (~1.7 s), `source=
  "chirps"` routes to v2.0.

## 0.22.0 — elevation in DSSAT/ORYZA weather headers + QA doc fixes
- **DSSAT and ORYZA weather files now carry the point's real elevation.** The
  `.WTH` `ELEV` field and the ORYZA CABO `Elevation:` line used to be `-99` / `0`
  even though the Copernicus DEM was available; `to_dssat`/`to_oryza` now read
  `ELEV` at each point (from the local DEM tiles on CGLabs — fast) and write it.
  It is **best-effort and only when sourcing statics from the layer**: a caller
  who injects their own `soil=` (offline/reuse mode) triggers no DEM fetch and
  keeps the `-99` sentinel, and a failed fetch logs a warning rather than
  blocking file generation. Live-verified: Kisumu points wrote 1189 m / 1152 m /
  1659 m into both engines. (`TAV`/`AMP` are computed from the supplied weather
  window as before — near-zero `AMP` is correct for equatorial sites.) +2 tests.
- **Docs:** documented the CLI `--bbox west,south,east,north` (comma-separated)
  form; clarified the two point-output shapes (`extract_points` long/tidy vs
  `extract_growing_season`/`extract_static_points` wide/ML-ready); and added a
  CGLabs memory-ceiling note (~32 GB container; run heavy jobs sequentially).
  All from the new-user QA walkthrough.

## 0.21.0 — per-variable source override (mix rainfall + temperature sources)
- **`source=` / `weather_source=` now also accept a `{variable: source}`
  mapping**, not just a single source id. A variable not in the mapping keeps
  its catalog default. This is what lets one crop-model call take rainfall from
  the fast local `chirps_v3` while temperature, radiation, humidity and wind
  come from AgERA5 — previously impossible, because a single forced source was
  applied to *every* weather variable and `to_dssat(..., weather_source=
  "chirps_v3")` failed with `Source 'chirps_v3' does not provide AGRO.TMAX`
  (found in the new-user QA walkthrough). The same mapping works for soil
  (`source={"CLAY": "isda"}`) and on the CLI (`--weather-source PRCP=chirps_v3`,
  `--source CLAY=isda,SOC=soilgrids`). A bare source id still forces all
  variables as before, and forcing a variable onto a source that lacks it still
  raises the same clear error. +3 tests.

## 0.20.1 — CHIRPS→Earth Engine is fast again (batched), and a docs fix
- **The CHIRPS Earth Engine fallback no longer takes an hour.** `_fetch_year_gee`
  used to pull one daily image per `computePixels` call — 365 sequential
  requests per year, which for a trial-site window ran **over an hour** (and
  dominated any `PRCP` request while the UCSB host stays 403-blocked). It now
  batches days into a few multi-band requests (each day a renamed band of a
  stacked image, sized so the response stays under the API's ~48 MB ceiling):
  **one request for a site/season window, a handful for the largest
  GEE-eligible domain.** Same CHIRPS v2.0 data, same cube. Live-verified: a
  full year of `PRCP` over a Kisumu window went from **>1 h (killed) to 1.7 s**;
  monthly totals show the correct bimodal Kenya pattern.
- **Docs:** the README §2.4 "first success" block now starts with
  `conda activate agwise_data`, so the very first `agwise-data …` command isn't
  a `command not found` for a new user who followed §2.2 but didn't keep the env
  active. Found in a new-user QA walkthrough.

## 0.20.0 — CHIRPS v3.0 as a selectable rainfall source (`source="chirps_v3"`)
- **New rainfall source:** the complete CHIRPS v3.0 daily series staged on
  CGLabs (`Landing/Rainfall/chirps_v3`, 1981-2023, 0.05 degree) is now a
  selectable source on every climate call: `source="chirps_v3"` in
  `get_climate`/`extract_points`/`extract_growing_season`/`rainy_days`/
  `get_season` (and `weather_source=` in the crop-model writers). CHIRPS
  v2.0 stays the default. Local-only by design (no network fetch), which
  also sidesteps the `data.chc.ucsb.edu` 403 blocking that limits the v2.0
  global-NetCDF path for large domains. New `catalog/chirps_v3.yaml` +
  `drivers/chirps_v3.py` (iSDA pattern: clear error without a local root).
- Live-verified: 10 Kenya points, 2023 — cold windowed read of the 24 GB
  yearly file in ~2 s; monthly totals correlate 0.93 with v2.0 and show the
  correct bimodal Kenya pattern. +3 tests (`test_chirps.py`).

## 0.19.0 — DEM served from the staged CGLabs tiles + bad local files fall back to downloading
- **Elevation/terrain now reuses the Copernicus GLO-30 tiles staged on CGLabs**
  (`/home/jovyan/common_data/cop30/raw`, full Africa, 3304 tiles — the *same*
  product the AWS path downloads, so no resolution trade-off). The DEM driver
  resolves each 1-degree tile against the staged copy first and only falls
  through to the AWS URL for tiles that are not staged (or unreadable), so a
  Kenya-sized request that used to hit OpenTopography/AWS per tile now reads
  local COGs in seconds. New `local` access block in `catalog/dem.yaml`
  (`tile_root`/`tile_pattern`); provenance gains `n_local_tiles`. Same
  `AGWISE_LOCAL_ROOT`/CGLabs-default switch as every other local source.
- **A staged local file that is unreadable, malformed, or truncated no longer
  fails the whole call.** The daily, static (soil), and MODIS-composite drivers
  now catch any local-read error — and, for daily data, a past year with
  missing days — log a warning naming the file and the reason, and download
  from the network source instead. Found via the 2023 AgERA5 Landing files,
  which were stored day-grouped (one netCDF group per day) and crashed every
  TMAX/TMIN/SRAD/RHUM/WIND request with `expected one data variable, found []`.
  (Those files have also been reorganized to the flat yearly layout on CGLabs,
  so they now serve locally like every other year; `TemperatureMean/AgEra/
  2023.nc` remains a partial 114-day file that correctly falls back to CDS.)
- **Terrain derivatives use half the memory:** slope/aspect/TPI/TRI now compute
  in float32 (differences of neighbouring 30 m cells are far above float32
  resolution). A 3°x3° cache domain derive dropped from ~4.4 GB to ~2.5 GB
  peak — `get_dem()` runs four derivatives in parallel, which could OOM a
  32 GB container before.
- +4 tests (`test_local.py`: driver-level fallbacks for daily/static/MODIS,
  staged-DEM tile reuse).

## 0.18.0 — CGLabs data folders are the built-in defaults (zero-config reuse)
- **A new user on CGLabs now reuses the already-downloaded data with no setup.**
  `Config.load()` defaults the download cache to the shared
  `…/Global_GeoData/Processed` and the reusable raw inputs to
  `…/Global_GeoData/Landing` whenever that tree exists — so `AGWISE_DATA_ROOT`
  and `AGWISE_LOCAL_ROOT` become *optional overrides*, not required setup.
  Off CGLabs (tree absent) it falls back to `~/agwise_data` with local reuse
  off, exactly as before. New `config.default_data_root()`/`default_local_root()`
  + `CGLABS_LANDING`/`CGLABS_PROCESSED` constants (edit those to relocate the
  whole team).
- **NFS file locking handled automatically:** `HDF5_USE_FILE_LOCKING=FALSE` is
  now set by default (the shared folders are on NFS), removing a step a new user
  had to know. A value you set yourself is left untouched.
- Verified live as a brand-new user (nothing exported): soil at 10 Kenya points
  came from `Landing` in **7 s** and CHIRPS rainfall in **6 s** (`Local source
  hit`), versus timing out while downloading before. +5 tests (`test_config.py`).
- Docs updated (README §2.2, cglabs_setup §2): on CGLabs there is nothing to
  export; env vars are for relocating only.

## 0.17.0 — upload your own area of interest (`geometry=` / `--aoi` / `aoi=`)
- **New region selector:** every gridded call (`get_climate`, `get_static` and
  its `get_dem`/`get_soil`/`get_cropmask` wrappers, `get_seasonal`, `get_modis`/
  `get_ndvi`, `get_season`, `smooth_ndvi`), plus `make_grid`, `bias_correct` and
  `forecast_to_dssat`, now accepts **`geometry=`** — a user-uploaded area of
  interest. Accepts a **file path** (shapefile, GeoJSON, GeoPackage — anything
  geopandas reads), a `GeoDataFrame`/`GeoSeries`, a shapely geometry, or a
  GeoJSON-like `dict`. CLI: **`--aoi <path>`**; R: **`aoi=`**.
- The AOI is reprojected to EPSG:4326 automatically (any input CRS), all
  features are kept (a multi-district / MultiPolygon selection clips as one
  AOI), and it takes priority over `country`/`bbox`. Products are cached under a
  stable, collision-free tag derived from the geometry (`aoi_<stem>_<hash>`), so
  two different shapes never share a cache entry.
- New `boundaries.load_aoi()` + `boundaries.aoi_tag()`; `_resolve_region` gained
  a `geometry` argument. Uses the geopandas/shapely stack the boundary loader
  already depends on — no new dependency. +7 tests. Verified live: an uploaded
  GeoJSON polygon clipped real Copernicus DEM (no credentials) to the exact
  polygon bounds.
- Docs updated: area-selection is now country / admin unit / bbox / **uploaded
  zone** / points (README, user guide §4.1 with Python/R/CLI examples, REFERENCE
  §6 `geometry` parameter on all region functions).

## 0.16.0 — smooth_ndvi (gap-fill + Savitzky-Golay of the MODIS NDVI stack)
- **New:** `smooth_ndvi(years, country=|bbox=, ...)` — turns the raw MODIS NDVI
  composites (with cloud/QA gaps left as NaN by the drivers) into the
  analysis-ready, smoothed time series the planting-date phenology workflow
  needs; the port of the legacy `get_MODISts_PreProc.R`. Per pixel the gaps are
  filled and a Savitzky-Golay filter (`window=9`, `polyorder=3` — the MODIS
  choice) is run along time; pixels with no valid observation stay NaN. Returns
  `{"RS.NDVI": {...}}` like `get_modis`, writing a `Smoothed_NDVI_*_SG` product.
- **Gap-fill method** is selectable via `gapfill=`:
  - `"linear"` (**default**) — linear interpolation along the *time*
    coordinate, edges carried from the nearest valid step. Tracks a peaked
    seasonal signal far better than the legacy mean (verified: RMSD-to-truth
    ~0.036 vs ~0.080 on a noisy season with ~20% gaps) and matches the MODIS
    driver's stated intent ("the downstream smoothing interpolates the gaps").
  - `"mean"` — per-pixel temporal mean, reproducing the legacy
    `substituteNA(type="mean")` for exact parity.
- **Optional cropland masking** (`cropmask=True`, default): the ESA WorldCover
  mask (`get_cropmask`) is aligned to the NDVI grid by nearest neighbour and
  non-cropland pixels are set to NaN before smoothing. `cropmask_source=`
  overrides the source; `cropmask=False` smooths every pixel.
- Non-default `window`/`polyorder` and a `"mean"` gap-fill are appended to the
  product name, so they never collide with a default-smoothed cache entry.
- New module `agwise_data.smoothing` (`smooth_stack`, `savgol_gapfill`,
  `apply_cropmask`); CLI `smooth-ndvi` and R `ad_smooth_ndvi`. +10 tests.
- Note (perf): the `"linear"` fill loops over pixels with gaps — fine at
  county/region scale; vectorize before continental-scale runs.

## 0.15.0 — soil hydraulics at points + Mehlich-3→Olsen P (DSSAT P block)
- **New:** `extract_static_points(..., derive=...)` adds pedotransfer columns,
  pulling in the base variables each needs:
  - `derive="hydraulics"` — Saxton & Rawls (2006) from CLAY/SAND/SOC, per
    depth: `PWP_<d>`, `FC_<d>`, `SAT_<d>` (cm³/cm³) and `KS_<d>` (mm/h). Same
    equations the crop-model writers already use (`writers/soil.saxton_rawls`),
    now exposed at points without writing a model file.
  - `derive="olsen_p"` — `OLSENP_<d>` (mg/kg) from Mehlich-3 extractable P via
    the new `mehlich3_to_olsen()` (`0.47·M3 + 2.4`; `calcareous=True` →
    `0.41·M3 + 1.1`; Steinfurth et al. 2023).
- **New:** `SOIL.EXTP` — Mehlich-3 extractable phosphorus (mg/kg), served from
  **iSDA** (`source="isda"`, layer `p`, depths `0-20cm`/`20-50cm`; Landing
  rasters are physical mg/kg, verified by sampling). SoilGrids has no P.
- **New:** the DSSAT `.SOL` writer now fills the **second-tier P block**
  (`SLPX` = Olsen P) when phosphorus is available — pass `write_sol(...,
  olsen_p=[...])` per layer, or let `to_dssat`/`write_sol` derive it from
  Mehlich-3 `EXTP_<depth>` columns on the soil frame. The extractable-P source
  has coarser depths than the six-layer profile, so each profile layer takes
  the `EXTP` value whose interval contains the layer midpoint (nearest by
  centre otherwise) — a transparent piecewise-constant depth mapping. With no
  P data the block is omitted (unchanged output).
  - The provisional 0-30 cm→multi-depth exponential P extrapolation in the
    legacy `get_geoSpatialData_V2_phosphorus.R` (flagged `# TODO: Revise`
    there) was **not** ported; the depth mapping above is used instead.
- CLI `extract-static` gains `--derive` / `--calcareous`; the R
  `ad_extract_static_points` gains `derive` / `calcareous`.

## 0.14.0 — iSDA as a selectable soil source
- **New:** soil can now be served from **iSDA Africa** as well as SoilGrids —
  choose per call with `source="isda"` (SoilGrids stays the default). New
  `catalog/isda.yaml` + `drivers/isda.py`. iSDA maps `SOIL.CLAY/SAND/SILT/PH/
  SOC/CEC/BDOD` to its `clay.tot.psa/sand.tot.psa/silt.tot.psa/ph.h2o/oc/ecec.f/
  db.od` layers at its two depths (`0-20cm`, `20-50cm`).
  - Units verified by inspection: texture/pH/OC/CEC are physical; **bulk
    density is stored ×100**, so `SOIL.BDOD` carries `conversion: d100`
    (121.7 → 1.217 g/cm³). Nitrogen is omitted — the `n.tot.ncs` values were
    implausible, so mapping it was left out rather than guessed.
  - iSDA is served **only from the local tree** (`AGWISE_LOCAL_ROOT`,
    `Soil/iSDA/isda_{var}_{depth}_v0.13_30s.tif`); with no local root it raises a
    clear error instead of attempting a download.
  - Note: iSDA's two depths differ from SoilGrids' six, so the crop-model
    writers (which expect the SoilGrids depth set) still use SoilGrids.
  - Verified live on Rwanda points against SoilGrids side by side.

## 0.13.0 — Local source extended to soil (SoilGrids) and MODIS
- The `AGWISE_LOCAL_ROOT` local source (0.12.0) now also covers the **soil**
  (`StaticDriver`) and **MODIS** (`ModisDriver`) code paths, not just the daily
  climate drivers.
  - **Soil:** `fetch_local_static` reads the SoilGrids depth rasters in
    `Soil/soilGrids/profile/{var}_{depth}_mean_30s.tif`, windowed to the region
    and stacked into the depth cube. These legacy tifs are already in physical
    units, so the `local` block is marked `preconverted: true` and the catalog
    conversion is skipped. Only the properties present locally are served; the
    rest fall back to the WCS. Verified live on Rwanda points (CLAY/SAND/SILT/
    SOC/PH/BDOD correct; urban points reflect the legacy tifs' per-property
    nodata gaps).
  - **MODIS:** `fetch_local_composite` assembles a composite year from
    per-composite GeoTIFFs at a **domain-tagged** `composite_path`
    (`modis/{domain}/{short}_{year}_{doy}.tif`) — the domain tag prevents ever
    serving one region's tiles for another. Legacy `Landing/MODISdata` files are
    region-baked to a single AOI/year and are *not* auto-matched; stage cubes in
    this layout to reuse them (MODIS otherwise stays on Earth Engine).
- New network-free tests for both paths.

## 0.12.0 — Local source: reuse already-downloaded geodata (no re-download)
- **New:** point `AGWISE_LOCAL_ROOT` at the AgWise `Global_GeoData/Landing`
  tree and the daily drivers read the matching legacy yearly file
  (`<Variable>/<Source>/<year>.nc`) — region-clipped — instead of downloading.
  It then flows through the normal harmonize + cache path, so the cached file
  is identical to one built from the network source. Added a `local` access
  block to the CHIRPS and AgERA5 catalog entries, a `drivers/local.py`
  (`fetch_local_year`), and a `local_root` config field. Opt-in: with the env
  var unset (default), nothing changes and drivers download as before.
  - Handles the legacy files' quirks: data variable named after the year, an
    extra `crs` variable, out-of-order time axis, and global extent (clipped
    before load to stay in memory). Legacy AgERA5 carries the same raw units as
    CDS (K, J m-2 day-1, %, m s-1), so the existing per-variable `conversion`
    applies unchanged.
  - Verified live: Rwanda 2020 RHUM/TMAX/SRAD read from the local tree in ~5 s
    (vs ~20 min from CDS), with K->degC and J->MJ conversions correct.

## 0.11.3 — Fix product cache-hit reopen for country-clipped data
- **Fix:** the second request for a **country-clipped** product failed with
  `ValueError: ... more than one data variable`. A geometry clip adds a
  `spatial_ref` CRS variable (rioxarray), so the cached product NetCDF has two
  variables, and the cache-hit path reopened it with `xr.open_dataarray`
  (single-variable only). Added `_open_product_da()` — picks the one real data
  variable, ignoring the CRS placeholder — and used it in all five affected
  functions (`get_climate`, `get_static`, `get_seasonal`, `get_modis`,
  `get_season`). First calls always worked (they return the in-memory cube);
  only repeat calls hit the bug. Found while ingesting local `common_data`
  files into the cache.

## 0.11.2 — AgERA5 v2; document CHIRPS-needs-Earth-Engine
- **AgERA5 → version 2.** ECMWF deprecated AgERA5 v1/v1.1 (no longer updated).
  The CDS request now uses `version: "2_0"` (same request schema and file
  structure — verified live). Note: the cache key is (source, variable, year,
  domain), *not* version, so already-cached years stay v1.1 until refetched —
  clear the AgERA5 cache dir to force a v2 re-pull.
- **Docs:** the README and `docs/credentials_setup.md` now state that while the
  UCSB host is 403-blocking, fetching CHIRPS `PRCP` needs Earth Engine
  credentials (the v0.11.1 fallback). The README "first success (no
  credentials)" fetch switched from CHIRPS to Copernicus **DEM elevation**,
  which needs no account regardless of the UCSB block.

## 0.11.1 — CHIRPS resilient to the UCSB host block
- **Fix:** `get_climate`/`get_season`/`extract_*`/`to_*` for `PRCP` no longer
  hard-fail when `data.chc.ucsb.edu` returns HTTP 403 (the UCSB host is
  currently blocking both the yearly NetCDF and the daily COGs). The CHIRPS
  driver now falls through **COG → Earth Engine mirror (`UCSB-CHG/CHIRPS/DAILY`)
  → NetCDF**: for a country/AOI-scale window it pulls CHIRPS daily from Earth
  Engine (needs GEE credentials + `AGWISE_GEE_PROJECT`, same as MODIS) and
  assembles the same harmonized mm/day cube. Quieted the expected GDAL 403
  INFO noise from the paced COG probing.

## 0.11.0 — ORYZA v3 crop-model inputs
- `to_oryza(...)` — write ORYZA v3 weather + soil files per point. Weather is
  the CABO format, one file per calendar year (`EXTE<n>/<code><n>.<yyy>`;
  `station, year, day, srad[kJ], tmin, tmax, vapr[kPa], wind, rain`; vapour
  pressure via FAO-56; missing `-99`). Soil is the 8-layer PADDY `.sol`
  (`soil_<n>.sol`): SoilGrids' six depths remapped onto ORYZA's fixed 8 layers
  and filled with the Saxton-Rawls hydraulics (WCST/WCFC/WCWP/WCAD, KST cm/day,
  CLAYX/SANDX fractions, BD, SOC/SON kg/ha), non-puddled template with
  overridable water-balance defaults. Also a `to-oryza` CLI subcommand and an
  `ad_to_oryza` R wrapper.

## 0.10.0 — WOFOST crop-model inputs
- `to_wofost(...)` — write WOFOST weather + soil-parameter CSVs per point
  (`EXTE<n>/weather_<n>.csv` with `date, srad, tmin, tmax, vapr, wind, prec`;
  `EXTE<n>/soil_<n>.csv` with the top-metre `SMW`/`SMFCF`/`SM0`/`K0` from the
  Saxton–Rawls hydraulics plus the WOFOST soil defaults). Sources relative
  humidity + wind on top of the crop-model four. Also a `to-wofost` CLI
  subcommand and an `ad_to_wofost` R wrapper.
- **Fix:** the weather vapour pressure is emitted in the physically-correct
  kPa. The legacy `5a_prepare_list_weather.r` multiplied `plantecophys::esat`
  (which returns **Pa**) by 1000, making `vapr` ~10⁶× too large; the correct
  actual vapour pressure is `(RH/100)·esat/1000`.

## 0.9.1
- **Fix:** add `scipy` to dependencies — `bias_correct` regrids the forecast
  with `xarray.interp`, which needs scipy; a clean install failed without it.

## 0.9.0 — Seasonal-forecast bias correction
- `bias_correct(...)` — Quantile Delta Mapping (Cannon et al. 2015) of a SEAS5
  forecast against the hindcast-vs-observation bias, per variable (additive for
  temperatures, multiplicative for PRCP/SRAD). Returns corrected cubes.
- `forecast_to_dssat(...)` — samples the corrected forecast at points, reduces
  the ensemble, and writes DSSAT weather+soil files.

## 0.8.0 — Spatial scaffolding
- `make_grid(...)` — regular point grid clipped to a country/admin boundary,
  each point tagged with `country`/`NAME_1`/`NAME_2`.
- `tag_admin(...)` — assign admin unit names to arbitrary points
  (field ↔ geospatial link).

## 0.7.0 — Crop-model input files
- `to_dssat(...)` / `to_apsim(...)` — write DSSAT `.WTH`+`.SOL` and APSIM
  `.met`+soil-table per point (Saxton–Rawls hydraulics), verified against the
  DSSAT/apsimx readers.

## 0.6.0 — Season slice
- `get_season(...)` — climate and/or NDVI sliced to a planting→harvest season,
  cross-year aware; region-cube or per-trial-point modes.

## 0.5.0 — Crop mask
- `get_cropmask(...)` — ESA WorldCover cropland mask (1/NaN) on the MODIS grid.

## 0.4.0 — MODIS vegetation indices
- `get_modis(...)` / `get_ndvi(...)` — MOD13Q1 + MYD13Q1 16-day NDVI/EVI
  composites via Earth Engine, Terra+Aqua interleaved (46/year).

## 0.3.0 — Seasonal forecasts + soil gap-fill
- `get_seasonal(...)` — SEAS5 seasonal forecast / hindcast cubes.
- `extract_static_points(..., fill_nearest_m=...)` — fill masked soil pixels
  from the nearest valid pixel, with a traceability column.

## 0.2.0 — Soil & terrain
- `get_static(...)` / `get_dem(...)` / `get_soil(...)` and
  `extract_static_points(...)` — SoilGrids 2.0 soil properties and Copernicus
  GLO-30 elevation + terrain derivatives (slope/aspect/TPI/TRI).

## 0.1.0 — Climate layer
- `get_climate(...)`, `extract_points(...)`, `extract_growing_season(...)` —
  harmonized CHIRPS + AgERA5 cubes and point/growing-season extraction, with
  the shared cache, the `agwise-data` CLI and the R wrapper.
