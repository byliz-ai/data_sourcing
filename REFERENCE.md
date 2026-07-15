# agwise-data — function reference

**The API reference:** every function with its parameters, output and a runnable
example. New here? Start with the **[README](README.md)** (install · credentials
· first fetch) and its [task table](README.md#what-do-you-want-to-do), which maps
each goal to the call that does it.

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

## Contents

- [1. Setup](#1-setup) — install, credentials (see also
  [credentials_setup](docs/credentials_setup.md) · [cglabs_setup](docs/cglabs_setup.md))
- [2. Canonical variables & units](#2-canonical-variables--units)
- [3. Function reference](#3-function-reference) —
  [gridded cubes](#31-gridded-cubes) ·
  [point extraction](#32-point-extraction-return-dataframes) ·
  [crop-model files](#33-crop-model-input-files-return-list-of-written-files) ·
  [spatial scaffolding](#34-spatial-scaffolding-return-dataframes) ·
  [bias correction](#35-seasonal-forecast-bias-correction)
- [4. R and command-line use](#4-r-and-command-line-use)
- [5. How it saves you time](#5-how-it-saves-you-time)

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

**Reuse already-downloaded data (skip the download):** set
`AGWISE_LOCAL_ROOT` to the AgWise `Global_GeoData/Landing` tree and the daily
drivers (CHIRPS, AgERA5) read the matching legacy yearly file, clip it to your
region and cache it — no network request. Opt-in; unset to always download.
See [docs/cglabs_setup.md](docs/cglabs_setup.md#performance-tuning-optional).

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

**`to_wofost(points, ...)`** — same arguments, writing `EXTE<n>/weather_<n>.csv`
and `soil_<n>.csv` for the R `meteor`/`Rwofost` model (which reads its inputs as
lists, so the deliverable is tidy CSVs). Weather columns are WOFOST's exact set
`date, srad, tmin, tmax, vapr, wind, prec` — SRAD in **kJ m⁻² day⁻¹** (the layer's
MJ ×1000), `vapr` the **actual vapour pressure in kPa** derived from relative
humidity and mean temperature, `wind` m s⁻¹, `prec` mm. The soil CSV is a long
`parameter,value,units,note` table: the moisture parameters `SMW`/`SMFCF`/`SM0`
(Saxton wilting-point/field-capacity/saturation) and `K0` (saturated
conductivity, cm day⁻¹), each a thickness-weighted mean over the top metre, plus
the site-independent WOFOST defaults (`RDMSOL`, `WAV`, `ZTI`, `IDRAIN`, `NOTINF`,
`SSI`, `SMLIM`). Sources relative humidity + wind on top of the crop-model four.
```python
from agwise_data import to_wofost
to_wofost(trials, planting_date="2021-01-01", harvest_date="2021-04-30",
          out_dir="WOFOST", station_col="site")
# -> [{"point", "dir", "weather": .../weather_1.csv, "soil": .../soil_1.csv}, ...]
```
> Note: the legacy `5a_prepare_list_weather.r` computed `vapr` with an errant
> `×1000` (`plantecophys::esat` returns **Pa**, so the correct kPa value is
> `(RH/100)·esat/1000`); `to_wofost` emits the physically-correct kPa vapour
> pressure.

**`to_oryza(points, ...)`** — same arguments, for the ORYZA v3 rice model.
Under `EXTE<n>/` it writes the CABO weather files `<code><n>.<yyy>` (**one per
calendar year** the season spans, so a cross-New-Year season yields two —
columns `station, year, day, srad, tmin, tmax, vapr, wind, rain`, SRAD in
kJ m⁻² day⁻¹, `vapr` the FAO-56 actual vapour pressure in kPa, missing values
`-99`) and the **8-layer PADDY** `soil_<n>.sol`. SoilGrids' six depths are
remapped onto ORYZA's fixed 8 layers (0.05 m ×6, 0.30, 0.40) and the
Saxton-Rawls hydraulics fill the retention/conductivity block (WCST/WCFC/WCWP/
WCAD m³ m⁻³, KST cm day⁻¹, CLAYX/SANDX fractions, BD g cm⁻³, SOC/SON kg ha⁻¹);
the water-balance switches follow the non-puddled template with overridable
defaults (`zrtms`, `wl0mx`, `wcli`=field capacity, `satav`, …). Sources relative
humidity + wind on top of the crop-model four.
```python
from agwise_data import to_oryza
to_oryza(trials, planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="ORYZA", station_col="site")
# -> [{"point", "dir", "weather": [.../AGWS1.021, ...], "soil": .../soil_1.sol}, ...]
```

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
`ad_extract_static_points`, `ad_to_dssat`/`ad_to_apsim`/`ad_to_wofost`/
`ad_to_oryza`, `ad_make_grid`/`ad_tag_admin`,
`ad_bias_correct`/`ad_forecast_to_dssat`.
Gridded wrappers return `terra::SpatRaster`s; point/writer wrappers return
data.frames.
```r
source("r/agwise_data.R")
rain <- ad_get_climate("PRCP", 2015:2024, country = "Rwanda", freq = "monthly")
soil <- ad_extract_static_points(trials, c("CLAY", "PH", "SOC"))
```

**CLI** (`agwise-data <subcommand>`): `get`, `extract`, `get-static`,
`get-seasonal`, `get-modis`, `get-cropmask`, `get-season`, `extract-static`,
`to-dssat`, `to-apsim`, `to-wofost`, `to-oryza`, `make-grid`, `tag-admin`,
`bias-correct`, `forecast-to-dssat`, plus `catalog` and `cache` for inspection. Each prints a
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
