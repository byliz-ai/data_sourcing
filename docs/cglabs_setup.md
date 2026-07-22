# CGLabs setup — Section 2 (shared-server deep dive)

Installation basics (required software, `git clone` → `conda` → `pip install`,
and the folder structure after install) are in
[README Section 2](../README.md#2-installation). This page covers what is
**specific to the shared server**: installing once for everyone, persisting the
data roots per user, using it from R, and performance tuning.

## 1. Install (once per server)

Install the package + env once, in a location every user can reach. The exact
commands — clone into the shared mount, create the env at a shared **prefix**,
`pip install -e`, and the one-time per-user `conda config --append envs_dirs …`
so `conda activate agwise_data` resolves by name — are in
[README §2.2](../README.md#22-install).

## 2. Data roots — already configured on CGLabs

The **three data folders** — and *why* there are three — are explained in
[README §1.2](../README.md#12-the-three-data-folders--each-with-one-job). On the
standard CGLabs tree **you do not set anything**: the layer defaults to the
shared folders automatically (see `CGLABS_LANDING`/`CGLABS_PROCESSED` in
`src/agwise_data/config.py`), and NFS file locking is handled for you. A new
user reuses the already-downloaded data and the shared cache out of the box.
The two override env vars (only needed off the standard tree) are
`AGWISE_LOCAL_ROOT` (raw inputs → `Landing`) and `AGWISE_DATA_ROOT` (download
cache → `Processed`) — see the relocate block below.

One staged dataset lives outside `Landing`: the Copernicus GLO-30 DEM tiles
(full Africa) at `/home/jovyan/common_data/cop30/raw`, used automatically for
elevation/terrain whenever local reuse is on (its path is set in
`catalog/dem.yaml`, not by an env var).

Only set the env vars to **relocate** the layer — e.g. on a laptop, or to write
to a private cache while testing:

```bash
export AGWISE_DATA_ROOT=~/agwise_data/cache        # a private download cache
export AGWISE_LOCAL_ROOT=/path/to/Global_GeoData/Landing   # or leave unset to just download
```

To move the whole team to a different tree, edit the two paths in
`config.py` once. Reuse is maximised by requesting stable regions
(`country=`/`admin_level=`) or, on a bulk server, `AGWISE_DATA_SCOPE=domain`
(fetch the whole continent once — see
[performance tuning](#performance-tuning-optional)).

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
source("/home/jovyan/agwise-datasourcing/code/data_sourcing/r/agwise_data.R")
# If agwise-data is not on the PATH R sees:
Sys.setenv(AGWISE_DATA_BIN = "/home/jovyan/agwise-datasourcing/envs/agwise_data/bin/agwise-data")

r <- ad_get_climate(vars = "PRCP", years = 2005:2024,
                    country = "Kenya", freq = "monthly")
```

(Adjust the conda env path to wherever `conda env list` says `agwise_data`
lives — on CGLabs the shared prefix env `/home/jovyan/agwise-datasourcing/envs/agwise_data`.)

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

# memory budget (advisory). The layer reads the container's real cgroup
# limit (~32 GB on CGLabs) — not the host RAM `free` shows — sizes the
# worker pool from it, and warns before an op would exceed it. Override
# only if the container is actually larger/smaller, or to change the reserve:
export AGWISE_MEM_LIMIT_GB=32       # force the assumed limit
export AGWISE_MEM_HEADROOM_GB=8     # bytes kept free (NFS write-back + a co-user)

# how many times a CDS download (AgERA5/SEAS5) retries on a transient
# network/queue failure before giving up (default 3). A cold seasonal
# forecast issues many requests, so a dropped download retries instead of
# aborting the whole run:
export AGWISE_CDS_RETRIES=5

# rainfall source when a call pins none. On CGLabs this defaults to the
# local CHIRPS v3.0 (staged in Landing) for the years it covers; set it
# explicitly to pin one version team-wide:
export AGWISE_RAINFALL_SOURCE=chirps       # force CHIRPS v2.0 everywhere
# export AGWISE_RAINFALL_SOURCE=chirps_v3  # (the CGLabs default already)
```

The biggest speed-up is **not downloading at all**: `AGWISE_LOCAL_ROOT`
(§2) reads already-staged data from `Landing` — a region-year then loads in
seconds instead of minutes on the CDS queue.

> **Memory ceiling.** A single call is light (a country-scale climate or DEM
> request peaks around 1–3 GB), but the CGLabs container is capped near
> **32 GB** — and `free` reports the host's much larger RAM, not your limit.
> Two heavy gridded/Earth-Engine jobs running at once (e.g. a MODIS pull and a
> DEM derive) can add up and get one of them OOM-killed. If you script bulk
> runs, prefer running them **sequentially**, and lower `AGWISE_DATA_WORKERS`
> (rather than raising it) when a job clips large regions.

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
