# agwise-data — the AgWise data access layer

One call to fetch, harmonize and cache the data every AgWise module needs.
A dataset is downloaded **once** into a shared cache with agreed names and
units (`AGRO.PRCP` in mm/day, `SOIL.CLAY` in %, ...), and reused by every
module and every person afterwards.

```
  CHIRPS (UCSB)      ───▶ ┌─────────────────────────────────┐ ──▶ Python  get_climate() / get_soil() / get_dem() / get_seasonal()
  AgERA5 (CDS)       ───▶ │           agwise-data           │ ──▶ R       ad_get_climate() / ad_get_soil() / ...
  SEAS5 (CDS)        ───▶ │ catalog → driver → harmonize →  │ ──▶ CLI     agwise-data get / get-static / get-seasonal
  SoilGrids (ISRIC)  ───▶ │ shared cache → products         │
  Copernicus DEM     ───▶ └─────────────────────────────────┘      + sentinel/script1 (GEE) for the phenology stack
```

## Install & setup (2 minutes on CGLabs)

```bash
git clone https://github.com/byliz-ai/data_sourcing.git
cd data_sourcing
conda env create -f environment.yml && conda activate agwise_data
pip install -e ".[all]"
```

**Point at the shared cache** (add to `~/.bashrc`):

```bash
export AGWISE_DATA_ROOT=/home/jovyan/common_data/agwise_data
```

That single line is how you take advantage of what is already on CGLabs:
everyone points at the same root, so the first person who asked for
*Rwanda rainfall 2005–2024* already paid the download — your call returns
in seconds from cache. Anything you fetch is likewise reused by the next
person. Nothing in the cache is ever modified, only added.

## Credentials (per user, one time)

| You want | You need | Setup |
| --- | --- | --- |
| CHIRPS rainfall, SoilGrids, DEM/terrain | nothing | — |
| AgERA5 (temp/radiation/etc.), SEAS5 forecasts | free [CDS account](https://cds.climate.copernicus.eu) | put your token in `~/.cdsapirc` (below), accept the dataset licence on its CDS page |
| Sentinel stack (`sentinel/script1`), future MODIS | Google Earth Engine account | `earthengine authenticate` once in a terminal |

`~/.cdsapirc` (then `chmod 600 ~/.cdsapirc`):

```
url: https://cds.climate.copernicus.eu/api
key: <your-personal-access-token>
```

**Never put keys inside scripts.** Details: [docs/cglabs_setup.md](docs/cglabs_setup.md).

## How each module runs it

### Fertilizer — trial-point climate, soil and terrain

```python
from agwise_data import extract_growing_season, extract_static_points

# per-trial monthly climate between planting and harvest (legacy ML columns:
# Precipitation_m1..mN, totalRF, nrRainyDays)
df = extract_growing_season("trials.csv", ["Precipitation", "TemperatureMax"],
                            planting_col="Pl_date", harvest_col="Hv_date")

# soil + terrain at the same points (columns ELEV, SLOPE, CLAY_0_5cm, ...);
# points on SoilGrids urban/water NoData are filled from the nearest valid
# pixel within 1 km, with a <VAR>_fill_m traceability column
pts = extract_static_points("trials.csv", ["ELEV", "SLOPE", "CLAY", "PH"],
                            depths=["0-5cm"])
```

```r
source("r/agwise_data.R")
df  <- ad_extract_growing_season("trials.csv", c("Precipitation", "TemperatureMax"),
                                 planting_col = "Pl_date", harvest_col = "Hv_date")
pts <- ad_extract_static_points("trials.csv", c("ELEV", "SLOPE", "CLAY", "PH"),
                                depths = "0-5cm")
```

### Planting date & cultivar — SEAS5 hindcast + matching observations

Hindcast and observations come out with the **same variable names, units
and conventions**, ready to pair for bias correction and DSSAT:

```python
from agwise_data import get_seasonal, get_climate

hind = get_seasonal("PRCP", init_month=2, years=range(1993, 2017),
                    country="Rwanda")            # (member, time, lat, lon)
obs  = get_climate("PRCP", years=range(1993, 2017),
                   country="Rwanda", freq="daily")
```

```r
h <- ad_get_seasonal("PRCP", init_month = 2, years = 1993:2016, country = "Rwanda")
o <- ad_get_climate("PRCP", years = 1993:2016, country = "Rwanda", freq = "daily")
```

### Phenology / planting-date detection — Sentinel-1/2 smoothed stack

`sentinel/script1_Download_Stack_Smooth.py` (in this repo — the source of
truth) builds the multi-index smoothed stack the phenology scripts consume.
Cross-year seasons (e.g. Rwanda season B, Sep→Feb) are supported; bands are
labelled by real date (`EVI_20200914_SG`). Needs GEE auth (table above).

```python
from script1_Download_Stack_Smooth import Download_Stack_Smooth  # run from sentinel/

result = Download_Stack_Smooth(
    country="Rwanda", useCaseName="RAB", crop="Maize",
    level=1, admin_unit_name="Amajyaruguru",
    Planting_year=2020, Planting_month="September",
    Harvesting_year=2021, Harvesting_month="February",   # cross-year: +1
    gee_project="<your-gee-project>", base_dir="./sar_pheno",
)
```

### Any module — gridded climate / soil / DEM cubes

```python
from agwise_data import get_climate, get_soil, get_dem

cube = get_climate(["PRCP", "TMAX"], years=range(2005, 2025),
                   country="Kenya", freq="monthly", out_format=["nc", "tif"])
soil = get_soil(["CLAY", "PH", "SOC"], country="Rwanda", depths=["0-5cm", "5-15cm"])
dem  = get_dem(country="Rwanda")     # ELEV + SLOPE/ASPECT/TPI/TRI
```

Same from the shell (what the R wrapper calls under the hood):

```bash
agwise-data get         --vars PRCP,TMAX --country Kenya --years 2015:2024 --freq monthly
agwise-data get-seasonal --vars PRCP --init-month 2 --years 1993:2016 --country Rwanda
agwise-data get-static  --vars ELEV,SLOPE,CLAY --country Rwanda --format nc,tif
agwise-data extract-static --points trials.csv --vars ELEV,CLAY --out out.csv
agwise-data catalog list && agwise-data cache info
```

## Good to know

- **Legacy names still work** everywhere (`Precipitation`, `altitude`, `clay`, ...).
- Every cached file has a `.meta.json` **provenance manifest** (source
  URL/request, dates, catalog version) — the audit trail for the CGIAR
  data hubs; catalog entries export as STAC (`agwise-data catalog stac chirps`).
- Country-scale requests fetch **only that country's window**, in parallel;
  a new season is just `years=range(..., 2027)` — files are append-only.
- 54 network-free tests run in CI on every push.

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/cglabs_setup.md](docs/cglabs_setup.md) | One-time CGLabs setup, credentials, shared root |
| [docs/architecture.md](docs/architecture.md) | Design, cache layout, Data Hub migration path |
| [docs/pipeline_map.md](docs/pipeline_map.md) | Module-by-module data pipeline map |
| [docs/roadmap.md](docs/roadmap.md) | What is done (climate, soil+DEM, SEAS5) and what is next (MODIS/GEE) |
| [sentinel/README.md](sentinel/README.md) | The Sentinel-1/2 phenology input generator |
