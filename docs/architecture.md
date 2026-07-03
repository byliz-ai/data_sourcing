# Architecture

## The problem this solves

At the July 2026 AoW1 technical meeting in Nairobi, data sourcing was named
"the backbone of AgWise" and its biggest bottleneck: every module
re-downloads and re-processes its own data, each with different conventions
(point extractions vs raw rasters), and pipelines are not reusable year to
year. The CGIAR Climate Data Hub will eventually host curated,
cloud-optimized, AI-discoverable datasets — but it reached metadata v0.1
with two datasets the week before that meeting. AgWise needs a working data
layer *now* that will not be thrown away when the Hub matures.

## Design

Three principles:

1. **One access path.** Modules call `get_climate()` /
   `extract_growing_season()` (or their R/CLI equivalents) and never touch
   source URLs, credentials, unit conversions or file layouts.
2. **Fetch once, reuse everywhere.** All downloads land in a shared,
   file-locked cache keyed by `(source, domain, variable, year)`. The second
   person who asks for Kenya rainfall gets a cache hit, not a download.
3. **Hub-shaped from day one.** Sources are described in catalog YAMLs
   carrying the Climate Data Hub metadata core; every cached product has a
   provenance manifest. Migration to the Hub is a handoff, not a rewrite.

```
        catalog/*.yaml            drivers/                harmonize.py
   (what exists, license,   (how to fetch: HTTPS,    (AGRO.* names, units,
    extent, variables,  ──▶  CDS API, later GEE/  ──▶  dims, calendars,   ──▶  cache
    access recipes)          Zarr range requests)      monthly aggregation)
                                                                │
                          products (NetCDF/GeoTIFF + manifests) ▼
              api.py: get_climate() · extract_points() · extract_growing_season()
                        │                    │                    │
                     Python               R wrapper             CLI/cron
```

## Cache layout

Everything lives under `$AGWISE_DATA_ROOT` (shared on CGLabs):

```
raw/<source>/                          transient downloads (deleted unless keep_raw)
harmonized/<source>/<domain>/<VAR>/    Daily_PRCP_2023.nc  ← the shared asset
products/<region_tag>/                 Monthly_PRCP_2005_2024.nc / .tif
boundaries/                            geoBoundaries GeoJSON (cached)
```

* **Domains** bound what we store: `africa` by default (configurable), so
  the harmonized yearly file covers the whole domain and one file serves
  every African country. AgERA5 requests are cropped to the domain *at the
  CDS server*, unlike the legacy scripts that pulled global files per
  variable per year.
* **Harmonized files** are the unit of sharing: daily, one variable, one
  year, AgWise names/units, latitude ascending, compressed NetCDF.
* **Products** are cheap derivations (country crops, monthly aggregations,
  GeoTIFF exports) cached per region so repeated module runs cost nothing.
* **Concurrency**: creation of any cached file is guarded by a file lock and
  an atomic rename — concurrent users produce one download and never a
  half-written file.
* **Near-present data**: files for the current year are marked
  `partial: true` in their manifest and auto-refreshed after 30 days
  (configurable).

## Harmonization conventions

Adopted from the data sourcing group's standardization proposal:

| Canonical | Short | Legacy name | Units | Monthly agg |
| --- | --- | --- | --- | --- |
| AGRO.PRCP | PRCP | Precipitation | mm day⁻¹ | sum |
| AGRO.TMAX | TMAX | TemperatureMax | °C | mean |
| AGRO.TMIN | TMIN | TemperatureMin | °C | mean |
| AGRO.TEMP | TEMP | TemperatureMean | °C | mean |
| AGRO.SRAD | SRAD | SolarRadiation | MJ m⁻² day⁻¹ | mean |
| AGRO.RHUM | RHUM | RelativeHumidity | % | mean |
| AGRO.WIND | WIND | WindSpeed | m s⁻¹ | mean |

All spellings are accepted as input; storage uses the short names. Unit
conversions (K→°C, J→MJ) are declared per-source in the catalog and applied
exactly once, at ingestion.

## Provenance

