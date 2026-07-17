# CGLabs setup — Section 2 (shared-server deep dive)

Installation basics (required software, `git clone` → `conda` → `pip install`,
and the folder structure after install) are in
[README Section 2](../README.md#2-installation). This page covers what is
**specific to the shared server**: installing once for everyone, persisting the
data roots per user, using it from R, and performance tuning.

## 1. Install (once per server)

Install the package + env once, in a location everyone can reach — same commands
as [README §2.2](../README.md#22-install) (`git clone` → `conda env create
-f environment.yml` → `conda activate agwise_data` → `pip install -e ".[all]"`).

## 2. Data roots (once per user)

The **three data folders** — and *why* there are three — are explained in
[README §1.2](../README.md#12-the-three-data-folders--each-with-one-job). On the
shared server, persist the two roots in your `~/.bashrc` (R users: `.Renviron`)
and create your use-case folder:

```bash
DATASOURCING=/home/jovyan/agwise-datasourcing/dataops/datasourcing/Data
export AGWISE_LOCAL_ROOT=$DATASOURCING/Global_GeoData/Landing     # raw inputs (read-only)
export AGWISE_DATA_ROOT=$DATASOURCING/Global_GeoData/Processed    # shared download cache (read/write)
export HDF5_USE_FILE_LOCKING=FALSE                                # Landing/Processed are on NFS
mkdir -p "$DATASOURCING/useCase_<Country>_<Name>/result"          # your outputs (writer out_dir)
```

Reuse is maximised by requesting stable regions (`country=`/`admin_level=`) or,
on a bulk server, `AGWISE_DATA_SCOPE=domain` (fetch the whole continent once —
see [performance tuning](#performance-tuning-optional)). Off CGLabs (laptop),
leave `AGWISE_LOCAL_ROOT` unset and use a personal `AGWISE_DATA_ROOT`.

## 3. Credentials (per user)

Personal, never shared. The full click-by-click (CDS + Earth Engine, from zero,
with troubleshooting) is in **[credentials_setup.md](credentials_setup.md)**. On
the shared server, each person keeps their own `~/.cdsapirc` and
`~/.config/earthengine/credentials` (`chmod 600`) and sets `AGWISE_GEE_PROJECT`
— never commit a token or put one in a shared folder. Note: while the UCSB host
is 403-blocked, even CHIRPS rainfall routes through Earth Engine, so `PRCP`
needs GEE set up too.

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
```

The biggest speed-up is **not downloading at all**: `AGWISE_LOCAL_ROOT`
(§2) reads already-staged data from `Landing` — a region-year then loads in
seconds instead of minutes on the CDS queue.

With the default `auto` scope, a country-scale CHIRPS request reads only
that country's window from UCSB's daily COGs (a Rwanda year stores ~1 MB);
Africa-scale requests download the yearly global NetCDF (~1.1 GB) once
over parallel connections and every country afterwards reuses it. Requests
against providers are deliberately paced — if a server rate-limits us the
driver falls back to a gentler path automatically.

## Portability

Nothing here is CGLabs-specific: the roots are just paths. On a laptop use local
folders (§2); where a shared bucket exists, mount it and point the roots there —
the code doesn't change.
