# agwise-data — the AgWise data-sourcing module

**For AgWise researchers and module developers** (working in Python *or* R) who
need analysis-ready climate, soil, terrain and remote-sensing inputs — **without
reading the source code**. One call fetches, harmonizes, **quality-checks** and
caches the data. The same data is downloaded **once** into a shared cache with
agreed names and units (`PRCP` in mm/day, `CLAY` in %, …) and everyone reuses it
afterwards.

**In one minute:**

```python
from agwise_data import get_climate, extract_static_points, to_dssat
get_climate("PRCP", years=range(2015, 2025), country="Rwanda", freq="monthly")  # a cube
extract_static_points("trials.csv", ["CLAY", "PH", "SOC"])                      # a table
to_dssat("trials.csv", planting_date="2021-03-01", harvest_date="2021-07-31",
         out_dir="DSSAT", station_col="site")                                   # model files
```

(Temperature and radiation come from AgERA5, which needs a free Copernicus
account. Soil, terrain and rainfall on CGLabs need none; see
[Section 3](docs/credentials_setup.md).)

Same in R (`ad_get_climate(...)`) and on the command line
(`agwise-data get ...`). See [what you get back](#15-what-you-get-back).

## New here? Let an AI assistant set you up — and drive the module for you

The fastest way through **everything below** is to not do it by hand: use the
**AI assistant you prefer** (Claude Code, OpenAI Codex, Gemini CLI, GitHub
Copilot, Cursor, ChatGPT…) and ask in plain language. Tell it first to read
**[AGENTS.md](AGENTS.md)**, the repo's guide for any AI. A terminal agent
started on CGLabs in a folder you own runs the same public functions documented
here and reports back. A browser chat writes the code and you run it. New
teammates: start from the [onboarding guide](docs/onboarding.md).

| Ask it to… | Example prompt | Covers |
| --- | --- | --- |
| **Install & verify** | *"Set me up to use agwise-data: register the shared env and run a first no-credential fetch to confirm it works."* | Sections 1–2 |
| **Configure credentials** | *"Help me create and configure my Copernicus CDS and Earth Engine credentials, then verify both."* | Section 3 |
| **Fetch data & build model inputs** | *"Get monthly CHIRPS rainfall for Rwanda 2015–2024 and tell me where the cube is cached"* · *"Pull weather for these trial points and write DSSAT files for a March–July season."* | Sections 4–6 |

Multi-step work is where it shines — one request instead of chaining calls by
hand. Ask it to *show each command before running* if you want to review first,
and remember the golden rule: it will place **your** credentials in **your**
home, never in shared folders. Details, which assistants load `AGENTS.md`
on their own, and more examples:
[user guide §5.3](docs/user_guide.md#53-ai-assistants--plain-language-use-the-one-you-prefer).

Prefer to drive yourself — or want to understand what it's doing? Follow the
documentation map below; the assistant and the manual path are interchangeable
at any point.

## Documentation map — read in this order

You do **not** need to understand the code to use this tool. Follow these pages
top to bottom; each is a self-contained step of the journey.

| # | Section | Where | Read it to… |
| --- | --- | --- | --- |
| 0 | **Onboarding (new teammates)** | [docs/onboarding.md](docs/onboarding.md) | get access to the shared space, activate the env, know where everything lives |
| 1 | **How the project works** | this page ↓ | understand the workflow, the folders, the cache, and where files land |
| 2 | **Installation** | this page ↓ + [docs/cglabs_setup.md](docs/cglabs_setup.md) | install on CGLabs (or a laptop) and check it works |
| 3 | **Credentials** | [docs/credentials_setup.md](docs/credentials_setup.md) | create / configure / verify Copernicus + Google Earth Engine |
| 4 | **User workflow** | [docs/user_guide.md](docs/user_guide.md) | choose your area, datasets, time period and output |
| 5 | **User interface (Python / R / CLI / AI assistant)** | [docs/user_guide.md](docs/user_guide.md#5-user-interface--python--r--cli--ai-assistant) | run the same task in the language you prefer |
| 6 | **Function documentation** | [REFERENCE.md](REFERENCE.md) | look up every function: parameters, types, defaults, examples |
| 6b | **Data quality (QC)** | [docs/quality_control.md](docs/quality_control.md) | understand why a value is `NaN`, what a `QCWarning` means, how to read `qc_report.json` |
| 6c | **Provenance** | [docs/provenance.md](docs/provenance.md) | cite the data (`METHODS.md`), check files with `manifest.json`, read `.meta.json` |
| 7 | **General improvements** | [CONTRIBUTING.md](CONTRIBUTING.md) | maintainer notes and suggested next steps |

Runnable end-to-end scripts (Python + R) are in **[examples/](examples/)**;
release history is in **[CHANGELOG.md](CHANGELOG.md)**. AI assistants (Claude
Code, Codex, …) have their own compact guide: **[AGENTS.md](AGENTS.md)**.

---

## 1. How the project works

### 1.1 The workflow in one picture

You ask for **variables** (`PRCP`, `CLAY`, `NDVI`, …) over a **region** and a
**time period**. The tool:

1. finds the right data source;
2. downloads only what you asked for;
3. converts it to the agreed names and units;
4. caches it;
5. checks its quality;
6. hands you an analysis-ready result: a data cube, a table of points, or a
   crop-model input file.

You never touch the raw download formats.

```text
   you ask ─────────────────────────────────────────────► you get
   variables + region + period          analysis-ready output

        │                                        ▲
        ▼                                        │
   ┌──────────────────────────────────────────────────────────────────┐
   │  catalog  →  driver  →  harmonize  →  shared cache  →  QC         │
   │  (which    (download   (agreed       (download once,  (impossible │
   │   source)   it)         names/units)  reuse forever)   → NaN,     │
   │                                                        report)    │
   └──────────────────────────────────────────────────────────────────┘
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

### 1.4 Data quality is checked for you

Every result is checked before you get it ([details](docs/quality_control.md)):

- **Impossible values become missing.** Negative rain, pH 25.5, solar
  radiation above the top-of-atmosphere maximum.
- **Unusual but real values are kept.** You get a `QCWarning` about them.
- **Missing rainfall is never counted as 0 mm.**
- **Crop-model files are checked before and after writing.** TMIN/TMAX are
  ordered, gaps of up to 5 days in temperature or radiation are filled (rain is
  never filled), and soil texture sums to 100 %. Each file is read back and
  validated.

Each result comes with a small report (`*.qc.json`, or `qc_report.json` for
crop-model runs). `qc="strict"` also removes the unusual values, and
`qc="off"` gives you the raw data.

### 1.5 What you get back

| You call | You get | Files written |
| --- | --- | --- |
| `get_climate`, `get_static`, `get_seasonal`, `get_season`, `get_modis` | `{variable: {"nc", "tif", "qc", "data"}}`; `data` is an `xarray.DataArray` (`get_modis` and `get_season` return no `"qc"`; a climate season slice inherits the checks of its `get_climate` product) | `<Kind>_<VAR>_….nc` (+ `.tif` if asked) + `.meta.json` (provenance) + `.qc.json` (quality) |
| `extract_points`, `extract_growing_season`, `extract_static_points` | a `pandas.DataFrame` (QC in `df.attrs["qc"]`) | a CSV when run from the CLI/R (+ `.qc.json`) |
| `to_dssat`, `to_apsim`, `to_wofost`, `to_oryza` | a list, one entry per point, with its files and `"qc"` | one `EXTE<n>/` folder per point + `qc_report.json` + `manifest.json` + `METHODS.md` ([provenance](docs/provenance.md)) |

Gridded products land in the shared cache (`Processed/products/<region>/`)
unless you pass `out_dir=`. Crop-model files land in your `out_dir`.

---

## 2. Installation

### 2.1 Required software

**On CGLabs you already have all of this** — the env is installed and the cache
is preconfigured, so skip to [§2.2](#22-install) and activate (it opens with the
one prerequisite: access to the shared space). The table below is
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

> **Prerequisite on CGLabs — access to the shared space.** Everything below
> lives under `/home/jovyan/agwise-datasourcing`, which is **not a folder**: it
> is the CGLabs shared space `AgWise_dataSourcing`, mounted over NFS only for
> accounts that are members of it. Check before you start:
>
> ```bash
> mountpoint -q /home/jovyan/agwise-datasourcing \
>   && echo "OK — shared space mounted" \
>   || echo "MISSING — request access"
> ```
>
> If it is MISSING, ask the CGLabs admins to **add your account to the shared
> space `AgWise_dataSourcing`**, then **stop and start your server** from the Hub
> control panel (mounts are applied when the container starts — reloading the
> page is not enough). Don't work around it by cloning the repo or building a
> personal env into that path: the mount will hide the clone, and a personal env
> named `agwise_data` then wins by name over the shared one (see the note below).
> Full explanation: [docs/onboarding.md §0](docs/onboarding.md).

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
├── AGENTS.md              ← guide for any AI assistant
├── CLAUDE.md  GEMINI.md   ← one-line pointers to AGENTS.md
├── CHANGELOG.md  CONTRIBUTING.md
├── docs/
│   ├── onboarding.md          ← new teammates start here
│   ├── credentials_setup.md   ← Section 3
│   ├── cglabs_setup.md        ← Section 2 (shared-server deep dive)
│   ├── user_guide.md          ← Sections 4–5
│   ├── quality_control.md     ← data quality checks and reports
│   ├── provenance.md          ← manifests, checksums, methods text
│   └── prismpy_comparison.md  ← comparison with prismpy + roadmap (Spanish)
├── examples/              ← runnable quickstart.py / quickstart.R
├── src/agwise_data/       ← the Python package (you don't need to read it)
│   └── qc_ranges.yaml         ← default quality-control ranges
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
> CHIRPS v3.0** series staged in `Landing` (1981–2025) — no account, no network.
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
- **[Section 5 — User interface](docs/user_guide.md#5-user-interface--python--r--cli--ai-assistant):**
  the same tasks in Python, R, the CLI, or plain language via the AI assistant
  you prefer.
- **[Section 6 — Function documentation](REFERENCE.md):** every public function
  with all its parameters, types, defaults and a runnable example.
- **[Data quality](docs/quality_control.md):** what is checked, what a
  `QCWarning` means, and how to read the reports.
- **[Provenance](docs/provenance.md):** where each file came from, the
  run manifest, and a ready-made methods paragraph with citations.
- **[Section 7 — General improvements](CONTRIBUTING.md):** maintainer notes.

## License

MIT — see [LICENSE](LICENSE).
