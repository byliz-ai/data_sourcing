# agwise-data — the AgWise data-sourcing module

One call to **fetch, harmonize and cache** the climate, soil, terrain and
remote-sensing data every AgWise module needs — and to turn it into
**analysis-ready inputs** (season slices, DSSAT/APSIM files, bias-corrected
forecasts). A dataset is downloaded **once** into a shared cache with agreed
names and units (`AGRO.PRCP` in mm/day, `SOIL.CLAY` in %, …) and reused by
everyone afterwards.

New here? Read this page top to bottom — it takes you from nothing to your
first data fetch. Detailed walkthroughs are linked where useful so this stays
short.

- 🔑 **[docs/credentials_setup.md](docs/credentials_setup.md)** — click-by-click credential setup (from zero) + troubleshooting
- 🖥️ **[docs/cglabs_setup.md](docs/cglabs_setup.md)** — shared-server (CGLabs) setup, use from R, performance tuning
- 📖 **[HANDOFF.md](HANDOFF.md)** — full function reference: every function, its parameters, output and an example

---

## 1. What you need

| # | Requirement | Needed for |
| --- | --- | --- |
| 1 | **conda** (Miniconda/Anaconda) + git | everything (creates the `agwise_data` Python 3.10+ env) |
| 2 | A **cache folder** (`AGWISE_DATA_ROOT`) | everything — where downloads and products are stored |
| 3 | A free **Copernicus CDS** account + token | temperature/radiation/humidity/wind (AgERA5) and seasonal forecasts (SEAS5) |
| 4 | A free **Google Earth Engine** account + a Cloud project | MODIS NDVI/EVI (`get_ndvi`) and the crop mask (`get_cropmask`) |

Rainfall (CHIRPS), soil (SoilGrids), terrain (Copernicus DEM) and admin
boundaries (geoBoundaries) need **no account**. You only need credentials
for the sources you actually call — you can install and use CHIRPS/soil/DEM
with no accounts at all.

> **Golden rule (shared servers):** *data is shared, credentials are
> personal.* Everyone points at the same shared cache, but each person keeps
> their **own** tokens in their **own** home (`chmod 600`) — never in the
> repo, a notebook, or the shared folder. Details in
> [credentials_setup.md](docs/credentials_setup.md).

## 2. How to get the credentials

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

## 3. How to install

```bash
git clone https://github.com/byliz-ai/data_sourcing.git
cd data_sourcing
conda env create -f environment.yml     # creates the 'agwise_data' env
conda activate agwise_data
pip install -e ".[all]"                  # package + CDS + Earth Engine clients

# Verify — the test suite needs no credentials and no network:
pytest -q

# Choose where data is cached (your own folder for testing;
# the shared folder in production — see cglabs_setup.md):
export AGWISE_DATA_ROOT=~/agwise_data/cache
```

Install extras if you don't need everything: `.[geo]` (clipping + GeoTIFF),
`.[cds]` (AgERA5/SEAS5), `.[gee]` (MODIS + crop mask), `.[dev]` (test suite).
Shared-server install, use from **R**, and performance tuning are in
**[docs/cglabs_setup.md](docs/cglabs_setup.md)**.

## 4. How to use

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

👉 **Every function — with all its parameters, expected output and a runnable
example — is in [HANDOFF.md](HANDOFF.md).** It covers gridded cubes
(`get_climate`, `get_static`/`get_dem`/`get_soil`, `get_seasonal`,
`get_modis`/`get_ndvi`, `get_cropmask`, `get_season`), point extraction
(`extract_points`, `extract_growing_season`, `extract_static_points`),
crop-model input files (`to_dssat`, `to_apsim`), spatial helpers
(`make_grid`, `tag_admin`) and seasonal-forecast bias correction
(`bias_correct`, `forecast_to_dssat`), plus the R and CLI equivalents.

## Contributing & changelog

Contributions welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** (dev setup,
running tests, adding a data source, the shared-cache ground rules). Release
history is in **[CHANGELOG.md](CHANGELOG.md)**.

## License

MIT — see [LICENSE](LICENSE).
