# agwise-data — the AgWise data access layer

One call to fetch, harmonize and cache the climate data every AgWise module
needs, **while the CGIAR data hubs come online**. A dataset is downloaded
once into a shared cache, standardized to the AgWise conventions, and reused
by every module and every person afterwards.

```
                       ┌────────────────────────────────────┐
  CHIRPS (UCSB) ──────▶│            agwise-data             │
  AgERA5 (CDS)  ──────▶│  catalog → driver → harmonize →    │──▶ Python: get_climate()
  (+ future sources)   │  shared cache → products/manifests │──▶ R:      ad_get_climate()
                       └────────────────────────────────────┘──▶ CLI:    agwise-data get
```

**Why**: today every AgWise module re-downloads and re-processes its own
data — global AgERA5 grabs repeated per variable per year, CHIRPS re-stacked
manually every season, no shared naming or units, results that are hard to
reproduce. This layer replaces that with a single, versioned, catalog-driven
path to the data, designed so its catalog and products migrate directly into
the CGIAR Climate Data Hub when it is ready (see
[docs/architecture.md](docs/architecture.md)).

## Install

```bash
git clone https://github.com/byliz-ai/data_sourcing.git
cd data_sourcing
conda env create -f environment.yml
conda activate agwise_data
pip install -e ".[all]"
```

Set the shared cache location (on CGLabs, point everyone at the same place):

```bash
export AGWISE_DATA_ROOT=/home/jovyan/common_data/agwise_data
```

For AgERA5 you need a free [CDS account](https://cds.climate.copernicus.eu)
and a `~/.cdsapirc` file — see [docs/cglabs_setup.md](docs/cglabs_setup.md).
**Never put API keys in scripts.**

## Use from Python

```python
from agwise_data import get_climate, extract_growing_season

# Monthly rainfall + max temperature cubes for Kenya, cached & shared.
res = get_climate(
    variables=["AGRO.PRCP", "AGRO.TMAX"],
    years=range(2005, 2025),
    country="Kenya",
    freq="monthly",
    out_format=["nc", "tif"],
)
res["AGRO.PRCP"]["data"]   # xarray.DataArray
res["AGRO.PRCP"]["tif"]    # path to the multi-band GeoTIFF (bands: 2005_01...)

# Growing-season climate for trial data (fertilizer ML format):
df = extract_growing_season(
    points="trials.csv",             # lon/lat + planting/harvest date columns
    variables=["Precipitation", "TemperatureMax", "SolarRadiation"],
    planting_col="Pl_date",
    harvest_col="Hv_date",
)
# adds Precipitation_m1..mN, TemperatureMax_m1..mN, totalRF, nrRainyDays
```

## Use from R

No reticulate, no conda wiring inside R — the wrapper calls the CLI:

```r
source("r/agwise_data.R")

# Replaces clim_genmonthlyAdmin & friends: monthly country cube as SpatRaster
r <- ad_get_climate(vars = c("PRCP", "TMAX"), years = 2005:2024,
                    country = "Kenya", freq = "monthly")

# Replaces the climate part of get_geoSpatialClimate: trial-point extraction
trials <- ad_extract_growing_season(
  points = "trials.csv", vars = c("Precipitation", "TemperatureMax"),
  planting_col = "Pl_date", harvest_col = "Hv_date"
)
```

## Use from the shell

```bash
agwise-data get --vars PRCP,TMAX --country Kenya --years 2015:2024 \
    --freq monthly --format nc,tif
agwise-data extract --points trials.csv --vars PRCP --planting-col Pl_date \
    --harvest-col Hv_date --out trials_climate.csv
agwise-data catalog list
agwise-data catalog stac chirps      # STAC Collection for Data Hub handoff
agwise-data cache info
```

## What you get

- **Canonical variables** — `AGRO.PRCP`, `AGRO.TMAX`, `AGRO.TMIN`,
  `AGRO.TEMP`, `AGRO.SRAD`, `AGRO.RHUM`, `AGRO.WIND`; legacy names
  (`Precipitation`, `TemperatureMax`, ...) still accepted everywhere.
- **Agreed units** — °C, mm/day, MJ m⁻² day⁻¹ (conversions applied once,
  at ingestion, not in every module script).
- **Shared cache** — harmonized yearly files (`Daily_PRCP_2023.nc`) written
  once under `$AGWISE_DATA_ROOT`, with file-locking so concurrent users
  trigger a single download.
- **Provenance manifests** — every cached file has a `.meta.json` sidecar
  (source URL/request, dates, catalog version): the audit trail the data
  hubs will ingest.
- **Hub-compatible catalog** — each source is a YAML entry
  ([src/agwise_data/catalog/](src/agwise_data/catalog/)) carrying the
  Climate Data Hub metadata core, exportable as STAC.

## Repository layout

```
src/agwise_data/        the Python package
  catalog/              dataset catalog (one YAML per source)
  drivers/              per-source fetchers (chirps, agera5, ...)
  harmonize.py          AgWise naming/units/aggregation conventions
  cache.py              shared-cache locking, atomic writes, manifests
  api.py                get_climate / extract_points / extract_growing_season
  cli.py                the `agwise-data` command
r/agwise_data.R         R wrapper (terra-friendly)
docs/                   architecture, CGLabs setup, module pipeline map, roadmap
examples/               drop-in replacements for the legacy module scripts
tests/                  network-free test suite (synthetic driver)
```

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Design, cache layout, Data Hub migration path |
| [docs/cglabs_setup.md](docs/cglabs_setup.md) | One-time setup on CGLabs, credentials, shared root |
| [docs/pipeline_map.md](docs/pipeline_map.md) | Module-by-module data pipeline map (Nairobi action #1) |
| [docs/roadmap.md](docs/roadmap.md) | Next sources: seasonal hindcast, soil, MODIS, GEE driver |

## Status

v0.1 covers historical climate (CHIRPS rainfall, AgERA5 temperature /
radiation / humidity / wind) — the bottleneck flagged by every module at the
Nairobi AoW1 technical meeting. The seasonal hindcast workflow (Jemal's
standardization proposal), soil/DEM and MODIS sources follow the same
catalog + driver pattern; see the [roadmap](docs/roadmap.md).
