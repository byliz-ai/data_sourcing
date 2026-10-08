# AGENTS.md — guide for AI assistants (Claude Code, Codex, Copilot, …)

This file is for **AI coding assistants** working in this repository or using
the `agwise-data` package for a user. Humans should start at
[README.md](README.md). Keep answers grounded in the files linked here; do not
guess parameters. [REFERENCE.md](REFERENCE.md) lists every public parameter.

## What this is

`agwise-data` is the AgWise **data-sourcing layer**: one call fetches,
harmonizes (canonical names/units), quality-checks and caches climate, soil,
terrain and remote-sensing data, and writes crop-model inputs (DSSAT, APSIM,
WOFOST, ORYZA). Python package `agwise_data` (`src/agwise_data/`), R wrappers
`ad_*` (`r/agwise_data.R`, they shell out to the CLI), CLI `agwise-data`.

## Rules (shared CGLabs server)

- **Data is shared, credentials are personal.** Never write tokens into the repo
  or a shared folder; credentials live in the user's home (`~/.cdsapirc`,
  `~/.config/earthengine/credentials`, `chmod 600`).
- **Never write into `Global_GeoData/Landing`** (raw, read-only). The shared
  cache `Global_GeoData/Processed` is written only by the library itself; for
  tests/experiments use a throwaway root (`AGWISE_DATA_ROOT`) or `out_dir=`
  in a folder the user owns.
- **Commit only from the shared clone** `/home/jovyan/agwise-datasourcing/code/data_sourcing`.
  Trunk-based: commit and push to `origin/main` **only when the user asks**.
  CI (`.github/workflows/tests.yml`) runs pytest on Python 3.10 and 3.12.
- Python for this repo on CGLabs: `/home/jovyan/agwise-datasourcing/envs/agwise_data/bin/python`.

## Task → function

| User wants | Python (`from agwise_data import …`) | CLI | R |
| --- | --- | --- | --- |
| Climate cube for a region | `get_climate(vars, years, country=… / bbox=… / geometry=…, freq="daily"\|"monthly")` | `get` | `ad_get_climate` |
| Soil / terrain cube | `get_static`, `get_soil`, `get_dem` | `get-static` | `ad_get_static` |
| Climate at points, two dates | `extract_points(points, vars, start, end)` → long DataFrame | `extract --start --end` | `ad_extract_points` |
| Per-trial season climate (ML, wide) | `extract_growing_season(points, vars, planting_col, harvest_col)` | `extract --planting-col --harvest-col` | `ad_extract_growing_season` |
| Soil/terrain at points | `extract_static_points(points, vars, derive="hydraulics")` | `extract-static` | `ad_extract_static_points` |
| Season slice (region or points) | `get_season(vars, planting_date, harvest_date, …)` | `get-season` | `ad_get_season` |
| Seasonal forecast (SEAS5) | `get_seasonal(vars, init_month, years)`; corrected: `bias_correct` | `get-seasonal`, `bias-correct` | `ad_get_seasonal`, `ad_bias_correct` |
| NDVI/EVI | `get_modis`, `get_ndvi`, `smooth_ndvi` | `get-modis`, `smooth-ndvi` | `ad_get_modis`, `ad_smooth_ndvi` |
| Cropland mask | `get_cropmask` | `get-cropmask` | `ad_get_cropmask` |
| Crop-model files | `to_dssat`, `to_apsim`, `to_wofost`, `to_oryza`, `forecast_to_dssat` | `to-dssat`, … | `ad_to_dssat`, … |
| Point grid / admin names | `make_grid`, `tag_admin` | `make-grid`, `tag-admin` | `ad_make_grid`, `ad_tag_admin` |

