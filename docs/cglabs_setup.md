# CGLabs setup (one time per server, then one line per user)

## 1. Shared installation (once, by whoever goes first)

```bash
cd /home/jovyan
git clone https://github.com/byliz-ai/data_sourcing.git
cd data_sourcing
conda env create -f environment.yml
conda activate agwise_data
pip install -e ".[all]"
```

## 2. Data roots — the AgWise folder convention

AgWise keeps two things apart, and this layer follows the same line:

* **Shared raw inputs** — the global geodata everyone consumes, already staged in
  `.../datasourcing/Data/Global_GeoData/Landing`. Point `AGWISE_LOCAL_ROOT` here
  and the drivers **read + region-clip** those files instead of re-downloading
  (treat it as read-only — see [performance tuning](#performance-tuning-optional)).
* **Your use-case workspace** — where the outputs go. Following the AgWise
  convention, create `.../datasourcing/Data/useCase_<Country>_<Name>/` and point
  `AGWISE_DATA_ROOT` there; the layer's harmonized cache, products and the files
  you write all land under your own use-case folder.

Each user adds to their `~/.bashrc` (or R `.Renviron`):

```bash
DATASOURCING=/home/jovyan/agwise-datasourcing/dataops/datasourcing/Data
export AGWISE_LOCAL_ROOT=$DATASOURCING/Global_GeoData/Landing    # shared raw inputs (read-only)
export AGWISE_DATA_ROOT=$DATASOURCING/useCase_Rwanda_MyProject   # your use-case outputs
export HDF5_USE_FILE_LOCKING=FALSE                               # Landing is on NFS
mkdir -p "$AGWISE_DATA_ROOT"
```

So a request **reads the shared Landing file (no download), clips it to your
region, and writes the result under your own use-case folder** — exactly the
inputs-shared / outputs-per-use-case pattern the AgWise modules already use. For
crop-model files, point `out_dir` under your use-case too, e.g.
`out_dir="$AGWISE_DATA_ROOT/result/DSSAT"`.

Not on CGLabs (laptop / other server)? Leave `AGWISE_LOCAL_ROOT` unset and the
layer downloads from source into `AGWISE_DATA_ROOT` as usual — a personal folder
like `~/agwise_data/cache` is fine.

## 3. CDS credentials (per user, for AgERA5)

(First time on Copernicus or Google Earth Engine? There is a from-zero,
click-by-click guide in [credentials_setup.md](credentials_setup.md) —
including the shared-server rules: data is shared via `common_data`,
credentials are personal and live only in your own home.)

1. Create a free account at <https://cds.climate.copernicus.eu>.
2. Accept the license of the *"Agrometeorological indicators"* dataset
   (one click on its download page).
3. Put your Personal Access Token in `~/.cdsapirc`:

```
url: https://cds.climate.copernicus.eu/api
key: <your-personal-access-token>
```

```bash
chmod 600 ~/.cdsapirc
```

⚠️ **Never hardcode the token in a script.** A previous module script
committed a CDS key in plain text — that key should be considered
compromised and rotated (log in to CDS → your profile → regenerate token).

CHIRPS needs no credentials.

## 4. Use from R (no reticulate needed)

```r
source("/home/jovyan/data_sourcing/r/agwise_data.R")
# If agwise-data is not on the PATH R sees:
Sys.setenv(AGWISE_DATA_BIN = "/home/jovyan/.conda-envs/agwise_data/bin/agwise-data")

r <- ad_get_climate(vars = "PRCP", years = 2005:2024,
                    country = "Kenya", freq = "monthly")
```

(Adjust the conda env path to wherever `conda env list` says `agwise_data`
lives — on CGLabs typically `/home/jovyan/.conda-envs/agwise_data`.)

## 5. Sanity check

```bash
agwise-data catalog list
agwise-data cache path
# small real download (one year, one small country product):
agwise-data get --vars PRCP --country Rwanda --years 2023:2023 --freq monthly
agwise-data cache info
```

## Performance tuning (optional)

The defaults are sensible; two environment variables matter at scale:

```bash
# parallel (variable, year) fetches — raise for AgERA5-heavy workloads
# where wall-clock is dominated by CDS queue waits:
export AGWISE_DATA_WORKERS=6

# on a shared bulk server you may prefer continental-domain fetching so
# one cache serves every country (small requests otherwise fetch only
# their own window, which is much faster for one-off runs):
export AGWISE_DATA_SCOPE=domain     # default: auto

# reuse already-downloaded legacy geodata instead of downloading: point this
# at the AgWise Global_GeoData/Landing tree. When set, the drivers read the
# matching local file, clip it to the region, and cache it — no network:
#   - CHIRPS / AgERA5 (daily): <Variable>/<Source>/<year>.nc
#   - SoilGrids (soil):        Soil/soilGrids/profile/<var>_<depth>_mean_30s.tif
#   - MODIS (composites):      modis/<domain>/<short>_<year>_<doy>.tif (staged;
#     the region-baked legacy Landing/MODISdata files are not auto-matched)
# Read-only and opt-in; unset it to always download. A Rwanda year reads in
# seconds instead of minutes. NB: Landing is an NFS share — export
# HDF5_USE_FILE_LOCKING=FALSE so the NetCDF reads don't fail with an HDF error.
export AGWISE_LOCAL_ROOT=/home/jovyan/agwise-datasourcing/dataops/datasourcing/Data/Global_GeoData/Landing
export HDF5_USE_FILE_LOCKING=FALSE
```

With the default `auto` scope, a country-scale CHIRPS request reads only
that country's window from UCSB's daily COGs (a Rwanda year stores ~1 MB);
Africa-scale requests download the yearly global NetCDF (~1.1 GB) once
over parallel connections and every country afterwards reuses it. Requests
against providers are deliberately paced — if a server rate-limits us the
driver falls back to a gentler path automatically.

## Portability

Nothing here is CGLabs-specific: on a laptop or another server, set
`AGWISE_DATA_ROOT` to any local path (default `~/agwise_data`). When a
shared bucket exists, mount it and point the root there — the code does not
change.
