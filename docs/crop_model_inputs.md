# Crop-model inputs: season slices and DSSAT/APSIM files

Two deliverables that let the AgWise modules **start from analysis-ready
inputs** instead of re-implementing the same data-prep per use case:

1. `get_season` — climate/NDVI already sliced to a growing season.
2. `to_dssat` / `to_apsim` — the weather + soil files a crop model reads,
   written per point. These retire the per-module `readGeo_CM_zone.R` /
   `01_readGeo_CM_zone_APSIM.R` scaffolding.

Everything below is the *last mile* on top of the layer's existing outputs
(`get_climate`, `extract_points`, `extract_static_points`, `get_modis`); it
adds no new data source.

## `get_season` — season-ready climate & NDVI

```python
from agwise_data import get_season
```

A "season" is any date range, **including one that crosses the calendar
year** (e.g. Rwanda season B, Sep → Feb): the time axis is continuous, so a
cross-year slice is just `slice(planting, harvest)`.

> Not to be confused with `get_seasonal`, which fetches SEAS5 seasonal
> *forecasts*. `get_season` slices observed climate/NDVI to a season.

**Region mode** (returns a cube per variable, written as a `Season_*`
product):

```python
res = get_season(
    ["PRCP", "TMAX", "NDVI"],          # climate + remote sensing together
    planting_date="2020-09-14",
    harvest_date="2021-02-28",         # crosses the New Year
    country="Rwanda",
)
res["AGRO.PRCP"]["data"]   # (time, lat, lon), sliced to the season
res["RS.NDVI"]["nc"]       # Season_NDVI_20200914_20210228.nc
```

**Points mode** (returns a long DataFrame; `planting_col`/`harvest_col` give
each trial its own season):

```python
df = get_season(
    ["PRCP", "TMAX", "TMIN", "SRAD"],
    points=trials,                     # DataFrame/CSV with lon/lat
    planting_col="Pl_date", harvest_col="Hv_date",
)   # columns: point, lon, lat, time, variable, value
```

CLI: `agwise-data get-season --vars PRCP,NDVI --country Rwanda
--planting-date 2020-09-14 --harvest-date 2021-02-28`. R: `ad_get_season()`.

## `to_dssat` / `to_apsim` — crop-model input files

```python
from agwise_data import to_dssat, to_apsim

to_dssat(trials, planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="DSSAT", station_col="site", country="Rwanda")
to_apsim(trials, planting_date="2021-01-01", harvest_date="2021-04-30",
         out_dir="APSIM", station_col="site")
```

For each point `n` these write:

| Engine | Files (under `out_dir/EXTE<n>/`) | Contents |
| --- | --- | --- |
| DSSAT | `WHTE<n>.WTH` | daily `DATE TMAX TMIN SRAD RAIN` + `GENERAL` header (INSI, LAT, LONG, ELEV, **TAV**, **AMP**, REFHT, WNDHT) |
| DSSAT | `SOIL.SOL` | layered profile: SLLL/SDUL/SSAT/SSKS (Saxton-Rawls hydraulics), SBDM, SLOC, SLCL, SLSI, SLNI, SLHW, SCEC, SRGF; SALB/SLU1/SLRO from texture |
| APSIM | `wth_loc_<n>.met` | `year day radn maxt mint rain` + `tav`/`amp` header |
| APSIM | `soil_<n>.csv` | per-layer LL15/DUL/SAT/AirDry/KS/BD/Carbon/clay/silt/N/PH/CEC (+ Salb/CN2Bare in the header comment) to slot into the apsimx soil template |

The weather is the season slice; the soil is SoilGrids at the point plus the
**Saxton & Rawls (2006)** pedotransfer functions (`get_geoSpatialData_V2.R`'s
equations) for the hydraulic properties SoilGrids does not carry. Sanity
fixes match the legacy scripts (swap any TMIN > TMAX day; TAV = mean daily
mean temperature; AMP = half the month-to-month temperature range).

You can pass `weather=`/`soil=` frames you already extracted to skip the
fetch. CLI: `agwise-data to-dssat`/`to-apsim`. R: `ad_to_dssat()`/
`ad_to_apsim()`.

### Verification

The file layouts were matched byte-for-byte against reference files the
`DSSAT`/`apsimx` R packages emit, and every output round-trips through those
packages' own readers:

- `DSSAT::read_wth` / `read_sol` read back `.WTH`/`.SOL` with the correct
  header, layers and cross-year date span; `PWP < FC < SAT` at every layer.
- `apsimx::read_apsim_met` + `check_apsim_met` accept the `.met` (no fatal
  issues).
- End-to-end `to_dssat` on real SoilGrids points (Rwanda) produces a
  `SOIL.SOL` that `read_sol` parses cleanly (texture "C"/clay, monotonic
  hydraulics).

### Not yet included

- The DSSAT phosphorus block (`SLPX`/`SLPT` …) is omitted — the layer does
  not source phosphorus yet (that is a separate Mehlich-3 step). DSSAT
  treats the block as optional.
- `SLDR`/`SLNF`/`SLPF` are DSSAT-neutral metadata defaults a module can
  override; they are not derivable from SoilGrids.
