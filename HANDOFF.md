# agwise-data — the AgWise data-sourcing module

One call to **fetch, harmonize and cache** the climate, soil, terrain and
remote-sensing data every AgWise module needs — and to turn it into
**analysis-ready inputs** (season slices, DSSAT/APSIM files, bias-corrected
forecasts). A dataset is downloaded **once** into a shared cache with agreed
names and units (`AGRO.PRCP` in mm/day, `SOIL.CLAY` in %, …) and reused by
everyone afterwards.

```
 CHIRPS  AgERA5  SEAS5  SoilGrids  Copernicus-DEM  MODIS  ESA-WorldCover  geoBoundaries
    └────────┴───────┴───────┴──────────┴───────────────┴───────┴───────────────┘
                              │  agwise-data  │
        catalog → driver → harmonize → shared cache → products / points / files
                              │
     Python  get_climate(), extract_points(), to_dssat(), bias_correct(), …
     R       ad_get_climate(), ad_extract_points(), ad_to_dssat(), …
     CLI     agwise-data get | extract | to-dssat | bias-correct | …
```

**This is the function reference.** To install and configure the module,
start with the **[README](README.md)** (what you need · how to get the
credentials · how to install · how to use), with the detailed walkthroughs in
[docs/credentials_setup.md](docs/credentials_setup.md) and
[docs/cglabs_setup.md](docs/cglabs_setup.md). Below is every function with its
parameters, output and an example.

---

## 1. Setup

Install and credentials live in the **[README](README.md)** (concise) and:

- **[docs/credentials_setup.md](docs/credentials_setup.md)** — CDS + Earth
  Engine credentials, click-by-click, with troubleshooting.
- **[docs/cglabs_setup.md](docs/cglabs_setup.md)** — shared-server (CGLabs)
  install, the shared cache root, use from R, and performance tuning.

In short: `conda env create -f environment.yml` → `conda activate
agwise_data` → `pip install -e ".[all]"` → `pytest -q` to verify → set
`AGWISE_DATA_ROOT` to your cache folder. Add `~/.cdsapirc` (CDS) and run
`earthengine authenticate` + `export AGWISE_GEE_PROJECT=<id>` only for the
sources you use.

## 2. Canonical variables & units

Ask for variables by **short** name (`PRCP`), **canonical** name
(`AGRO.PRCP`) or the legacy label (`Precipitation`).

| Namespace | Short names | Units |
| --- | --- | --- |
| `AGRO.*` (climate) | `PRCP` | mm/day |
| | `TMAX`, `TMIN`, `TEMP` | °C |
| | `SRAD` | MJ m⁻² day⁻¹ |
| | `RHUM` | % |
| | `WIND` | m s⁻¹ |
| `SOIL.*` (SoilGrids) | `CLAY`, `SAND`, `SILT` | % |
| | `PH` | pH |
| | `SOC`, `NITROGEN` | g kg⁻¹ |
| | `CEC` | cmol(c) kg⁻¹ |
| | `BDOD` | g cm⁻³ |
| | `CFVO`, `WV0010`, `WV0033`, `WV1500` | vol % |
| `TOPO.*` (DEM) | `ELEV`, `SLOPE`, `ASPECT`, `TPI`, `TRI` | m / degree |
| `RS.*` (MODIS) | `NDVI`, `EVI` | unitless |
| `LC.*` | `CROPLAND` | 1 = cropland, NaN otherwise |

Soil depths: `0-5cm, 5-15cm, 15-30cm, 30-60cm, 60-100cm, 100-200cm` (point
columns use underscores, e.g. `CLAY_0_5cm`).

**Region** for any gridded/writer call is given by `country="Rwanda"` (or ISO3
`"RWA"`), optionally `admin_level=1/2` + `admin_name="..."`, **or**
`bbox=[west, south, east, north]`.

---

## 3. Function reference

Every Python function has an R wrapper (`ad_<name>`) and a CLI subcommand
(§4). Gridded functions return
`{canonical_var: {"nc": Path, "tif": Path|None, "data": xarray.DataArray}}`
(the NetCDF is always written — it is the cache; `out_format=["nc","tif"]`
adds a GeoTIFF). Point functions return a `pandas.DataFrame`. Writer functions
return a `list` of the files written.

### 3.1 Gridded cubes

**`get_climate(variables, years, country=|bbox=, freq="daily"|"monthly", out_format="nc", ...)`**
Harmonized daily/monthly climate cube `(time, lat, lon)`.
```python
from agwise_data import get_climate
res = get_climate("PRCP", years=range(2015, 2025), country="Rwanda",
                  freq="monthly", out_format=["nc", "tif"])
cube = res["AGRO.PRCP"]["data"]            # (time, lat, lon)
print(res["AGRO.PRCP"]["nc"])              # cached NetCDF path
```

