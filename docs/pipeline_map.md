# AgWise data pipeline map (module by module)

Action #1 from the Nairobi AoW1 technical meeting (1 Jul 2026): document
which datasets each module uses, where each comes from, and where the
bottlenecks are — so the Climate Data Hub team can see where to help. This
table is seeded from the module scripts reviewed while building this
library; **module leads should correct and complete it** (PRs welcome).

## Fertilizer recommendations & soil health

| Dataset | Source | How it is accessed today | Pain points | Status in agwise-data |
| --- | --- | --- | --- | --- |
| Rainfall (daily/monthly) | CHIRPS v2.0 | Yearly NetCDF pre-downloaded to `~/common_data/chirps_af`; per-country crop+mask+monthly stacking re-coded per use case (`clim_genmonthlyAdmin.R`) | Manual yearly refresh; every use case re-crops and re-stacks; file naming drift; UCSB has meanwhile *removed* the africa_daily NetCDF path those scripts relied on | ✅ `get_climate(... freq="monthly")` |
| Temperature max/min/mean, solar radiation, humidity, wind | AgERA5 via CDS | `ecmwfr` requests of **global** files, one script block per variable per year (`chirps_download.R`); API key hardcoded in script | Global downloads for country needs; copy-paste per year/variable; credential leakage; zip/unzip juggling | ✅ `get_climate()` (domain-cropped at CDS, no keys in code) |
| Growing-season climate at trial points | CHIRPS + AgERA5 monthly/daily stacks | `get_geoSpatialClimate` loops over growing periods, indexes monthly layers by position, cross-year seams handled by hand | Fragile layer-index arithmetic (several bugs found and fixed in 2026); slow; depends on pre-built local stacks | ✅ `extract_growing_season()` (continuous time axis; produces the same `*_m1..mN`, `totalRF`, `nrRainyDays` columns) |
| Soil & topography at trial points | EthioSIS / SoilGrids / SRTM rasters | `terra::extract` on locally stored projected rasters | Rasters assembled ad hoc per country | 🔜 roadmap (soil/DEM driver) |
| Field trial data | Wheat/maize trials CSVs | Local CSVs under `~/shared-data` | No shared schema/QC | Out of scope v0.1 (agronomic data workstream) |

## Planting date & cropping-systems optimisation

| Dataset | Source | How it is accessed today | Pain points | Status in agwise-data |
| --- | --- | --- | --- | --- |
| NDVI time series (phenology → planting dates) | MODIS MOD13Q1/MYD13Q1 (+VIIRS) via GEE | Python toolkit in `CGIAR-AgWise/agwise_data_sourcing` driven by YAML, called from R via `reticulate` | GEE project/auth per user; conda/reticulate wiring; outputs not in shared cache | ✅ built (v0.4) + **live-verified 2026-07-07** (Rwanda 2021): `get_ndvi()`/`ad_get_modis()`, Terra+Aqua interleaved (46/year), shared cache + manifests |
| Historical observations for bias correction / DSSAT | AgERA5, CHIRPS | Separate scripts per use case, inconsistent grids/extents (see Jemal's standardization proposal) | Repeated downloads; grids/calendars/naming inconsistent across use cases | ✅ historical part: `get_climate()` on a fixed domain gives identical reference data to all use cases |
| Seasonal hindcast/forecast (SEAS5) | ECMWF via CDS | Earthkit-based scripts in `agwise-planting-date-and-cultivar` (`AgWise_download.py` etc.) | One-time hindcast download + standardization not yet centralized | ✅ `get_seasonal()` (v0.3): per-year cached `Seasonal_<VAR>_i<MM>_<year>.nc`, valid-date time axis, AGRO units matching `get_climate` observations for bias correction (live-verified on CDS 2026-07-04) |
| Crop mask | ESA WorldCover via GEE | GEE crop-mask class in the legacy toolkit (`get_ESACropland_fromGEE`), mask uploaded per use case by hand | Tied to GEE flow; manual export/upload per use case | ✅ built (v0.5) + **live-verified 2026-07-07**: `get_cropmask()`/`ad_get_cropmask()`, ESA WorldCover class 40 → cropland fraction thresholded to a 1/NaN mask **on the MODIS grid** (straight multiply), shared cache + manifests |

## Cross-cutting bottlenecks (as named in Nairobi)

1. **Every module fetches its own data** → this library's shared cache is
   the direct fix; adoption = replacing script blocks with the calls above.
2. **No shared conventions** (names, units, grids) → `harmonize.py` is now
   the single place conventions live; grid alignment across resolutions
   (soil 150 m vs climate ~1–5 km) is on the roadmap and needs a decision on
   a common target grid before it is automated.
3. **Pipelines not reusable year to year** → yearly harmonized files are
   append-only: a new season is `years=range(..., 2027)`; partial current
   years auto-refresh.
4. **Compute (CG Labs) is slow, no GPUs** → out of scope for this library,
   but the pre-computed scenario-library idea from the meeting would sit
   naturally in `products/` with manifests.
