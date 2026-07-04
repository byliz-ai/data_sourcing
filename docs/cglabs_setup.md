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

## 2. Shared data root (once)

Pick a location every AgWise user can read and write, e.g.:

```bash
mkdir -p /home/jovyan/common_data/agwise_data
```

Each user adds to their `~/.bashrc` (or R `.Renviron`):

```bash
export AGWISE_DATA_ROOT=/home/jovyan/common_data/agwise_data
```

That's the whole trick: because everyone points at the same root, the first
person to ask for `Kenya PRCP 2005:2024` pays the download; everyone after
that gets a cache hit.

## 3. CDS credentials (per user, for AgERA5)

(First time on Copernicus or Google Earth Engine? There is a from-zero,
click-by-click guide in [credentials_setup.md](credentials_setup.md).)

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