**`get_static(variables, country=|bbox=, depths=None, ...)`** — soil/terrain
(no time axis; soil layers carry a `depth` dim).
Convenience: **`get_dem(...)`** (ELEV/SLOPE/ASPECT/TPI/TRI),
**`get_soil(...)`** (the fertilizer soil set).
```python
from agwise_data import get_soil, get_dem
get_soil(["CLAY", "PH", "SOC"], country="Rwanda", depths=["0-5cm", "5-15cm"])
get_dem(country="Rwanda")["TOPO.SLOPE"]["data"]     # (lat, lon)
```

**`get_seasonal(variables, init_month, years, country=|bbox=, ensemble="members"|"mean"|"median")`**
SEAS5 seasonal forecast / hindcast cube `(member, time, lat, lon)`, `time` =
valid date.
```python
from agwise_data import get_seasonal
res = get_seasonal(["PRCP", "TMAX"], init_month=2, years=range(1993, 2017),
                   country="Rwanda")          # 24-yr hindcast, all members
```

**`get_modis(variables, years, country=|bbox=, satellite="both"|"terra"|"aqua")`**
and **`get_ndvi(...)`** — MODIS 16-day vegetation-index composites
`(time, lat, lon)`, 46/year with `satellite="both"`. Needs Earth Engine.
```python
from agwise_data import get_ndvi
ndvi = get_ndvi(years=2021, country="Rwanda")["RS.NDVI"]["data"]
```

**`get_cropmask(country=|bbox=)`** — ESA WorldCover cropland mask (1/NaN) on
the MODIS grid, so `ndvi * mask` drops non-cropland. Needs Earth Engine.

**`get_season(variables, planting_date, harvest_date, country=|bbox=|points=, planting_col=, harvest_col=, freq="daily", satellite="both")`**
Climate **and/or** NDVI sliced to a growing season, **cross-year aware**
(e.g. Sep→Feb). Region mode → cube dict (`Season_*` products); points mode →
long DataFrame. Mixes `AGRO.*` and `RS.*` in one call. (Distinct from
`get_seasonal`, which is SEAS5 *forecasts*.)
```python
from agwise_data import get_season
# region: NDVI sliced to a cross-year season
get_season("NDVI", planting_date="2020-09-14", harvest_date="2021-02-28",
           country="Rwanda")
# points: per-trial seasons -> long df (point, lon, lat, time, variable, value)
get_season(["PRCP", "TMAX"], points=trials,
           planting_col="Pl_date", harvest_col="Hv_date")
```

### 3.2 Point extraction (return DataFrames)

**`extract_points(points, variables, start, end, freq="daily")`** — long time
series at points. `points` is a CSV path or DataFrame with lon/lat columns.
Returns columns `point, lon, lat, time, variable, value`.
```python
from agwise_data import extract_points
df = extract_points("trials.csv", ["PRCP", "TMAX"],
                    start="2021-01-01", end="2021-06-30")
```

**`extract_growing_season(points, variables, planting_col, harvest_col, legacy_names=True)`**
Per-trial growing-season climate in the fertilizer-ML wide format:
`<VAR>_m1..mN` monthly columns plus `totalRF` and `nrRainyDays` for rainfall.
```python
from agwise_data import extract_growing_season
out = extract_growing_season(trials, ["PRCP", "TMAX"],
                             planting_col="Pl_date", harvest_col="Hv_date")
# adds Precipitation_m1.., TemperatureMax_m1.., totalRF, nrRainyDays
```

**`extract_static_points(points, variables, depths=None, fill_nearest_m=1000)`**
Soil/terrain at points (wide), one column per depth (`CLAY_0_5cm`, …). Points
on masked pixels (urban/water NoData) are filled from the nearest valid pixel
within `fill_nearest_m` metres; each variable gets a `<VAR>_fill_m` column
(0 = own pixel, >0 = donor distance, NaN = nothing in range).
```python
from agwise_data import extract_static_points
soil = extract_static_points(trials, ["CLAY", "SAND", "SILT", "SOC", "PH"])
```

### 3.3 Crop-model input files (return list of written files)

**`to_dssat(points, planting_date=|planting_col=, harvest_date=|harvest_col=, out_dir=, station_col=, country=)`**
Writes, per point `n`, `out_dir/EXTE<n>/WHTE<n>.WTH` (daily
DATE/TMAX/TMIN/SRAD/RAIN + TAV/AMP header) and `SOIL.SOL` (layered profile
with Saxton–Rawls hydraulics). Fetches season weather + soil itself, or reuse
frames you already have via `weather=`/`soil=`.
```python
from agwise_data import to_dssat
to_dssat(trials, planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="DSSAT", station_col="site", country="Rwanda")
# -> [{"point", "dir", "wth": .../WHTE0001.WTH, "sol": .../SOIL.SOL}, ...]
```

