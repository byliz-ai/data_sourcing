# agwise-data — function reference (Documentation Section 6)

Every public function, with **all its parameters** — type, required/optional,
default, meaning, allowed values — and a runnable example. New here? Do
[Sections 1–5](README.md) first (install → credentials → workflow); this page is
the lookup you return to.

```
 CHIRPS  AgERA5  SEAS5  SoilGrids  iSDA  Copernicus-DEM  MODIS  ESA-WorldCover  geoBoundaries
    └───────┴──────┴───────┴───────┴─────────┴──────────────┴───────┴──────────────┘
                              │  agwise-data  │
        catalog → driver → harmonize → shared cache → products / points / files
```

## Contents

- [Conventions](#conventions) — how to read the tables (region, variables, returns)
- [6.1 Gridded cubes](#61-gridded-cubes-climate-soil-terrain-forecasts-ndvi)
- [6.2 Point extraction](#62-point-extraction-return-dataframes)
- [6.3 Crop-model input files](#63-crop-model-input-files-return-the-list-of-files-written)
- [6.4 Spatial scaffolding](#64-spatial-scaffolding-return-dataframes)
- [6.5 Seasonal-forecast bias correction](#65-seasonal-forecast-bias-correction)
- [R and CLI equivalents](#r-and-cli-equivalents)

---

## Conventions

**Region** (for every gridded/writer call): `country="Rwanda"` (name or ISO3
`"RWA"`), optionally with `admin_level=1|2` + `admin_name="..."`; **or**
`bbox=[west, south, east, north]`; **or** `geometry=` — **your own uploaded area**
(a shapefile/GeoJSON path, a GeoDataFrame, a shapely geometry, or a GeoJSON
mapping), which takes priority and is reprojected to EPSG:4326 for you. Point
functions instead take `points=` (a CSV or DataFrame with lon/lat). Full
guidance: [user guide §4.1](docs/user_guide.md#41-decision-1--select-the-study-area).

**Return shapes** referenced in the tables below:

- *Gridded cube* → `{canonical_var: {"nc": Path, "tif": Path|None, "data": xarray.DataArray}}`.
  The NetCDF is always written (it **is** the cache); `out_format=["nc","tif"]`
  adds a GeoTIFF.
- *Point extraction* → a `pandas.DataFrame`.
- *Crop-model writers* → a `list` of the files written.

**Variables** are given by short name (`PRCP`), canonical name (`AGRO.PRCP`) or
legacy label (`Precipitation`):

| Namespace | Short names | Units |
| --- | --- | --- |
| `AGRO.*` (climate) | `PRCP` | mm/day |
| | `TMAX`, `TMIN`, `TEMP` | °C |
| | `SRAD` | MJ m⁻² day⁻¹ |
| | `RHUM` | % |
| | `WIND` | m s⁻¹ |
| `SOIL.*` (SoilGrids / iSDA) | `CLAY`, `SAND`, `SILT` | % |
| | `PH` | pH |
| | `SOC`, `NITROGEN` | g kg⁻¹ |
| | `CEC` | cmol(c) kg⁻¹ |
| | `BDOD` | g cm⁻³ |
| | `CFVO` | vol % |
| | `EXTP` (iSDA only) | mg kg⁻¹ |
| `TOPO.*` (DEM) | `ELEV`, `SLOPE`, `ASPECT`, `TPI`, `TRI` | m / degree |
| `RS.*` (MODIS) | `NDVI`, `EVI` | unitless |
| `LC.*` | `CROPLAND` | 1 = cropland, NaN otherwise |

Soil depths — SoilGrids: `0-5cm, 5-15cm, 15-30cm, 30-60cm, 60-100cm, 100-200cm`;
iSDA: `0-20cm, 20-50cm` (point columns use underscores, e.g. `CLAY_0_5cm`).

> Every function also accepts `config=` (an advanced preloaded `Config`; omit it
> to load settings from the environment). It is listed once here to keep the
> per-function tables focused.

---

## 6.1 Gridded cubes (climate, soil, terrain, forecasts, NDVI)

### `get_climate`

Fetch a harmonized daily/monthly **climate cube** for a region.

**Returns:** `{canonical_var: {"nc": Path, "tif": Path|None, "data": DataArray}}`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Climate variable name(s), short or canonical. Values: `PRCP, TMAX, TMIN, TEMP, SRAD, RHUM, WIND`. |
| `years` | int \| list[int] | Yes | — | Year or years to fetch. Values: e.g. `2021`, `range(2015, 2025)`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `freq` | str | No | `'daily'` | Time step of the returned climate values. Values: `"daily"`, `"monthly"`. |
| `source` | str | No | `None` | Force one source for every variable. Default: `PRCP`→CHIRPS, the rest→AgERA5. Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `domain` | str | No | `None` | Cache-domain override (advanced); leave unset to let the tool choose. |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import get_climate
res = get_climate("PRCP", years=range(2015, 2025),
                  country="Rwanda", freq="monthly")
cube = res["AGRO.PRCP"]["data"]              # (time, lat, lon)
```

### `get_static`

Fetch harmonized **soil / terrain** layers (no time axis).

**Returns:** `{canonical_var: {"nc", "tif", "data"}}` (soil layers carry a `depth` dim)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Soil and/or terrain variable name(s). Values: soil `CLAY,SAND,SILT,PH,SOC,NITROGEN,CEC,BDOD,CFVO,EXTP`; terrain `ELEV,SLOPE,ASPECT,TPI,TRI`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `depths` | list[str] | No | `None` | Soil depth layers to return (default: all). Values: SoilGrids `0-5cm,5-15cm,15-30cm,30-60cm,60-100cm,100-200cm`; iSDA `0-20cm,20-50cm`. |
| `source` | str | No | `None` | Soil source (terrain always comes from Copernicus DEM). Values: `"soilgrids"` (default), `"isda"`. |
| `domain` | str | No | `None` | Cache-domain override (advanced); leave unset to let the tool choose. |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import get_static
get_static(["CLAY", "PH"], country="Rwanda", depths=["0-5cm", "5-15cm"])
```

### `get_dem`

Convenience wrapper of `get_static` for **elevation + terrain derivatives**.

**Returns:** same as `get_static`

Accepts **all the same parameters as [`get_static`](#get_static)** — only the `variables` default differs:

- `variables` (str | list[str], optional) — Terrain variable(s). Values: `ELEV, SLOPE, ASPECT, TPI, TRI` — default: all five.

```python
from agwise_data import get_dem
get_dem(country="Rwanda")["TOPO.SLOPE"]["data"]     # (lat, lon)
```

### `get_soil`

Convenience wrapper of `get_static` for the **SoilGrids soil set**.

**Returns:** same as `get_static`

Accepts **all the same parameters as [`get_static`](#get_static)** — only the `variables` default differs:

- `variables` (str | list[str], optional) — Soil variable(s). Values: default: `CLAY, SAND, SILT, PH, SOC, NITROGEN, CEC, BDOD, CFVO`.

```python
from agwise_data import get_soil
get_soil(["CLAY", "PH", "SOC"], country="Rwanda")
```

### `get_cropmask`

Convenience wrapper of `get_static` for the **ESA WorldCover cropland mask** (1 = cropland, NaN else) on the MODIS grid.

**Returns:** same as `get_static`

Accepts **all the same parameters as [`get_static`](#get_static)** — only the `variables` default differs:

- `variables` is fixed to `"LC.CROPLAND"` (this wrapper serves that one layer).

```python
from agwise_data import get_cropmask
mask = get_cropmask(country="Rwanda")["LC.CROPLAND"]["data"]
```

### `get_seasonal`

Fetch a **SEAS5 seasonal forecast / hindcast** cube (one init month across years).

**Returns:** `{canonical_var: {"nc", "tif", "data"}}`, `data` dims `(member, time, lat, lon)`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Forecast variable(s). Values: `PRCP, TMAX, TMIN, TEMP, SRAD`. |
| `init_month` | int | Yes | — | Forecast initialization month. Values: `1`–`12`. |
| `years` | int \| list[int] | Yes | — | Year or years to fetch. Values: e.g. `2021`, `range(2015, 2025)`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `ensemble` | str | No | `'members'` | Ensemble handling. `members` keeps all; `mean`/`median` reduce (required for a GeoTIFF). Values: `"members"`, `"mean"`, `"median"`. |
| `source` | str | No | `None` | Forecast source. Values: `"seas5"` (default). |
| `domain` | str | No | `None` | Cache-domain override (advanced); leave unset to let the tool choose. |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import get_seasonal
get_seasonal(["PRCP", "TMAX"], init_month=2, years=range(1993, 2017),
             country="Rwanda")
```

### `get_modis`

Fetch **MODIS NDVI/EVI composite** stacks (Terra+Aqua interleaved).

**Returns:** `{canonical_var: {"nc", "tif", "data"}}`, `data` dims `(time, lat, lon)`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Vegetation-index name(s). Values: `NDVI, EVI`. |
| `years` | int \| list[int] | Yes | — | Year or years to fetch. Values: e.g. `2021`, `range(2015, 2025)`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `satellite` | str | No | `'both'` | MODIS satellite: `both` interleaves Terra+Aqua (46/yr); a single one gives 23/yr. Values: `"both"`, `"terra"`, `"aqua"`. |
| `source` | str \| list[str] | No | `None` | Override the MODIS source id(s) (advanced). Values: `"mod13q1"` (Terra), `"myd13q1"` (Aqua). |
| `domain` | str | No | `None` | Cache-domain override (advanced); leave unset to let the tool choose. |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import get_modis
get_modis("NDVI", years=2021, country="Rwanda")["RS.NDVI"]["data"]
```

### `get_ndvi`

Convenience wrapper of `get_modis` for **NDVI**.

**Returns:** same as `get_modis`

Accepts **all the same parameters as [`get_modis`](#get_modis)** — only the `variables` default differs:

- `variables` is fixed to `"NDVI"` (this wrapper serves that one layer).

```python
from agwise_data import get_ndvi
get_ndvi(years=2021, country="Rwanda")["RS.NDVI"]["data"]
```

### `smooth_ndvi`

**Gap-fill + Savitzky-Golay smooth** the MODIS NDVI stack (analysis-ready NDVI).

**Returns:** `{"RS.NDVI": {"short", "source", "nc", "tif", "data"}}`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `years` | int \| list[int] | Yes | — | Year or years to fetch. Values: e.g. `2021`, `range(2015, 2025)`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `satellite` | str | No | `'both'` | MODIS satellite: `both` interleaves Terra+Aqua (46/yr); a single one gives 23/yr. Values: `"both"`, `"terra"`, `"aqua"`. |
| `source` | str \| list[str] | No | `None` | Override the MODIS source id(s) (advanced). |
| `domain` | str | No | `None` | Cache-domain override (advanced); leave unset to let the tool choose. |
| `cropmask` | bool | No | `True` | Mask out non-cropland (ESA WorldCover) before smoothing. Values: `True`, `False`. |
| `cropmask_source` | str | No | `None` | Override the cropland source (advanced). |
| `window` | int | No | `9` | Savitzky-Golay window length (must be odd). |
| `polyorder` | int | No | `3` | Savitzky-Golay polynomial order (must be < `window`). |
| `gapfill` | str | No | `'linear'` | How to fill cloud/QA gaps before smoothing: interpolate along time, or the legacy per-pixel mean. Values: `"linear"` (default), `"mean"`. |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import smooth_ndvi
smooth_ndvi(years=2021, country="Rwanda")["RS.NDVI"]["data"]
```

### `get_season`

Climate and/or NDVI **already sliced to a growing season** (cross-year aware).

**Returns:** **region mode** → cube dict; **point mode** (`points=`) → long `DataFrame`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Climate and/or remote-sensing name(s); each is routed to its source automatically. Values: climate `PRCP,TMAX,…` and/or `NDVI, EVI`. |
| `planting_date` | str | No | `None` | Season start date, applied to the whole region / all points. Values: ISO `YYYY-MM-DD`. |
| `harvest_date` | str | No | `None` | Season end date. Values: ISO `YYYY-MM-DD`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `points` | str \| DataFrame | No | `None` | Optional **point mode**: a CSV/DataFrame with lon/lat. If given, returns a long DataFrame instead of cubes. |
| `planting_col` | str | No | `None` | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | No | `None` | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `freq` | str | No | `'daily'` | Time step of the returned climate values. Values: `"daily"`, `"monthly"`. |
| `satellite` | str | No | `'both'` | MODIS satellite: `both` interleaves Terra+Aqua (46/yr); a single one gives 23/yr. Values: `"both"`, `"terra"`, `"aqua"`. |
| `source` | str \| list[str] | No | `None` | Override the source(s) for the variables (advanced). |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import get_season
get_season("NDVI", planting_date="2020-09-14",
           harvest_date="2021-02-28", country="Rwanda")
```

## 6.2 Point extraction (return DataFrames)

### `extract_points`

Long-format climate **time series at point locations** between two dates.

**Returns:** `DataFrame` with columns `point, lon, lat, time, variable, value`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `variables` | str \| list[str] | Yes | — | Climate variable name(s). Values: `PRCP, TMAX, TMIN, TEMP, SRAD, RHUM, WIND`. |
| `start` | str | Yes | — | First date to extract. Values: ISO `YYYY-MM-DD`. |
| `end` | str | Yes | — | Last date to extract. Values: ISO `YYYY-MM-DD`. |
| `freq` | str | No | `'daily'` | Time step of the returned climate values. Values: `"daily"`, `"monthly"`. |
| `source` | str | No | `None` | Force one climate source. Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import extract_points
df = extract_points("trials.csv", ["PRCP", "TMAX"],
                    start="2021-01-01", end="2021-06-30")
```

### `extract_growing_season`

Per-trial **growing-season climate** in the fertilizer-ML wide format.

**Returns:** `DataFrame`: input rows + `<VAR>_m1..mN`, `totalRF`, `nrRainyDays`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `variables` | str \| list[str] | Yes | — | Climate variable name(s). Values: `PRCP, TMAX, TMIN, TEMP, SRAD, RHUM, WIND`. |
| `planting_col` | str | Yes | — | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | Yes | — | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `legacy_names` | bool | No | `True` | Use pre-2026 column names (`Precipitation_m1`, …) so existing ML code works; `False` uses short names (`PRCP_m1`, …). Values: `True`, `False`. |
| `source` | str | No | `None` | Force one climate source. Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import extract_growing_season
df = extract_growing_season("trials.csv", ["PRCP", "TMAX"],
                            planting_col="Pl_date", harvest_col="Hv_date")
```

### `extract_static_points`

**Soil / terrain at point locations** (wide format), with optional derived columns.

**Returns:** `DataFrame`: input + one column per variable×depth (+ derived)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `variables` | str \| list[str] | Yes | — | Soil/terrain variable name(s) to extract. Values: soil `CLAY,…,EXTP`; terrain `ELEV,SLOPE,ASPECT,TPI,TRI`. |
| `depths` | list[str] | No | `None` | Soil depth layers to return (default: all). Values: SoilGrids `0-5cm,5-15cm,15-30cm,30-60cm,60-100cm,100-200cm`; iSDA `0-20cm,20-50cm`. |
| `source` | str | No | `None` | Soil source. Values: `"soilgrids"` (default), `"isda"`. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `fill_nearest_m` | float | No | `1000.0` | Fill points on NoData pixels from the nearest valid pixel within this many metres; `None` or `0` disables. |
| `derive` | str \| list[str] | No | `None` | Add pedotransfer-derived columns (a name or list). Values: `"hydraulics"`, `"olsen_p"`. |
| `calcareous` | bool | No | `False` | Use the calcareous Mehlich-3→Olsen P regression instead of the default. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import extract_static_points
extract_static_points("trials.csv", ["CLAY", "PH", "SOC"],
                      derive="hydraulics")
```

### `rainy_days`

Count the days with rainfall ≥ a threshold along the time axis.

**Returns:** `DataArray` (the count per pixel)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `daily_precip` | DataArray | Yes | — | A daily-precipitation cube with a `time` axis. |
| `threshold` | float | No | `2.0` | Minimum daily rainfall (mm) to count a day as rainy. |

```python
from agwise_data import rainy_days
n = rainy_days(cube, threshold=2.0)
```

## 6.3 Crop-model input files (return the list of files written)

### `to_dssat`

Write **DSSAT** weather (`.WTH`) + soil (`.SOL`) files for every point.

**Returns:** `list` of `{"point", "dir", "wth", "sol"}`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `planting_date` | str | No | `None` | Season start date, applied to the whole region / all points. Values: ISO `YYYY-MM-DD`. |
| `harvest_date` | str | No | `None` | Season end date. Values: ISO `YYYY-MM-DD`. |
| `out_dir` | str \| Path | No | `None` → `./DSSAT` | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `planting_col` | str | No | `None` | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | No | `None` | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `id_col` | str | No | `None` | Column in `points` used as the point identifier in output file names. |
| `station_col` | str | No | `None` | Column in `points` for the weather-station id/name written into the files. |
| `country` | str | No | `'-99'` | DSSAT country **code** written into the files (not a region selector). |
| `weather` | DataFrame | No | `None` | Reuse a weather `DataFrame` you already extracted instead of re-fetching. |
| `soil` | DataFrame | No | `None` | Reuse a soil `DataFrame` you already extracted instead of re-fetching. |
| `weather_source` | str | No | `None` | Override the climate source used for the weather (advanced). Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `soil_source` | str | No | `None` | Override the soil source. Values: `"soilgrids"`, `"isda"`. |
| `calcareous` | bool | No | `False` | Use the calcareous Mehlich-3→Olsen P regression instead of the default. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import to_dssat
to_dssat("trials.csv", planting_date="2021-01-01",
         harvest_date="2021-04-30", out_dir="DSSAT", station_col="site")
```

### `to_apsim`

Write **APSIM** weather (`.met`) + soil files for every point.

**Returns:** `list` of the files written per point

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `planting_date` | str | No | `None` | Season start date, applied to the whole region / all points. Values: ISO `YYYY-MM-DD`. |
| `harvest_date` | str | No | `None` | Season end date. Values: ISO `YYYY-MM-DD`. |
| `out_dir` | str \| Path | No | `None` → `./APSIM` | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `planting_col` | str | No | `None` | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | No | `None` | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `id_col` | str | No | `None` | Column in `points` used as the point identifier in output file names. |
| `station_col` | str | No | `None` | Column in `points` for the weather-station id/name written into the files. |
| `weather` | DataFrame | No | `None` | Reuse a weather `DataFrame` you already extracted instead of re-fetching. |
| `soil` | DataFrame | No | `None` | Reuse a soil `DataFrame` you already extracted instead of re-fetching. |
| `weather_source` | str | No | `None` | Override the climate source used for the weather (advanced). Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `soil_source` | str | No | `None` | Override the soil source. Values: `"soilgrids"`, `"isda"`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import to_apsim
to_apsim("trials.csv", planting_date="2021-01-01",
         harvest_date="2021-04-30", out_dir="APSIM")
```

### `to_wofost`

Write **WOFOST** weather + soil files for every point.

**Returns:** `list` of the files written per point

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `planting_date` | str | No | `None` | Season start date, applied to the whole region / all points. Values: ISO `YYYY-MM-DD`. |
| `harvest_date` | str | No | `None` | Season end date. Values: ISO `YYYY-MM-DD`. |
| `out_dir` | str \| Path | No | `None` → `./WOFOST` | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `planting_col` | str | No | `None` | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | No | `None` | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `id_col` | str | No | `None` | Column in `points` used as the point identifier in output file names. |
| `station_col` | str | No | `None` | Column in `points` for the weather-station id/name written into the files. |
| `weather` | DataFrame | No | `None` | Reuse a weather `DataFrame` you already extracted instead of re-fetching. |
| `soil` | DataFrame | No | `None` | Reuse a soil `DataFrame` you already extracted instead of re-fetching. |
| `weather_source` | str | No | `None` | Override the climate source used for the weather (advanced). Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `soil_source` | str | No | `None` | Override the soil source. Values: `"soilgrids"`, `"isda"`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import to_wofost
to_wofost("trials.csv", planting_date="2021-01-01",
          harvest_date="2021-04-30", out_dir="WOFOST")
```

### `to_oryza`

Write **ORYZA** CABO weather + PADDY soil files for every point.

**Returns:** `list` of the files written per point

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `planting_date` | str | No | `None` | Season start date, applied to the whole region / all points. Values: ISO `YYYY-MM-DD`. |
| `harvest_date` | str | No | `None` | Season end date. Values: ISO `YYYY-MM-DD`. |
| `out_dir` | str \| Path | No | `None` → `./ORYZA` | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `planting_col` | str | No | `None` | Column in `points` holding each row's planting date (per-trial seasons). |
| `harvest_col` | str | No | `None` | Column in `points` holding each row's harvest date. Pass **both** `*_col` or neither. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `id_col` | str | No | `None` | Column in `points` used as the point identifier in output file names. |
| `station_col` | str | No | `None` | Column in `points` for the weather-station id/name written into the files. |
| `weather` | DataFrame | No | `None` | Reuse a weather `DataFrame` you already extracted instead of re-fetching. |
| `soil` | DataFrame | No | `None` | Reuse a soil `DataFrame` you already extracted instead of re-fetching. |
| `weather_source` | str | No | `None` | Override the climate source used for the weather (advanced). Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `soil_source` | str | No | `None` | Override the soil source. Values: `"soilgrids"`, `"isda"`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import to_oryza
to_oryza("trials.csv", planting_date="2021-01-01",
         harvest_date="2021-04-30", out_dir="ORYZA")
```

## 6.4 Spatial scaffolding (return DataFrames)

### `make_grid`

Build a **regular point grid** clipped to a country/admin boundary (or a bbox).

**Returns:** `DataFrame` with `lon, lat, country` (+ `NAME_1`/`NAME_2` when a country is given)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `res_km` | float | No | `5.0` | Grid spacing in kilometres. Values: e.g. `5.0`, `1.0`, `0.25`. |
| `tag_admin_level` | int | No | `2` | Tag each grid point with admin names up to this level. Values: `0`, `1`, `2`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import make_grid
grid = make_grid(country="Rwanda", res_km=5.0)
```

### `tag_admin`

**Tag points with admin-unit names** (the field↔geospatial link).

**Returns:** the input `DataFrame` + `country`, `NAME_1` (and `NAME_2` when `admin_level ≥ 2`)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `country` | str | Yes | — | Country the points fall in (name or ISO3), used for the boundary lookup. Values: e.g. `"Rwanda"`. |
| `admin_level` | int | No | `2` | Deepest admin level to tag (`2` adds `NAME_2`). Values: `1`, `2`. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import tag_admin
tagged = tag_admin("trials.csv", country="Rwanda", admin_level=2)
```

## 6.5 Seasonal-forecast bias correction

### `bias_correct`

**Bias-correct a SEAS5 forecast** with Quantile Delta Mapping.

**Returns:** `{canonical_var: {"short", "kind", "nc", "data"}}`, corrected `(member, time, lat, lon)`

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `variables` | str \| list[str] | Yes | — | Forecast variable(s) to bias-correct. Values: `PRCP, TMAX, TMIN, SRAD`. |
| `init_month` | int | Yes | — | Forecast initialization month. Values: `1`–`12`. |
| `forecast_year` | int | Yes | — | The year whose forecast is corrected. |
| `calib_years` | list[int] | Yes | — | Hindcast/observation years used to learn the bias. Values: e.g. `range(1993, 2017)`. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `window_days` | int | No | `None` | Restrict QDM calibration to ±this many days-of-year of each step; `None` pools the whole season. |
| `obs` | dict | No | `None` | Advanced/testing: supply the observation cubes directly to skip fetching. |
| `hind` | dict | No | `None` | Advanced/testing: supply the hindcast cubes directly to skip fetching. |
| `fcst` | dict | No | `None` | Advanced/testing: supply the forecast cubes directly to skip fetching. |
| `source` | str | No | `None` | Forecast source. Values: `"seas5"` (default). |
| `out_format` | str \| list[str] | No | `'nc'` | Output format(s). The NetCDF is always written (it *is* the cache); add `tif` for a GeoTIFF. Values: `"nc"`, `"tif"`, `["nc","tif"]`. |
| `out_dir` | str \| Path | No | `None` → cache | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `overwrite` | bool | No | `False` | Recompute and overwrite the cached product instead of reusing it. Values: `True`, `False`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import bias_correct
bias_correct(["PRCP", "TMAX"], init_month=2, forecast_year=2024,
             calib_years=range(1993, 2017), country="Rwanda")
```

### `forecast_to_dssat`

**Bias-correct a forecast and write DSSAT files** in one call.

**Returns:** the `to_dssat` manifest (`list` of written-file records)

| Parameter | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `points` | str \| DataFrame | Yes | — | Point locations: a CSV path or a `DataFrame` with longitude/latitude columns. |
| `init_month` | int | Yes | — | Forecast initialization month. Values: `1`–`12`. |
| `forecast_year` | int | Yes | — | The year whose forecast is written. |
| `calib_years` | list[int] | Yes | — | Hindcast/observation years used to learn the bias. Values: e.g. `range(1993, 2017)`. |
| `out_dir` | str \| Path | No | `None` → `./DSSAT` | Directory for the output files (see the **Default** column for where it lands when omitted). |
| `ensemble` | str | No | `'mean'` | Reduce the corrected forecast ensemble before writing. Values: `"mean"` (default), `"median"`. |
| `window_days` | int | No | `None` | Restrict QDM calibration to ±this many days-of-year of each step; `None` pools the whole season. |
| `country` | str | No | `None` | Region by country **name or ISO3 code**. Use this *or* `bbox`. Values: e.g. `"Rwanda"`, `"RWA"`. |
| `bbox` | list[float] | No | `None` | Region as a bounding box `[west, south, east, north]` in degrees. Use this *or* `country`. |
| `admin_level` | int | No | `0` | How deep to clip when `country` is set: country / first / second admin level. Values: `0`, `1`, `2`. |
| `admin_name` | str | No | `None` | Name of the admin unit to clip to (needs `admin_level` ≥ 1). Values: e.g. `"Nakuru"`. |
| `geometry` | str \| GeoDataFrame \| geometry | No | `None` | **Your own uploaded area** to clip to — a file path (shapefile/GeoJSON/…), a `GeoDataFrame`, a shapely geometry, or a GeoJSON mapping. Takes priority over `country`/`bbox`; reprojected to EPSG:4326 automatically. |
| `lon_col` | str | No | `None` | Longitude column in `points` (auto-detected if omitted). |
| `lat_col` | str | No | `None` | Latitude column in `points` (auto-detected if omitted). |
| `id_col` | str | No | `None` | Column in `points` used as the point identifier in output file names. |
| `station_col` | str | No | `None` | Column in `points` for the weather-station id/name written into the files. |
| `country_name` | str | No | `'-99'` | DSSAT country **code** written into the files. |
| `corrected` | dict | No | `None` | Advanced/testing: a precomputed `bias_correct` result, to skip the QDM step. |
| `soil` | DataFrame | No | `None` | Reuse a soil `DataFrame` you already extracted instead of re-fetching. |
| `soil_source` | str | No | `None` | Override the soil source. Values: `"soilgrids"`, `"isda"`. |
| `weather_source` | str | No | `None` | Override the climate source used for the weather (advanced). Values: `"chirps"`, `"chirps_v3"` (local-only, CGLabs), `"agera5"`. |
| `config` | Config | No | `None` | Advanced: a preloaded `Config`; omit to load from the environment. |

```python
from agwise_data import forecast_to_dssat
forecast_to_dssat("trials.csv", init_month=2, forecast_year=2024,
                  calib_years=range(1993, 2017), out_dir="DSSAT_fc",
                  station_col="site")
```

---

## R and CLI equivalents

Every function above has an **R wrapper** (`ad_<name>`, same arguments —
`source("r/agwise_data.R")`) and a **CLI subcommand** (`agwise-data <name>`).
The full Python ↔ R ↔ CLI mapping, with side-by-side examples, is in
**[user guide §5](docs/user_guide.md#5-user-interface--python--r--cli--claude-code)**.

```bash
agwise-data --help            # list every subcommand
agwise-data <subcommand> -h   # parameters for one subcommand
agwise-data catalog list      # sources + variables
agwise-data cache info        # what is cached, and where
```

Region flags on the CLI: `--country`, `--admin-level`, `--admin-name`, `--bbox`;
output flags: `--format nc,tif`, `--out-dir`, `--overwrite`.
