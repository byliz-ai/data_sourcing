# agwise-data — the AgWise data access layer

One call to fetch, harmonize and cache the climate, soil and terrain data
every AgWise module needs, **while the CGIAR data hubs come online**. A
dataset is downloaded once into a shared cache, standardized to the AgWise
conventions, and reused by every module and every person afterwards.

```
                          ┌────────────────────────────────────┐
  CHIRPS (UCSB)   ───────▶│            agwise-data             │
  AgERA5 (CDS)    ───────▶│  catalog → driver → harmonize →    │──▶ Python: get_climate() / get_soil() / get_dem()
  SoilGrids (ISRIC) ─────▶│  shared cache → products/manifests │──▶ R:      ad_get_climate() / ad_get_soil() / ad_get_dem()
  Copernicus DEM (AWS) ──▶│                                    │──▶ CLI:    agwise-data get / get-static
  (+ future sources)      └────────────────────────────────────┘
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

# Static layers: soil properties (SoilGrids 2.0) and terrain (Copernicus DEM)
from agwise_data import get_soil, get_dem, extract_static_points

soil = get_soil(["CLAY", "PH", "SOC"], country="Rwanda", depths=["0-5cm", "5-15cm"])
dem = get_dem(country="Rwanda")        # ELEV + derived SLOPE/ASPECT/TPI/TRI
trials = extract_static_points(        # soil/topo at trial points, wide columns
    "trials.csv", ["ELEV", "SLOPE", "CLAY", "PH"], depths=["0-5cm"]
)   # adds ELEV, SLOPE, CLAY_0_5cm, PH_0_5cm, ...
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

# Replaces the soil/topography part of get_geoSpatialData:
soil <- ad_get_soil(c("CLAY", "PH"), country = "Rwanda", depths = "0-5cm")
dem  <- ad_get_dem(country = "Rwanda")   # ELEV, SLOPE, ASPECT, TPI, TRI
pts  <- ad_extract_static_points("trials.csv", c("ELEV", "SLOPE", "CLAY"))
```

## Use from the shell

```bash
agwise-data get --vars PRCP,TMAX --country Kenya --years 2015:2024 \
    --freq monthly --format nc,tif
agwise-data extract --points trials.csv --vars PRCP --planting-col Pl_date \
    --harvest-col Hv_date --out trials_climate.csv
agwise-data get-static --vars ELEV,SLOPE,CLAY --country Rwanda --format nc,tif
agwise-data extract-static --points trials.csv --vars ELEV,CLAY,PH \
    --depths 0-5cm --out trials_static.csv
agwise-data catalog list
agwise-data catalog stac chirps      # STAC Collection for Data Hub handoff
agwise-data cache info
```

## What you get

- **Canonical variables** — climate `AGRO.PRCP`, `AGRO.TMAX`, `AGRO.TMIN`,
  `AGRO.TEMP`, `AGRO.SRAD`, `AGRO.RHUM`, `AGRO.WIND`; terrain `TOPO.ELEV`,
  `TOPO.SLOPE`, `TOPO.ASPECT`, `TOPO.TPI`, `TOPO.TRI`; soil `SOIL.CLAY`,
  `SOIL.SAND`, `SOIL.SILT`, `SOIL.PH`, `SOIL.SOC`, `SOIL.NITROGEN`,
  `SOIL.CEC`, `SOIL.BDOD`, `SOIL.CFVO`, `SOIL.WV0010/0033/1500`; legacy
  names (`Precipitation`, `altitude`, `clay`, ...) still accepted everywhere.
- **Agreed units** — °C, mm/day, MJ m⁻² day⁻¹, %, g/kg, cmol(c)/kg
  (conversions — including SoilGrids' scaled integers — applied once, at
  ingestion, not in every module script).
- **Shared cache** — harmonized yearly files (`Daily_PRCP_2023.nc`) written
  once under `$AGWISE_DATA_ROOT`, with file-locking so concurrent users
  trigger a single download.
- **Provenance manifests** — every cached file has a `.meta.json` sidecar
  (source URL/request, dates, catalog version): the audit trail the data
  hubs will ingest.
- **Hub-compatible catalog** — each source is a YAML entry
  ([src/agwise_data/catalog/](src/agwise_data/catalog/)) carrying the
  Climate Data Hub metadata core, exportable as STAC.
- **Fast by design** — country-scale requests fetch only that country's
  window (CHIRPS via cloud-optimized GeoTIFF range reads, AgERA5 via
  bbox-cropped CDS requests); all (variable, year) fetches run in
  parallel; big files download over several connections. See the
  [performance model](docs/architecture.md#performance-model).

## Repository layout

```
src/agwise_data/        the Python package
  catalog/              dataset catalog (one YAML per source)
  drivers/              per-source fetchers (chirps, agera5, ...)
  harmonize.py          AgWise naming/units/aggregation conventions
  cache.py              shared-cache locking, atomic writes, manifests
  api.py                get_climate / get_soil / get_dem / point extraction
  terrain.py            slope/aspect/TPI/TRI from the cached DEM
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

v0.1 covered historical climate (CHIRPS rainfall, AgERA5 temperature /
radiation / humidity / wind) — the bottleneck flagged by every module at the
Nairobi AoW1 technical meeting. v0.2 adds the static layers: SoilGrids 2.0
soil properties (six depths, native 250 m via the ISRIC WCS) and the
Copernicus GLO-30 DEM with derived slope/aspect/TPI/TRI (windowed COG reads,
no full-tile downloads). The seasonal hindcast workflow (Jemal's
standardization proposal) and MODIS sources follow the same catalog + driver
pattern; see the [roadmap](docs/roadmap.md).
