# Which function downloads which source

A cheat-sheet for someone starting from zero: **pick your data on the
left, call the function on the right.** Every function fetches, harmonizes
(agreed names + units) and caches the data, and returns the same thing —
so once you learn one, you know them all.

| I want… | Source | Function | Credentials |
| --- | --- | --- | --- |
| Rainfall | CHIRPS | `get_climate("PRCP", …)` | none |
| Temperature / radiation / humidity / wind | AgERA5 | `get_climate("TMAX", …)` | CDS |
| Seasonal forecast / hindcast | SEAS5 | `get_seasonal(…)` | CDS |
| Soil properties (clay, pH, SOC, …) | SoilGrids | `get_soil(…)` | none |
| Elevation + slope/aspect/TPI/TRI | Copernicus DEM | `get_dem(…)` | none |
| Cropland mask | ESA WorldCover | `get_cropmask(…)` | GEE |
| NDVI / EVI vegetation indices | MODIS | `get_ndvi(…)` / `get_modis(…)` | GEE |
| Any of the above **at point locations** | — | `extract_points` / `extract_growing_season` / `extract_static_points` | same as the layer |

"CDS" = a free Copernicus account; "GEE" = your own Google Earth Engine
project. Both are one-time setup — see
[credentials_setup.md](credentials_setup.md). CHIRPS/SoilGrids/DEM need
nothing.

---

## The parameters every `get_*` shares

You only ever set a few things. Learn these once:

| Parameter | What it does | Examples |
| --- | --- | --- |
| `variables` | which variable(s) — short or canonical name, one string or a list | `"PRCP"`, `["TMAX","TMIN"]`, `"AGRO.PRCP"` |
| **region** — pick one: | | |
| `country=` | country name **or** ISO3 code (whole-country boundary) | `country="Rwanda"`, `country="RWA"` |
| `bbox=` | a raw box `(west, south, east, north)` in degrees | `bbox=(28.8, -2.9, 30.9, -1.0)` |
| `admin_level=` + `admin_name=` | a sub-national unit instead of the whole country | `admin_level=1, admin_name="Amajyaruguru"` |
| `years` | one year, a `range`, or a list (time-series sources only) | `2021`, `range(2015, 2025)`, `[2019, 2021]` |
| `out_format` | `"nc"` (default; the cache) and/or `"tif"` (GeoTIFF) | `out_format=["nc", "tif"]` |
| `source=` | override the default source for a variable (rarely needed) | `source="agera5"` |

**What every `get_*` returns:** a dict keyed by canonical variable name:

```python
{"AGRO.PRCP": {
    "short": "PRCP",
    "source": "chirps",
    "nc":   PosixPath(".../Daily_PRCP_2015_2024.nc"),   # always written (it is the cache)
    "tif":  PosixPath(".../Daily_PRCP_2015_2024.tif"),  # only if you asked for "tif"
    "data": <xarray.DataArray>,                          # ready to use, dims (time, lat, lon)
}}
```

So the usual pattern is:

```python
res = get_climate("PRCP", years=2021, country="Rwanda")
da  = res["AGRO.PRCP"]["data"]        # the xarray cube
```

Everything is cached under `AGWISE_DATA_ROOT`: the **second** call for the
same data returns in seconds, for you and for everyone on the shared root.

---

## 1. Rainfall — CHIRPS  ·  `get_climate`

No credentials. Variable: **`PRCP`** (mm/day).

```python
from agwise_data import get_climate

# daily rainfall cube for a country
rain = get_climate("PRCP", years=range(2015, 2025), country="Rwanda")

# monthly totals as NetCDF + GeoTIFF
rain = get_climate("PRCP", years=range(2015, 2025), country="Rwanda",
                   freq="monthly", out_format=["nc", "tif"])
```

```bash
agwise-data get --vars PRCP --country Rwanda --years 2015:2024 --freq monthly --format nc,tif
```