Full parameters: [REFERENCE.md](REFERENCE.md). Same task in all interfaces:
[docs/user_guide.md §5](docs/user_guide.md#5-user-interface--python--r--cli--claude-code).

## Conventions you must respect

- **Variables:** short (`PRCP`), canonical (`AGRO.PRCP`) or legacy
  (`Precipitation`) names. Namespaces `AGRO.*` (climate), `SOIL.*`, `TOPO.*`,
  `RS.*`, `LC.*`. Units are fixed in `harmonize.py` (`CANONICAL_VARS`,
  `STATIC_VARS`, `RS_VARS`); e.g. PRCP mm/day, temperatures °C, SRAD
  MJ m⁻² day⁻¹, CLAY %, SOC g/kg, BDOD g/cm³.
- **Region:** `country` (+ `admin_level`, `admin_name`) **or** `bbox=[W,S,E,N]`
  **or** `geometry=` (file/GeoDataFrame/shapely; CLI `--aoi`, R `aoi=`).
- **Returns:** gridded → `{canonical: {"nc", "tif", "qc", "data"}}`; points →
  `DataFrame` (QC reports in `df.attrs["qc"]`); writers → list of
  `{"point", "dir", <files>, "qc"}` plus `<out_dir>/qc_report.json`.
- **Defaults:** `PRCP` → CHIRPS v3 local on CGLabs (1981–2025), else CHIRPS
  v2; other weather → AgERA5 (needs CDS); soil → SoilGrids (`source="isda"` for
  iSDA); MODIS/WorldCover/CHIRPS v2 → Earth Engine.
- **Product cache names:** `<Kind>_<VAR>_<first>_<last>[_y<digest>][_<source>][_qcoff|_qc<hash>]`.
  Tags appear only for non-default choices, so default products are shared.
- **Quality control is on by default** (`qc="warn"`): impossible → NaN,
  implausible → kept + `QCWarning`, missing rain never 0, crop-model files
  gap-filled ≤ 5 days (never rain) and validated after writing. Explain this
  to users when they see NaN or warnings: [docs/quality_control.md](docs/quality_control.md).

## Code map

| Path | Owns |
| --- | --- |
| `src/agwise_data/api.py` | every public function; product caching; QC wiring |
| `src/agwise_data/harmonize.py` | canonical variables, units, conversions, `to_monthly`, `integer_sentinel` |
| `src/agwise_data/qc.py` + `qc_ranges.yaml` | range QC, SRAD ≤ Ra, texture normalization, `check_weather` (gap-fill) |
| `src/agwise_data/catalog/*.yaml` + `catalog.py` | one YAML per source (access, variables, conversion, nodata) |
| `src/agwise_data/drivers/` | one driver per source (`base.Driver`, `static.StaticDriver`, `seasonal`, `modis`, `local` = reads staged `Landing` files) |
| `src/agwise_data/writers/` | crop-model writers (`dssat`, `apsim`, `wofost`, `oryza`, `soil`) + `validate.py` (post-write checks) |
| `src/agwise_data/cache.py`, `config.py`, `retry.py`, `memory.py` | atomic writes + manifests, data roots/domains, download retries, memory budget |
| `src/agwise_data/cli.py`, `r/agwise_data.R` | CLI subcommands; R wrappers (not covered by pytest) |
| `tests/` | network-free tests; fake drivers + `config` fixture in `conftest.py` |

## Invariants and lessons (read before changing code)

1. **Product NetCDF writes run on dask's synchronous scheduler**
   (`api._write_nc_product`). With threads, writes that stream from lazily
   opened NetCDFs deadlocked intermittently on the HDF5 locks. Don't revert
   this. **Never compute anything else inside a write's dask graph** (QC counts
   are computed in a separate pass, `_qc_finish`).
2. **Missing rain is never zero:** `to_monthly` sums use `skipna=False`;
   season totals are NaN with a missing day; writers never gap-fill rain.
3. **Mask nodata before unit conversion** (`integer_sentinel`, catalog
   `nodata`), or 255 becomes pH 25.5.
4. **QC never alters the shared harmonized cache**; it runs at product time.
   Non-default QC gets its own product name.
5. **New canonical variable** → add it to `harmonize.py` *and*
   `qc_ranges.yaml` (`tests/test_qc.py` checks units and that the plausible
   range sits inside the physical one), plus REFERENCE, user-guide tables and a
   CHANGELOG entry with a version bump in `pyproject.toml`.
6. **Measure on real data before adopting an external rule.** The prismpy
   ">5 % texture deviation → drop the layer" rule blanked the topsoil of ~1/3 of
   SoilGrids profiles (fixed in 0.36.1).
7. Synthetic test data is not physical (e.g. TMAX = day-of-year − 273.15);
   tests that assert raw synthetic values pass `qc="off"`.

## Run the tests

```bash
cd /home/jovyan/agwise-datasourcing/code/data_sourcing
/home/jovyan/agwise-datasourcing/envs/agwise_data/bin/python -m pytest -q -o faulthandler_timeout=60
```

~17 s, no network, no credentials. `tests/test_modis.py::test_modis_tif_band_labels`
fails on CGLabs (local GDAL lacks GeoTIFF update mode) and passes in CI; every
other test must pass. After a version bump run
`…/envs/agwise_data/bin/pip install -e . --no-deps` so `agwise_data.__version__`
updates for everyone.

## Docs map (one home per topic)

| File | Topic |
| --- | --- |
| [README.md](README.md) | what it is, folders/cache, install, first success |
| [docs/onboarding.md](docs/onboarding.md) | new teammates on CGLabs |
| [docs/credentials_setup.md](docs/credentials_setup.md) | Copernicus CDS + Earth Engine |
| [docs/cglabs_setup.md](docs/cglabs_setup.md) | server install, data roots, R, performance env vars |
| [docs/user_guide.md](docs/user_guide.md) | area / data / period / output decisions; Python · R · CLI · Claude Code |
| [docs/quality_control.md](docs/quality_control.md) | everything QC: ranges, reports, writers, sources |
| [REFERENCE.md](REFERENCE.md) | every public function and parameter |
| [CONTRIBUTING.md](CONTRIBUTING.md) | dev workflow, adding a source, doc rules, roadmap |
| [CHANGELOG.md](CHANGELOG.md) | what changed in each version and why |
| [docs/prismpy_comparison.md](docs/prismpy_comparison.md) | comparison with prismpy + phased plan (Spanish) |
