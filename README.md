# agwise-data — the AgWise data-sourcing module

**For AgWise module developers and researchers** (working in Python *or* R) who
need analysis-ready climate, soil, terrain and remote-sensing inputs — without
each project re-implementing downloads, units and caching.

One call to **fetch, harmonize and cache** the data every AgWise module needs —
and to turn it into **analysis-ready inputs** (season slices, DSSAT/APSIM/WOFOST/
ORYZA files, bias-corrected forecasts). A dataset is downloaded **once** into a
shared cache with agreed names and units (`AGRO.PRCP` in mm/day, `SOIL.CLAY` in
%, …) and reused by everyone afterwards.

New here? Skim the two tables below (what it can do · where to read), then run
the [no-credential first success](#first-success-no-credentials-needed).

## Documentation map

| Document | Open it when you want to… |
| --- | --- |
| **README** (this page) | install and run your first fetch |
| 📖 **[REFERENCE.md](REFERENCE.md)** | look up a function — parameters, output, runnable example |
| ▶️ **[examples/](examples/)** | run a complete working script (Python + R) |
| 🔑 **[docs/credentials_setup.md](docs/credentials_setup.md)** | set up CDS + Earth Engine credentials, click-by-click, from zero |
| 🖥️ **[docs/cglabs_setup.md](docs/cglabs_setup.md)** | install on the shared server (CGLabs), use from R, tune performance |
| 🛠️ **[CONTRIBUTING.md](CONTRIBUTING.md)** | add a data source or contribute a change |
| 📜 **[CHANGELOG.md](CHANGELOG.md)** | see what changed between versions |

## What do you want to do?

| I want to… | Call | Details |
| --- | --- | --- |
| Get monthly/daily rainfall or temperature for a region | `get_climate("PRCP", …)` | [REF §3.1](REFERENCE.md#31-gridded-cubes) |
| Get soil or terrain **at my trial points** | `extract_static_points(…)` | [REF §3.2](REFERENCE.md#32-point-extraction-return-dataframes) |
| Choose the **soil source** (SoilGrids *or* iSDA) | `extract_static_points(…, source="isda")` | [REF §3.2](REFERENCE.md#32-point-extraction-return-dataframes) |
| Add **soil hydraulics** (PWP/FC/SAT/KS) or **Olsen P** at points | `extract_static_points(…, derive="hydraulics")` | [REF §3.2](REFERENCE.md#32-point-extraction-return-dataframes) |
| Get climate for **each trial's growing season** | `extract_growing_season(…)` / `get_season(…)` | [REF §3.2](REFERENCE.md#32-point-extraction-return-dataframes) |
| Build **DSSAT / APSIM / WOFOST / ORYZA** input files | `to_dssat` · `to_apsim` · `to_wofost` · `to_oryza` | [REF §3.3](REFERENCE.md#33-crop-model-input-files-return-list-of-written-files) |
| Get **NDVI/EVI** or a **cropland mask** | `get_ndvi(…)` / `get_cropmask(…)` | [REF §3.1](REFERENCE.md#31-gridded-cubes) |
| Get **gap-filled, smoothed NDVI** (Savitzky-Golay) | `smooth_ndvi(…)` | [REF §3.1](REFERENCE.md#31-gridded-cubes) |
| Bias-correct a **seasonal forecast** | `bias_correct(…)` / `forecast_to_dssat(…)` | [REF §3.5](REFERENCE.md#35-seasonal-forecast-bias-correction) |
| Make an **AOI grid** or tag points with **admin units** | `make_grid(…)` / `tag_admin(…)` | [REF §3.4](REFERENCE.md#34-spatial-scaffolding-return-dataframes) |
| Use it from **R** or the **command line** | `ad_*` wrappers / `agwise-data …` | [REF §4](REFERENCE.md#4-r-and-command-line-use) |
| **Reuse data already downloaded** (skip re-fetching from the network) | set `AGWISE_LOCAL_ROOT` | [cglabs_setup](docs/cglabs_setup.md#performance-tuning-optional) |
| Set up **credentials** | — | [credentials_setup](docs/credentials_setup.md) |
| Install on the **shared server** | — | [cglabs_setup](docs/cglabs_setup.md) |

> **Golden rule (shared servers):** *data is shared, credentials are personal.*
> Everyone points at the same shared cache, but each person keeps their **own**
> tokens in their **own** home (`chmod 600`) — never in the repo, a notebook, or
> the shared folder. Details in [credentials_setup.md](docs/credentials_setup.md).

---

## 1. What you need

| # | Requirement | Needed for |
| --- | --- | --- |
| 1 | **conda** (Miniconda/Anaconda) + git | everything (creates the `agwise_data` Python 3.10+ env) |
| 2 | A **cache folder** (`AGWISE_DATA_ROOT`) | everything — where downloads are cached (on CGLabs, the shared `Global_GeoData/Processed`; your outputs go under `Data/useCase_<name>` — see [cglabs_setup](docs/cglabs_setup.md)) |
| 3 | A free **Copernicus CDS** account + token | temperature/radiation/humidity/wind (AgERA5) and seasonal forecasts (SEAS5) |
| 4 | A free **Google Earth Engine** account + a Cloud project | MODIS NDVI/EVI (`get_ndvi`) and the crop mask (`get_cropmask`) |

Soil (SoilGrids), terrain (Copernicus DEM) and admin boundaries
(geoBoundaries) need **no account** — so you can install and get your
[first result](#first-success-no-credentials-needed) with no credentials at all.
You only add credentials for the sources you actually call.

> **Rainfall (CHIRPS) note:** CHIRPS normally needs no account, but the UCSB
> host (`data.chc.ucsb.edu`) is **currently returning HTTP 403**. When that
> happens the driver automatically falls back to CHIRPS on **Earth Engine**,
> which needs the *same* GEE credentials as MODIS (a Cloud project +
> `AGWISE_GEE_PROJECT`, see row 4 and [§2](#2-get-the-credentials)). So today,
> fetching `PRCP` effectively needs Earth Engine set up.

## 2. Get the credentials

Two free accounts, once per person. The full click-by-click (including a
from-zero path and every error we've hit) is in
**[docs/credentials_setup.md](docs/credentials_setup.md)**; the short version:

**Copernicus CDS** (for AgERA5 + SEAS5)
1. Register at <https://cds.climate.copernicus.eu> and copy your **Personal
   Access Token** (your profile page).
2. Accept the licence (one click) on the *AgERA5* and *SEAS5* dataset pages.
3. Save the token to `~/.cdsapirc`:
   ```
   url: https://cds.climate.copernicus.eu/api
   key: <your-personal-access-token>
   ```
   then `chmod 600 ~/.cdsapirc`.

**Google Earth Engine** (for MODIS + crop mask)
1. Register a free non-commercial Cloud project at
   <https://code.earthengine.google.com/register> and **note the project id**
   exactly as shown.
2. Authenticate once (on a machine with a browser):
   `python -m ee.cli.eecli authenticate` → this writes
   `~/.config/earthengine/credentials`.
3. Tell the tool your project: `export AGWISE_GEE_PROJECT=<your-project-id>`.

## 3. Install

```bash
git clone https://github.com/byliz-ai/data_sourcing.git
cd data_sourcing
conda env create -f environment.yml     # creates the 'agwise_data' env
conda activate agwise_data
pip install -e ".[all]"                  # package + CDS + Earth Engine clients
```

### Where your data lives — three folders, three jobs

On CGLabs the layer follows the existing AgWise folder layout. **Inputs are
shared, your outputs stay yours** — so three folders, each with one job:

| Folder (under `…/datasourcing/Data/`) | Holds | Shared? | You set it via |
| --- | --- | --- | --- |
| `Global_GeoData/Landing` | raw **global** source data, already downloaded | shared · **read-only** | `AGWISE_LOCAL_ROOT` |
| `Global_GeoData/Processed` | the **region** slices the tool downloads + harmonizes | shared · **read/write** | `AGWISE_DATA_ROOT` |
| `useCase_<Country>_<Name>/` | the **files you produce** (DSSAT/APSIM/…, CSVs) | your project | each writer's `out_dir` |

How they connect — a request flows top to bottom:

```text
  your call:  get_climate · extract_static_points · to_dssat · …
        │
        ▼   ① look in Landing (already downloaded, read-only)
  ┌────────────────────────────────────────────┐
  │  Global_GeoData/Landing                    │  raw · GLOBAL · shared · read-only
  │  AGWISE_LOCAL_ROOT                         │  here? → read + clip to region, NO download
  └────────────────────────────────────────────┘
        │  not in Landing
        ▼   ② else download just your region, then cache it
  ┌────────────────────────────────────────────┐
  │  Global_GeoData/Processed                  │  your REGION · shared · read/write
  │  AGWISE_DATA_ROOT                          │  cached once → everyone reuses it next time
  └────────────────────────────────────────────┘
        │  analysis-ready cube / points
        ▼   ③ you write your results
  ┌────────────────────────────────────────────┐
  │  useCase_<Country>_<Name>/result/          │  files YOU produce (DSSAT, CSVs, …)
  │  each writer's  out_dir                    │  your project · your outputs stay yours
  └────────────────────────────────────────────┘
```

**Why three?** each role needs different rules — raw inputs stay **read-only**,
the download cache is **shared + writable** (fetch a region once, everyone
reuses it), and your outputs stay **yours**; one folder can't be all three. Set
the two shared roots (outputs go per call via `out_dir`):

```bash
DATASOURCING=/home/jovyan/agwise-datasourcing/dataops/datasourcing/Data
export AGWISE_LOCAL_ROOT=$DATASOURCING/Global_GeoData/Landing    # raw inputs (read-only)
export AGWISE_DATA_ROOT=$DATASOURCING/Global_GeoData/Processed   # download cache (read/write)
export HDF5_USE_FILE_LOCKING=FALSE                              # Landing/Processed are on NFS
# Laptop / off CGLabs: leave AGWISE_LOCAL_ROOT unset; AGWISE_DATA_ROOT=~/agwise_data/cache
```

Install extras if you don't need everything: `.[geo]` (clipping + GeoTIFF),
`.[cds]` (AgERA5/SEAS5), `.[gee]` (MODIS + crop mask), `.[dev]` (test suite).
Shared-server install, use from **R**, and performance tuning are in
**[docs/cglabs_setup.md](docs/cglabs_setup.md)**.

### First success (no credentials needed)

Confirm the install works — no accounts required:

```bash
# 1. Offline: list the data sources and variables you can pull.
agwise-data catalog list

# 2. A real fetch that needs NO account at all (Copernicus DEM elevation,
#    cropped to a county, then cached). ~1-2 min the first time.
agwise-data get-static --vars ELEV --country Kenya --admin-level 1 --admin-name Nakuru
agwise-data cache info                   # see what landed in the cache
```

(Contributors verifying a change run the test suite instead — that needs the dev
extra: `pip install -e ".[dev]"` then `pytest -q`; see [CONTRIBUTING.md](CONTRIBUTING.md).)

Got a NetCDF path back from step 2? You're ready. Add credentials
([§2](#2-get-the-credentials)) for the sources that need them: CDS for AgERA5/
SEAS5, and **Earth Engine for MODIS, the crop mask, and — while UCSB is
blocking direct downloads — CHIRPS rainfall** (see the rainfall note above).
Once Earth Engine is set up, rainfall works too:
`agwise-data get --vars PRCP --country Kenya --admin-level 1 --admin-name Nakuru --years 2023:2023 --freq monthly`.

## 4. Use

Ask for variables by short name (`PRCP`, `TMAX`, `CLAY`, `NDVI`) and a region
(`country="Rwanda"` or `bbox=[w, s, e, n]`). Gridded calls return
`{var: {"nc": Path, "tif": Path|None, "data": xarray.DataArray}}` and cache
the NetCDF; point calls return a `pandas.DataFrame`.

**Python**
```python
from agwise_data import get_climate, extract_static_points, to_dssat

# Monthly rainfall cube for Rwanda (CHIRPS), NetCDF + GeoTIFF
res = get_climate("PRCP", years=range(2015, 2025), country="Rwanda",
                  freq="monthly", out_format=["nc", "tif"])
rain = res["AGRO.PRCP"]["data"]                     # (time, lat, lon)

# Soil at trial points (SoilGrids, gap-filled)
soil = extract_static_points("trials.csv", ["CLAY", "SAND", "PH", "SOC"])

# DSSAT weather + soil files for each trial point, ready to simulate
to_dssat("trials.csv", planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="DSSAT", station_col="site", country="Rwanda")
```

**R** (`source("r/agwise_data.R")` — every function has an `ad_` wrapper)
```r
source("r/agwise_data.R")
rain <- ad_get_climate("PRCP", 2015:2024, country = "Rwanda", freq = "monthly")
soil <- ad_extract_static_points("trials.csv", c("CLAY", "PH", "SOC"))
```

**Command line**
```bash
agwise-data get --vars PRCP --country Rwanda --years 2015:2024 --freq monthly
agwise-data cache info
```

▶️ **Complete runnable scripts are in [examples/](examples/)** (Python + R).
👉 **Every function — with all its parameters, expected output and a runnable
example — is in [REFERENCE.md](REFERENCE.md)**, and the [task table](#what-do-you-want-to-do)
above maps each goal to the call that does it.

## Contributing & changelog

Contributions welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** (dev setup,
running tests, adding a data source, the shared-cache ground rules). Release
history is in **[CHANGELOG.md](CHANGELOG.md)**.

## License

MIT — see [LICENSE](LICENSE).