```r
r <- ad_get_climate("PRCP", years = 2015:2024, country = "Rwanda", freq = "monthly")
```

## 2. Temperature, radiation, humidity, wind — AgERA5  ·  `get_climate`

Needs a **CDS** account. Same function as rainfall — just different
variables:

| Short | Meaning | Units |
| --- | --- | --- |
| `TMAX` / `TMIN` / `TEMP` | max / min / mean 2 m air temperature | °C |
| `SRAD` | solar radiation | MJ m⁻² day⁻¹ |
| `RHUM` | relative humidity | % |
| `WIND` | 10 m wind speed | m s⁻¹ |

```python
clim = get_climate(["TMAX", "TMIN", "SRAD"], years=range(2015, 2025),
                   country="Rwanda", freq="monthly")
```

```bash
agwise-data get --vars TMAX,TMIN,SRAD --country Rwanda --years 2015:2024 --freq monthly
```

`freq` is `"daily"` (default) or `"monthly"`. PRCP is summed to monthly;
temperatures/radiation are averaged.

## 3. Seasonal forecast / hindcast — SEAS5  ·  `get_seasonal`

Needs **CDS**. One **initialization month** across a range of years; the
result has an ensemble axis, dims `(member, time, lat, lon)` where `time`
is the valid date.

```python
from agwise_data import get_seasonal

# February-initialized hindcast, 1993–2016, all ensemble members
hind = get_seasonal("PRCP", init_month=2, years=range(1993, 2017),
                    country="Rwanda")

# reduce the ensemble (required for GeoTIFF export)
fc = get_seasonal(["PRCP", "TMAX"], init_month=2, years=2024,
                  country="Rwanda", ensemble="mean", out_format=["nc", "tif"])
```

```bash
agwise-data get-seasonal --vars PRCP --init-month 2 --years 1993:2016 --country Rwanda
```

Key extra parameters: **`init_month`** (1–12, required) and
**`ensemble`** (`"members"` default / `"mean"` / `"median"`).

## 4. Soil properties — SoilGrids  ·  `get_soil`

No credentials. Every variable carries a **`depth`** dimension (6 standard
depths; all cached, `depths=` subsets what you get back).

| Variables | `CLAY SAND SILT` (%), `PH`, `SOC` `NITROGEN` (g/kg), `CEC`, `BDOD`, `CFVO`, `WV0010` `WV0033` `WV1500` |
| --- | --- |
| Depths | `0-5cm 5-15cm 15-30cm 30-60cm 60-100cm 100-200cm` |

```python
from agwise_data import get_soil

# default fertilizer-module set, all depths
soil = get_soil(country="Rwanda")

# just clay + pH, two depths
soil = get_soil(["CLAY", "PH"], country="Rwanda", depths=["0-5cm", "5-15cm"])
```

```bash
agwise-data get-static --vars CLAY,PH --country Rwanda --depths 0-5cm,5-15cm --format nc,tif
```

```r
s <- ad_get_soil(c("CLAY", "PH"), country = "Rwanda", depths = c("0-5cm", "5-15cm"))
```

## 5. Elevation + terrain — Copernicus DEM  ·  `get_dem`

No credentials. Elevation is fetched once; slope/aspect/TPI/TRI are derived
from it.

| Variables | `ELEV` (m), `SLOPE` `ASPECT` (°), `TPI`, `TRI` |
| --- | --- |

```python
from agwise_data import get_dem

dem = get_dem(country="Rwanda")                 # all five (default)
dem = get_dem(["ELEV", "SLOPE"], country="Rwanda")
```

```bash
agwise-data get-static --vars ELEV,SLOPE --country Rwanda --format nc,tif
```

## 6. Cropland mask — ESA WorldCover  ·  `get_cropmask`

Needs **GEE**. One layer, no `variables` argument. Returns a **1 =
cropland / NaN = other** mask on the **same grid as MODIS NDVI/EVI**, so
you mask a vegetation stack by multiplying.

