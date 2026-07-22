# agwise-data — the AgWise data-sourcing module

**For AgWise researchers and module developers** (working in Python *or* R) who
need analysis-ready climate, soil, terrain and remote-sensing inputs — **without
reading the source code**. One call fetches, harmonizes and caches the data; the
same data is downloaded **once** into a shared cache with agreed names and units
(`PRCP` in mm/day, `CLAY` in %, …) and reused by everyone afterwards.

## Documentation map — read in this order

You do **not** need to understand the code to use this tool. Follow these pages
top to bottom; each is a self-contained step of the journey.

| # | Section | Where | Read it to… |
| --- | --- | --- | --- |
| 1 | **How the project works** | this page ↓ | understand the workflow, the folders, the cache, and where files land |
| 2 | **Installation** | this page ↓ + [docs/cglabs_setup.md](docs/cglabs_setup.md) | install on CGLabs (or a laptop) and check it works |
| 3 | **Credentials** | [docs/credentials_setup.md](docs/credentials_setup.md) | create / configure / verify Copernicus + Google Earth Engine |
| 4 | **User workflow** | [docs/user_guide.md](docs/user_guide.md) | choose your area, datasets, time period and output |
| 5 | **User interface (Python / R / CLI)** | [docs/user_guide.md](docs/user_guide.md#5-user-interface--python--r--cli) | run the same task in the language you prefer |
| 6 | **Function documentation** | [REFERENCE.md](REFERENCE.md) | look up every function: parameters, types, defaults, examples |
| 7 | **General improvements** | [CONTRIBUTING.md](CONTRIBUTING.md) | maintainer notes and suggested next steps |

Runnable end-to-end scripts (Python + R) are in **[examples/](examples/)**;
release history is in **[CHANGELOG.md](CHANGELOG.md)**.

---

## 1. How the project works

### 1.1 The workflow in one picture

You ask for **variables** (`PRCP`, `CLAY`, `NDVI`, …) over a **region** and a
**time period**. The tool finds the right data source, downloads only what you
asked for, converts it to agreed names and units, caches it, and hands you an
analysis-ready result — a data cube, a table of points, or a crop-model input
file. You never touch the raw download formats.

```text
   you ask ─────────────────────────────────────────────► you get
   variables + region + period          analysis-ready output

        │                                        ▲
        ▼                                        │
   ┌────────────────────────────────────────────────────────┐
   │  catalog  →  driver  →  harmonize  →  shared cache      │
   │  (which    (download   (agreed       (download once,    │
   │   source)   it)         names/units)  reuse forever)    │
   └────────────────────────────────────────────────────────┘
   sources: CHIRPS · AgERA5 · SEAS5 · SoilGrids · iSDA · Copernicus DEM
            · MODIS · ESA WorldCover · geoBoundaries
```

### 1.2 The three data folders — each with one job

On CGLabs the module follows the existing AgWise layout. **Inputs are shared,
your outputs stay yours**, so there are three folders under
`…/datasourcing/Data/`:

| Folder | Holds | Role | You point at it with |
| --- | --- | --- | --- |
| `Global_GeoData/Landing` | raw **global** source data, already downloaded | **reusable input** · read-only | `AGWISE_LOCAL_ROOT` |
| `Global_GeoData/Processed` | **region** slices the tool downloads + harmonizes | **cache** · shared · read/write | `AGWISE_DATA_ROOT` |
| `useCase_<Country>_<Name>/` | the files **you** produce (DSSAT/APSIM/…, CSVs) | your outputs | each writer's `out_dir` |

- **Reusable data:** `Landing` (raw inputs staged once) and `Processed` (the
  download cache). Anything already in either is reused — no re-download.
- **Your downloads:** land in `Processed`, keyed by region, and are shared —
  the next person who asks for the same region gets an instant cache hit.
- **Your outputs:** go wherever you set `out_dir` (typically your
  `useCase_<…>/result/` folder). They are never mixed into the shared cache.

### 1.3 What happens when data is *not* already on disk

A request flows top to bottom and stops at the first place that already has the
data:

```text
  ① Is it in Landing (raw, read-only)?   ── yes ─►  read + clip to your region, NO download
        │ no
  ② Is it in Processed (the cache)?       ── yes ─►  reuse the cached region slice
        │ no
  ③ Download just your region from the    ───────►  save it to Processed (shared),
     source, harmonize names/units                  then everyone reuses it next time
```

So a first request for a new region downloads only that region's window and
caches it; every later request for the same region — by anyone — is a cache
hit. Nothing global is re-downloaded once it is in `Landing`.

> **Golden rule (shared servers):** *data is shared, credentials are personal.*
> Everyone points at the same shared cache, but each person keeps their **own**
> tokens in their **own** home (`chmod 600`) — never in the repo, a notebook, or
> the shared folder. See [Section 3](docs/credentials_setup.md).

---

## 2. Installation

### 2.1 Required software

**On CGLabs you already have all of this** — the env is installed and the cache
is preconfigured, so skip to [§2.2](#22-install) and activate. The table below is
what a *from-scratch* install (a laptop, or a new server) needs.

| Requirement | Needed for |
| --- | --- |
| **conda** (Miniconda/Anaconda) + **git** | a from-scratch install (creating the `agwise_data` Python ≥ 3.10 env); **not needed on CGLabs** |
| A **cache folder** (`AGWISE_DATA_ROOT`) | where downloads are cached (on CGLabs: the shared `Global_GeoData/Processed`, already set) |
| *(optional)* **R** ≥ 4.0 | only if you use the `ad_*` R wrappers |
| *(optional)* Copernicus CDS + Google Earth Engine accounts | only for the sources that need them — see [Section 3](docs/credentials_setup.md) |

Soil (SoilGrids/iSDA), terrain (Copernicus DEM) and admin boundaries
(geoBoundaries) need **no account**, so you get a first result with no
credentials at all.

### 2.2 Install

**On CGLabs the module is already installed — you don't clone or install
anything.** Register the shared environment once, then activate it each session:

```bash
# Once per user — so `conda activate agwise_data` finds the shared env by name:
conda config --append envs_dirs /home/jovyan/agwise-datasourcing/envs

# Every session (puts the `agwise-data` command on your PATH):
conda activate agwise_data
```

(If you also keep a personal env named `agwise_data` — e.g. you develop the
code — that one wins by name; activate the shared one by its full path instead:
`conda activate /home/jovyan/agwise-datasourcing/envs/agwise_data`.)

**There is nothing else to configure** — the two shared data folders are
the built-in defaults, so you reuse the already-downloaded data and the
shared cache out of the box:

- reusable raw inputs (read-only): `…/Global_GeoData/Landing`
- shared download cache (read/write): `…/Global_GeoData/Processed`

Override them only to relocate the layer (e.g. on a laptop) — the `export`
commands and R/`.Renviron` setup are in
**[docs/cglabs_setup.md §2](docs/cglabs_setup.md#2-data-roots--already-configured-on-cglabs)**.

**Installing from scratch** is only needed on a laptop or when standing up a
**new** shared server (already done on CGLabs) — clone the repo, `conda env
create -f environment.yml`, `pip install -e ".[all]"` (`.[dev]` for tests). Full
steps, and the shared-prefix layout that lets one install serve every user, are
in **[docs/cglabs_setup.md §1](docs/cglabs_setup.md#1-install-once-per-server)**.

For the sources that need an account (CDS, Earth Engine), set your **own**
credentials next — see [Section 3](docs/credentials_setup.md).

### 2.3 Folder structure after installation

```text
data_sourcing/
├── README.md              ← you are here (Sections 1–2)
├── REFERENCE.md           ← Section 6: every function, every parameter
├── CHANGELOG.md  CONTRIBUTING.md
├── docs/
│   ├── credentials_setup.md   ← Section 3
│   ├── cglabs_setup.md        ← Section 2 (shared-server deep dive)
│   └── user_guide.md          ← Sections 4–5
├── examples/              ← runnable quickstart.py / quickstart.R
├── src/agwise_data/       ← the Python package (you don't need to read it)
└── r/agwise_data.R        ← the R wrappers (ad_*)
```

### 2.4 First success (no credentials needed)

Confirm the install works — no accounts required (activate the environment
first, so the `agwise-data` command is on your PATH):

```bash
conda activate agwise_data        # from §2.2 — without it, `agwise-data` is "command not found"
agwise-data catalog list          # list the data sources and variables you can pull
agwise-data get-static --vars ELEV --country Kenya --admin-level 1 --admin-name Nakuru
agwise-data cache info            # see what landed in the cache
```

Got a NetCDF path back? You're ready. Add credentials
([Section 3](docs/credentials_setup.md)) for the sources that need them, then
follow the **[user workflow (Section 4)](docs/user_guide.md)**.

> **Rainfall (CHIRPS) note:** On CGLabs `PRCP` is served from the **local
> CHIRPS v3.0** series staged in `Landing` (1981–2023) — no account, no network.
> Elsewhere, or for years outside that range, it falls back to CHIRPS v2.0;
> because the UCSB host (`data.chc.ucsb.edu`) is **currently returning HTTP
> 403**, v2.0 is pulled from **Earth Engine** (so needs GEE set up, like MODIS).
> Force a version with `source="chirps"` / `source="chirps_v3"`.

---

## 3–7. The rest of the documentation

- **[Section 3 — Credentials](docs/credentials_setup.md):** create, configure and
  verify Copernicus CDS and Google Earth Engine, click-by-click.
- **[Section 4 — User workflow](docs/user_guide.md):** choose your study area,
  datasets, time period and output type.
- **[Section 5 — User interface](docs/user_guide.md#5-user-interface--python--r--cli):**
  the same tasks in Python, R and the CLI.
- **[Section 6 — Function documentation](REFERENCE.md):** every public function
  with all its parameters, types, defaults and a runnable example.
- **[Section 7 — General improvements](CONTRIBUTING.md):** maintainer notes.

## License

MIT — see [LICENSE](LICENSE).
