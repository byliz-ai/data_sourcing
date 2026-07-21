# agwise-data — user workflow & interface (Documentation Sections 4–5)

This is the **how-to-use** guide. It assumes you have installed the module
([Section 2](../README.md#2-installation)) and, for the sources that need them,
set up credentials ([Section 3](credentials_setup.md)). You do **not** need to
read any source code.

Every task is the same four decisions:

> **1. an area → 2. some datasets → 3. a time period → 4. an output.**

Section 4 walks each decision; [Section 5](#5-user-interface--python--r--cli)
shows the same tasks in Python, R and the CLI. The full parameter list for any
function is in **[Section 6 / REFERENCE.md](../REFERENCE.md)**.

---

## 4. User workflow

### 4.1 Decision 1 — select the study area

Pass the area the same way to every gridded call (`get_climate`, `get_static`,
`get_modis`, `get_season`, …):

| You have… | Use | Example |
| --- | --- | --- |
| A **country** | `country=` (name or ISO3) | `country="Rwanda"` (or `"RWA"`) |
| An **administrative unit** (region/district) | `country=` + `admin_level=` + `admin_name=` — clips to that admin polygon | `country="Kenya", admin_level=1, admin_name="Nakuru"` |
| **Coordinates** (a rectangle) | `bbox=[west, south, east, north]` (CLI: `--bbox west,south,east,north`, comma-separated) | `bbox=[29.9, -2.1, 30.4, -1.7]` · `--bbox 29.9,-2.1,30.4,-1.7` |
| **Your own zone** (upload a polygon) | `geometry=` — a shapefile/GeoJSON path, a GeoDataFrame, a shapely geometry, or a GeoJSON dict | `geometry="my_zone.geojson"` |
| **Points** (specific locations) | `points=` a CSV/DataFrame with lon/lat columns | `points="trials.csv"` |

- `admin_level`: `0` = whole country, `1` = first level (region/province),
  `2` = second level (district). `admin_name` names the unit at that level.
- **`geometry` (upload your own area):** point it at a file you uploaded
  (`.shp`, `.geojson`, GeoPackage, …) or pass an in-memory geometry. Every
  feature is kept (so a multi-district or MultiPolygon selection clips as one
  AOI), and it is reprojected to lon/lat for you — no matter the file's CRS.
  `geometry` takes priority over `country`/`bbox`. On the CLI use `--aoi`, in R
  `aoi=`.
- **Points** are for the point-extraction and crop-model functions
  (`extract_points`, `extract_static_points`, `to_dssat`, …), which return a
  table or write files rather than a gridded cube.

```python
from agwise_data import get_climate
# whole country
get_climate("PRCP", years=2023, country="Rwanda")
# one district
get_climate("PRCP", years=2023, country="Kenya", admin_level=1, admin_name="Nakuru")
# a bounding box
get_climate("PRCP", years=2023, bbox=[29.9, -2.1, 30.4, -1.7])
# your own uploaded zone (shapefile / GeoJSON)
get_climate("PRCP", years=2023, geometry="my_zone.geojson")
```

The uploaded-zone option in all three interfaces:

```python
get_climate("PRCP", years=2023, geometry="my_zone.geojson")   # Python
```
```r
ad_get_climate("PRCP", 2023, aoi = "my_zone.geojson")          # R
```
```bash
agwise-data get --vars PRCP --years 2023:2023 --aoi my_zone.geojson   # CLI
```

### 4.2 Decision 2 — select the datasets

Ask for variables by **short name**; the tool routes each to the right source.
Full variable list and units are in
[REFERENCE → Conventions](../REFERENCE.md#conventions).

| Dataset | Variables (short) | Function | Source(s) | Account |
| --- | --- | --- | --- | --- |
| **Observed climate** | `PRCP` | `get_climate` | CHIRPS | none¹ |
| | `TMAX, TMIN, TEMP, SRAD, RHUM, WIND` | `get_climate` | AgERA5 | Copernicus CDS |
| **Seasonal forecast** | `PRCP, TMAX, TMIN, TEMP, SRAD` | `get_seasonal`, `bias_correct` | SEAS5 | Copernicus CDS |
| **Soil** | `CLAY, SAND, SILT, PH, SOC, NITROGEN, CEC, BDOD, CFVO` | `get_soil`, `get_static`, `extract_static_points` | SoilGrids (default) | none |
| | same set + `EXTP` | add `source="isda"` | iSDA (Africa) | none² |
| **Terrain / DEM** | `ELEV, SLOPE, ASPECT, TPI, TRI` | `get_dem`, `get_static` | Copernicus DEM | none |
| **MODIS vegetation** | `NDVI, EVI` | `get_modis`, `get_ndvi`, `smooth_ndvi` | MODIS (Terra+Aqua) | Google Earth Engine |
| **Cropland mask** | `CROPLAND` | `get_cropmask` | ESA WorldCover | Google Earth Engine |

¹ **On CGLabs, `PRCP` comes from the local CHIRPS v3.0 series by default** (the
complete 1981–2023 set is staged in `Landing`), so rainfall needs no account and
no network. For years it does not cover — or off CGLabs — `PRCP` falls back to
CHIRPS v2.0, which currently uses Earth Engine while the UCSB host is
403-blocked (so needs GEE there). Force a version any time with
`source="chirps"` (v2.0) or `source="chirps_v3"`; set a site-wide default with
`AGWISE_RAINFALL_SOURCE`. ² iSDA reads from the shared `Landing` folder
(`AGWISE_LOCAL_ROOT`).

Choose the **soil source** on any soil call: default SoilGrids (6 depths,
global) or `source="isda"` (iSDA Africa, 2 depths, adds extractable P).

**Mixing sources in one call.** `source=` (and `weather_source=` on the
crop-model writers, `--source`/`--weather-source` on the CLI) accepts either a
single source id — forced for every variable — *or* a per-variable mapping, so
one call can draw different variables from different sources. This is the way to
pull rainfall from the fast local CHIRPS v3 while temperature/radiation come
from AgERA5:

```python
# Python: a dict maps variable -> source; unlisted vars keep their default
to_dssat("trials.csv", planting_date="2023-03-01", harvest_date="2023-07-31",
         out_dir="DSSAT", station_col="site",
         weather_source={"PRCP": "chirps_v3"})   # temp/rad still AgERA5
```
```bash
# CLI: VAR=source pairs (comma-separated); a bare id still forces all vars
agwise-data to-dssat --points trials.csv --planting-date 2023-03-01 \
    --harvest-date 2023-07-31 --station-col site \
    --weather-source PRCP=chirps_v3 --out-dir DSSAT
```

### 4.3 Decision 3 — select the time period

| You want… | Use | Example |
| --- | --- | --- |
| A **continuous multi-year** period | `years=` (a year or range) | `years=range(2015, 2025)` |
| A **crop season** for a region | `get_season(planting_date=, harvest_date=)` | `planting_date="2020-09-14", harvest_date="2021-02-28"` |
| A **crop season per trial** (each row its own dates) | `extract_growing_season(planting_col=, harvest_col=)` or `get_season(..., points=, planting_col=, harvest_col=)` | `planting_col="Pl_date", harvest_col="Hv_date"` |

Seasons that **cross the calendar year** (e.g. Sep → Feb) are handled
automatically — just give the two dates.

```python
from agwise_data import get_season, extract_growing_season
# region, one season (cross-year OK)
get_season("NDVI", planting_date="2020-09-14", harvest_date="2021-02-28",
           country="Rwanda")
# per-trial seasons from a CSV
extract_growing_season("trials.csv", ["PRCP", "TMAX"],
                       planting_col="Pl_date", harvest_col="Hv_date")
```

### 4.4 Decision 4 — select the output

| You want… | Use | You get |
| --- | --- | --- |
| A **raw analysis-ready cube** for a region | `get_climate`, `get_static`, `get_modis`, `get_seasonal`, `get_season` | `{var: {"nc", "tif", "data"}}` — cached NetCDF (+ optional GeoTIFF) |
| A **table of values at points** | `extract_points`, `extract_growing_season`, `extract_static_points` | a `DataFrame` / CSV |
| **Crop-model input files** | `to_dssat`, `to_apsim`, `to_wofost`, `to_oryza` | files written under `out_dir` |
| Crop-model files from a **corrected forecast** | `forecast_to_dssat` | DSSAT files under `out_dir` |

Add a GeoTIFF next to the NetCDF with `out_format=["nc", "tif"]`. Reuse work
across engines by passing `weather=`/`soil=` frames you already extracted, so
the data is fetched once and written to DSSAT *and* APSIM.

---

## 5. User interface — Python / R / CLI

Every function is available three ways. Pick whichever fits your work; the
results are equivalent.

- **Python** — `from agwise_data import <function>`
- **R** — `source("r/agwise_data.R")`, then the `ad_<function>` wrapper
  (gridded wrappers return a `terra::SpatRaster`; point/writer wrappers return a
  `data.frame`). See [cglabs_setup §4](cglabs_setup.md#4-use-from-r-no-reticulate-needed).
- **CLI** — `agwise-data <subcommand>` (prints a JSON line describing the outputs)

> **Two shapes of point output.** `extract_points` returns a **long/tidy**
> table — one row per point × date × variable, columns `point, lon, lat, time,
> variable, value` — for time-series work. `extract_growing_season` returns a
> **wide, ML-ready** table — one row per point with per-month columns
> (`Precipitation_m1…`, and `totalRF`/`nrRainyDays` for rainfall; pass
> `--agwise-names` for `PRCP_m1…` style). `extract_static_points` is also wide
> (one row per point, a column per property × depth). Pick long for plotting a
> series, wide for a feature matrix.

### 5.1 The same tasks, three ways

**A. Monthly rainfall cube for a country**

```python
from agwise_data import get_climate
get_climate("PRCP", years=range(2015, 2025), country="Rwanda", freq="monthly")
```
```r
source("r/agwise_data.R")
ad_get_climate("PRCP", 2015:2024, country = "Rwanda", freq = "monthly")
```
```bash
agwise-data get --vars PRCP --country Rwanda --years 2015:2024 --freq monthly
```

**B. Soil at trial points (SoilGrids, gap-filled)**

```python
from agwise_data import extract_static_points
extract_static_points("trials.csv", ["CLAY", "SAND", "PH", "SOC"])
```
```r
ad_extract_static_points("trials.csv", c("CLAY", "SAND", "PH", "SOC"))
```
```bash
agwise-data extract-static --points trials.csv --vars CLAY,SAND,PH,SOC --out soil.csv
```

**C. Growing-season climate per trial (wide, ML-ready)**

```python
from agwise_data import extract_growing_season
extract_growing_season("trials.csv", ["PRCP", "TMAX"],
                       planting_col="Pl_date", harvest_col="Hv_date")
```
```r
ad_extract_growing_season("trials.csv", c("PRCP", "TMAX"),
                          planting_col = "Pl_date", harvest_col = "Hv_date")
```
```bash
agwise-data extract --points trials.csv --vars PRCP,TMAX \
    --planting-col Pl_date --harvest-col Hv_date --out season.csv
```

**D. DSSAT weather + soil files for each trial**

```python
from agwise_data import to_dssat
to_dssat("trials.csv", planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="DSSAT", station_col="site", country="Rwanda")
```
```r
ad_to_dssat("trials.csv", planting_date = "2021-01-01",
            harvest_date = "2021-04-30", out_dir = "DSSAT", station_col = "site")
```
```bash
agwise-data to-dssat --points trials.csv --planting-date 2021-01-01 \
    --harvest-date 2021-04-30 --station-col site --out-dir DSSAT --country Rwanda
```

**E. Gap-filled, smoothed NDVI for a region**

```python
from agwise_data import smooth_ndvi
smooth_ndvi(years=2021, country="Rwanda")
```
```r
ad_smooth_ndvi(2021, country = "Rwanda")
```
```bash
agwise-data smooth-ndvi --years 2021:2021 --country Rwanda
```

### 5.2 Function ↔ wrapper ↔ subcommand

| Python | R | CLI |
| --- | --- | --- |
| `get_climate` | `ad_get_climate` | `get` |
| `get_static` / `get_dem` / `get_soil` | `ad_get_static` / `ad_get_dem` / `ad_get_soil` | `get-static` |
| `get_cropmask` | `ad_get_cropmask` | `get-cropmask` |
| `get_seasonal` | `ad_get_seasonal` | `get-seasonal` |
| `get_modis` / `get_ndvi` | `ad_get_modis` | `get-modis` |
| `smooth_ndvi` | `ad_smooth_ndvi` | `smooth-ndvi` |
| `get_season` | `ad_get_season` | `get-season` |
| `extract_points` | `ad_extract_points` | `extract` |
| `extract_growing_season` | `ad_extract_growing_season` | `extract` (with `--planting-col`/`--harvest-col`) |
| `extract_static_points` | `ad_extract_static_points` | `extract-static` |
| `to_dssat` / `to_apsim` / `to_wofost` / `to_oryza` | `ad_to_dssat` / … | `to-dssat` / `to-apsim` / `to-wofost` / `to-oryza` |
| `make_grid` / `tag_admin` | `ad_make_grid` / `ad_tag_admin` | `make-grid` / `tag-admin` |
| `bias_correct` / `forecast_to_dssat` | `ad_bias_correct` / `ad_forecast_to_dssat` | `bias-correct` / `forecast-to-dssat` |

Inspect the catalog and cache from the CLI at any time:

```bash
agwise-data catalog list      # sources + variables available
agwise-data cache info        # what is cached, and where
```

Next: **[Section 6 / REFERENCE.md](../REFERENCE.md)** documents every parameter
of every function.