```python
from agwise_data import get_ndvi, get_cropmask

ndvi = get_ndvi(years=2021, country="Rwanda")["RS.NDVI"]["data"]
crop = get_cropmask(country="Rwanda")["LC.CROPLAND"]["data"]
ndvi_cropland = ndvi * crop        # non-crop pixels become NaN
```

```bash
agwise-data get-cropmask --country Rwanda --format nc,tif
```

```r
cm <- ad_get_cropmask(country = "Rwanda")
```

## 7. NDVI / EVI vegetation indices — MODIS  ·  `get_ndvi` / `get_modis`

Needs **GEE**. Terra (MOD13Q1) + Aqua (MYD13Q1) 16-day composites at
~250 m, interleaved into the **46 images per year** the phenology workflow
expects. `get_ndvi` is `get_modis` fixed to NDVI.

```python
from agwise_data import get_ndvi, get_modis

ndvi = get_ndvi(years=2021, country="Rwanda", out_format=["nc", "tif"])
both = get_modis(["NDVI", "EVI"], years=[2021, 2022], country="Rwanda")
terra_only = get_modis("NDVI", years=2021, country="Rwanda", satellite="terra")
```

```bash
agwise-data get-modis --vars NDVI,EVI --years 2021:2022 --country Rwanda --format nc,tif
```

```r
nd <- ad_get_modis("NDVI", years = 2021:2022, country = "Rwanda")
```

Extra parameter: **`satellite`** — `"both"` (default, 46/year) /
`"terra"` / `"aqua"` (23/year each).

---

## Values at point locations (trial sites, plots)

When you have a CSV/data frame of coordinates rather than a region, use the
`extract_*` functions. They auto-detect `lon`/`lat` columns (or pass
`lon_col=`/`lat_col=`).

**Time series at points** — `extract_points`:

```python
from agwise_data import extract_points

ts = extract_points("trials.csv", ["PRCP", "TMAX"],
                    start="2020-01-01", end="2021-12-31", freq="monthly")
# long format: point, lon, lat, time, variable, value
```

**Per-trial growing-season climate** — `extract_growing_season`
(fertilizer-module format: `Precipitation_m1..mN`, `totalRF`,
`nrRainyDays`):

```python
from agwise_data import extract_growing_season

df = extract_growing_season("trials.csv", ["PRCP", "TMAX"],
                            planting_col="planting_date",
                            harvest_col="harvest_date")
```

**Soil / terrain / cropmask at points** — `extract_static_points`:

```python
from agwise_data import extract_static_points

df = extract_static_points("trials.csv", ["CLAY", "PH", "ELEV"],
                           depths=["0-5cm"])
# one column per variable/depth; points on masked pixels are filled
# from the nearest valid pixel within fill_nearest_m (default 1 km)
```

```bash
agwise-data extract-static --points trials.csv --vars CLAY,ELEV --out out.csv
```

---

## Full example — one country, every source

```python
from agwise_data import (get_climate, get_soil, get_dem,
                         get_ndvi, get_cropmask, get_seasonal)

country = "Rwanda"

rain    = get_climate("PRCP", years=range(2015, 2025), country=country, freq="monthly")
weather = get_climate(["TMAX", "TMIN", "SRAD"], years=range(2015, 2025), country=country, freq="monthly")  # CDS
soil    = get_soil(["CLAY", "PH", "SOC"], country=country, depths=["0-5cm", "5-15cm"])
dem     = get_dem(country=country)
ndvi    = get_ndvi(years=2021, country=country)                       # GEE
crop    = get_cropmask(country=country)                               # GEE
hind    = get_seasonal("PRCP", init_month=2, years=range(1993, 2017), country=country)  # CDS
```

The first run downloads; every later run (yours or a teammate's on the same
`AGWISE_DATA_ROOT`) is a cache hit. Nothing in the cache is ever modified,
only added.