**`to_apsim(points, ...)`** — same, writing `EXTE<n>/wth_loc_<n>.met` and
`soil_<n>.csv` (the per-layer soil table for the apsimx template).

### 3.4 Spatial scaffolding (return DataFrames)

**`make_grid(country=|bbox=, admin_level=0, admin_name=None, res_km=5, tag_admin_level=2)`**
Regular ~`res_km` point grid clipped to a boundary, each point tagged
`country, NAME_1, NAME_2`.
```python
from agwise_data import make_grid
grid = make_grid(country="Rwanda", res_km=5)     # lon, lat, country, NAME_1, NAME_2
```

**`tag_admin(points, country, admin_level=2)`** — add `country/NAME_1/NAME_2`
to your points by point-in-polygon (the field↔geospatial link).
```python
from agwise_data import tag_admin
tagged = tag_admin(trials, country="Rwanda")
```

### 3.5 Seasonal-forecast bias correction

**`bias_correct(variables, init_month, forecast_year, calib_years, country=|bbox=, window_days=None)`**
Quantile Delta Mapping of a SEAS5 forecast: learns the model bias from
hindcast-vs-observations over `calib_years` and corrects the `forecast_year`
forecast (additive for temperatures, multiplicative for PRCP/SRAD). Returns
`{var: {"short", "kind", "nc", "data"}}`, corrected cube
`(member, time, lat, lon)` on the observation grid.
```python
from agwise_data import bias_correct
bc = bias_correct(["PRCP", "TMAX", "TMIN", "SRAD"], init_month=2,
                  forecast_year=2024, calib_years=range(1993, 2017),
                  country="Rwanda")
```

**`forecast_to_dssat(points, init_month, forecast_year, calib_years, out_dir=, ensemble="mean"|"median", station_col=)`**
Bias-corrects the forecast, samples it at points, reduces the ensemble, and
writes DSSAT `.WTH`+`.SOL` (chains `bias_correct` into `to_dssat`).
```python
from agwise_data import forecast_to_dssat
forecast_to_dssat(trials, init_month=2, forecast_year=2024,
                  calib_years=range(1993, 2017), out_dir="DSSAT_forecast",
                  station_col="site")
```

Helper: **`rainy_days(daily_precip, threshold=2.0)`** — count of days ≥
threshold (the metric behind `nrRainyDays`).

---

## 4. R and command-line use

**R** (`source("r/agwise_data.R")`): every function above has an `ad_` wrapper
with the same arguments — `ad_get_climate`, `ad_extract_points`,
`ad_extract_growing_season`, `ad_get_static`/`ad_get_dem`/`ad_get_soil`,
`ad_get_seasonal`, `ad_get_modis`, `ad_get_cropmask`, `ad_get_season`,
`ad_extract_static_points`, `ad_to_dssat`/`ad_to_apsim`,
`ad_make_grid`/`ad_tag_admin`, `ad_bias_correct`/`ad_forecast_to_dssat`.
Gridded wrappers return `terra::SpatRaster`s; point/writer wrappers return
data.frames.
```r
source("r/agwise_data.R")
rain <- ad_get_climate("PRCP", 2015:2024, country = "Rwanda", freq = "monthly")
soil <- ad_extract_static_points(trials, c("CLAY", "PH", "SOC"))
```

**CLI** (`agwise-data <subcommand>`): `get`, `extract`, `get-static`,
`get-seasonal`, `get-modis`, `get-cropmask`, `get-season`, `extract-static`,
`to-dssat`, `to-apsim`, `make-grid`, `tag-admin`, `bias-correct`,
`forecast-to-dssat`, plus `catalog` and `cache` for inspection. Each prints a
JSON line describing the outputs.
```bash
agwise-data get --vars PRCP,TMAX --country Rwanda --years 2015:2024 --freq monthly
agwise-data to-dssat --points trials.csv --planting-date 2021-01-01 \
    --harvest-date 2021-04-30 --station-col site --out-dir DSSAT
agwise-data cache info
```

---

## 5. How it saves you time

- **Region-scoped** fetches: a country request downloads only its window.
- **Download once, shared**: the second request (anyone) is a cache hit;
  cached years are never re-fetched.
- **Parallel** downloads across (variable, year); reuse extractions across
  writers by passing `weather=`/`soil=` so you fetch once and write DSSAT +
  APSIM (or several engines) from the same data.