Every cached file gets a `<file>.meta.json` sidecar: source id and catalog
version, the URL or CDS request that produced it, creation time, domain
bbox, processing (region, aggregation), and whether the year was partial.
This is deliberately the information the data hubs' metadata standard needs,
so hosted intermediate products arrive documented ("this is 5 km native —
do not over-interpret at 250 m" was the Nairobi agreement on honest
labelling).

## Migration path to the CGIAR data hubs

The Climate Data Hub catalogs datasets as YAML → STAC/OGC Records →
JSON-LD, hosted on cloud object storage in cloud-optimized formats. This
library is shaped so each piece maps onto that:

| agwise-data today | Climate Data Hub tomorrow |
| --- | --- |
| `catalog/*.yaml` (Hub metadata core + AgWise fields) | Hub metadata YAML (submit nearly as-is; `agwise-data catalog stac <id>` emits the STAC Collection) |
| `harmonized/` yearly NetCDF in the shared root | Cloud-optimized (Zarr/COG) copies on Hub storage — *gold/silver tier* |
| `.meta.json` manifests | Hub provenance/versioning fields |
| drivers (HTTPS/CDS) | a `hub` driver doing range requests against Hub storage — modules don't change a line |

The endgame: when a dataset graduates to the Hub, its catalog entry's
`access` block changes from `https/cds` to the Hub's cloud-optimized
endpoint, the local cache becomes a thin read-through cache, and every
module keeps calling the same three functions.

## Performance model

Measured baseline (v0.1): one year of Rwanda rainfall took ~18 minutes,
of which ≥95% was downloading the 1.1 GB global CHIRPS file to keep a
37×41-cell window — ~0.03% of the bytes moved. Four mechanisms fix this:

1. **Region-scoped fetching** (`fetch_scope: auto`, the default). A request
   covering a small area (≤ `region_max_area_deg2`, default a 20°×20° box)
   gets its own cache domain (`harmonized/<source>/rg_*`), so only that
   window is fetched: CHIRPS reads the daily COGs via windowed HTTP range
   requests (a Rwanda year ≈ 1 MB stored instead of 1.1 GB downloaded);
   AgERA5 asks the CDS for just that bbox, which also means much smaller
   extraction jobs in their queue. Any containing cache that is already
   complete — e.g. the Africa domain on CGLabs — is always preferred, and
   `fetch_scope: domain` forces continental-domain fetching for shared
   bulk servers. Region caches are rediscovered from disk across sessions.
2. **Parallel prefetch.** All (variable, year) fetches run in a thread pool
   (`max_workers`, default 4), so HTTP downloads and CDS queue waits
   overlap instead of accumulating serially — the win is largest for
   AgERA5, where each request can sit minutes in Copernicus' queue.
3. **Segmented downloads.** Large single files (the yearly CHIRPS NetCDF)
   are fetched over `download_parts` (default 4) parallel range
   connections when the server supports it.
4. **Aligned storage.** Harmonized files are written with chunk shapes
   matching how they are read back (time=92, lat/lon=128) and a fast
   compression level; reads never fight the storage layout.

**Politeness and safety.** Data providers rate-limit aggressive clients —
UCSB answers HTTP 403 and can temporarily ban an IP. The COG path is paced
(`cog_workers`, default 3, plus a small delay per read), treats 403 as
"server is blocking us" (falling back to the single-request yearly NetCDF)
rather than as missing data, and a driver-level invariant refuses to cache
a past year with missing days, so a mid-run block can never silently
truncate the shared cache.

## Why not X?

* **Why not keep per-module scripts?** They duplicate effort (the Nairobi
  estimate: data sourcing is ~half the time of producing a fertilizer
  recommendation), disagree on units/naming, and hardcode credentials and
  absolute paths.
* **Why not wait for the Hub?** It is at v0.1 with two datasets; AgWise has
  a 2026 season to run. This layer is the bridge — and doubles as AoW1's
  concrete requirements list for the Hub.
* **Why not host our own bucket now?** Hosting costs money and cross-program
  governance (SFP/Climate Action/MFL/DTA) is still being defined. Nothing
  here precludes it: point `AGWISE_DATA_ROOT` at a mounted bucket, or add a
  driver, when that decision lands.
