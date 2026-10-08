# Contributing to agwise-data

Thanks for improving the AgWise data-sourcing layer. This is a short guide to
the dev workflow and the conventions the codebase follows. Working with an AI
assistant? It should read [AGENTS.md](AGENTS.md) first. That file has the code
map and the invariants below in compact form.

## Dev setup & tests

This is a **personal dev env** (a named env in your own home, with the `[dev]`
test extras) — for working on the code. It is separate from the **shared-server**
install (a shared-prefix env, built per
[docs/cglabs_setup.md §1](docs/cglabs_setup.md#1-install-once-per-server) and
activated per [README §2.2](README.md#22-install)).

```bash
conda env create -f environment.yml && conda activate agwise_data
pip install -e ".[dev]"
pytest -q -o faulthandler_timeout=60   # ~17 s; prints thread stacks if a test hangs
```

The test suite is **network-free and needs no credentials** (drivers are
mocked with synthetic data via `tests/conftest.py`), so it runs anywhere and
in CI on every push. One test — `tests/test_modis.py::test_modis_tif_band_labels`
— can fail on machines whose GDAL lacks GeoTIFF *update* mode (a local
environment quirk); it passes in CI. Everything else must stay green.

New behaviour needs a test. Prefer network-free tests using the fake drivers
and `config` fixture in `conftest.py`; verify live paths (CDS/GEE) manually and
note the result in the commit/REFERENCE rather than adding a networked test.

### Optional: the clean-user smoke test

`scripts/smoke_test.sh` is an **optional, networked** end-to-end check — run it
by hand after an install or a change to the download/cache path:

```bash
bash scripts/smoke_test.sh          # ~2-4 min the first time
```

It reproduces the README [first success](README.md#24-first-success-no-credentials-needed)
a brand-new user hits — list the catalog, then two real **no-credential**
fetches (Copernicus DEM `ELEV` + SoilGrids `CLAY` clipped to a county) — and
asserts the cache filled up, so it exercises install + network + clipping +
cache for someone with zero credentials. It writes only to a throwaway `mktemp`
cache it deletes on exit and never touches the shared `Landing` tree. It is
**not** part of `pytest`/CI (which stays network-free) — keep it that way.

## Ground rules (shared server / cache)

- **Data is shared, credentials are personal.** The shared inputs/cache vs your
  outputs are explained in
  [README §1.2](README.md#12-the-three-data-folders--each-with-one-job); while
  developing, point `AGWISE_DATA_ROOT` at a throwaway cache, never write into
  `Landing`, and never commit tokens or put them in a shared folder (see
  `docs/credentials_setup.md`).
- Only create/modify files inside the repo (or your own test root). Treat the
  shared `common_data` and other repos as read-only inputs.

## Adding a new data source

The layer is `catalog → driver → harmonize → cache → api`. To add a source:

1. **Catalog** — add a YAML in `src/agwise_data/catalog/` describing the source
   (id, access, variables, units, `conversion`), following an existing file.
2. **Driver** — subclass the right base in `src/agwise_data/drivers/`
   (`Driver` for time series, `StaticDriver`, `SeasonalDriver`, `ModisDriver`),
   implement the fetch method, and `@register("<driver-id>")` it.
3. **Harmonize** — add the canonical variable (name, units, conversion) to
   `src/agwise_data/harmonize.py` so outputs use the shared `AGRO.*`/`SOIL.*`/
   `TOPO.*`/`RS.*`/`LC.*` names and units. Declare the raster's `nodata` in the
   catalog so it is masked **before** the unit conversion.
4. **Quality ranges** — add the variable to `src/agwise_data/qc_ranges.yaml`
   (same units as `harmonize.py`; a *physical* range for impossible values and
   a broader-than-you-think *plausible* range). `tests/test_qc.py` checks the
   units and that plausible ⊆ physical. Before you commit, check the ranges on
   real data: they must flag ~nothing on a normal region.
5. **API/CLI/R** — expose it via a function in `api.py` (add to `__all__` and
   `__init__.py`), a CLI subcommand in `cli.py`, and an `ad_*` wrapper in
   `r/agwise_data.R`.
6. **Tests + docs** — add a network-free test, document the function in
   `REFERENCE.md`, and add a `CHANGELOG.md` entry + version bump.

## Documentation conventions

The docs are organized as a numbered path (the
[documentation map](README.md#documentation-map--read-in-this-order) in the
README). Each doc has **one job** and one home for each topic:

| Section | File | Owns |
| --- | --- | --- |
| 1 How it works, 2 Installation | `README.md` | workflow, folders, user activation (already-installed) |
| 2 (server deep-dive) | `docs/cglabs_setup.md` | from-scratch / shared-server install, roots, R, performance |
| 3 Credentials | `docs/credentials_setup.md` | CDS + GEE create/configure/verify |
| 4 Workflow, 5 Interfaces | `docs/user_guide.md` | the dataset/area/period/output tables, Python/R/CLI examples |
| 6 Function reference | `REFERENCE.md` | every function's parameter tables |
| Data quality | `docs/quality_control.md` | QC behaviour, default ranges, reports, sources/credits |
| AI assistants | `AGENTS.md` (`CLAUDE.md` imports it) | compact task→function map, code map, invariants |
| History | `CHANGELOG.md` | what changed per version and **why** (include real-data evidence) |

- **Don't duplicate** setup steps, folder explanations or a doc list across
  files — link to the one canonical place instead.
- When you add or change a public function, keep in step: its docstring, its
  `REFERENCE.md` entry, the dataset/interface tables in `docs/user_guide.md`,
  and an `examples/` line if it opens a new workflow.
- README/REFERENCE/user-guide snippets are expected to run — verify them.
- **Write for two readers.** A researcher who doesn't read code: say what
  happens to their data, in plain words, with a runnable example. An AI
  assistant: name the exact function, parameter and file. Put facts in tables,
  not long paragraphs.
- **Credit external sources.** When an idea comes from another project or
  paper, link it (for repositories, a permalink to the exact commit) in the
  relevant doc. See [quality_control.md §7](docs/quality_control.md#7-sources-and-credits).

## Section 7 — General improvements (docs & UX)

Concrete, prioritized ideas to keep the "use it without reading the code" goal
true as the module grows:

1. **Auto-generate the REFERENCE parameter tables from the signatures.** The
   Section 6 tables were produced from `inspect.signature` so they can never
   drift from or invent a parameter. Committing that generator as a small
   `scripts/gen_reference.py` + a CI check (regenerate → `git diff --exit-code`)
   would guarantee the reference stays correct with zero manual upkeep. *(High
   value, low effort — the biggest maintenance win.)*
2. **~~Accept an arbitrary AOI polygon.~~ Done (v0.17.0).** `geometry=` (a
   shapefile/GeoJSON path, GeoDataFrame, shapely geometry, or GeoJSON mapping;
   `--aoi` on the CLI, `aoi=` in R) now clips every gridded call and `make_grid`
   to a user-uploaded zone via `boundaries.load_aoi`. Possible follow-ups: cache
   the uploaded geometry itself, and accept a remote URL.
3. **Surface progress for slow fetches.** A cold AgERA5/SEAS5 pull sits in the
   CDS queue for minutes with no feedback; a one-line "expect minutes; cached
   after" message (or a progress hook) would prevent "is it stuck?" confusion.
4. **A single copy-paste environment block.** New users set 3–4 env vars
   (`AGWISE_LOCAL_ROOT`, `AGWISE_DATA_ROOT`, `HDF5_USE_FILE_LOCKING`,
   `AGWISE_GEE_PROJECT`) across two docs. A ready-made `env.sh.example` to
   `source` would cut setup to one step and one place to maintain.
5. **A `doctor`/`verify` subcommand.** `agwise-data doctor` could check the env
   vars, `~/.cdsapirc`, the GEE credentials file + project, and the cache path
   in one command — replacing the scattered manual verification snippets in
   Section 3 with a single self-test.
6. **Keep navigation shallow.** The numbered doc map is the one index; when a
   new topic appears, extend an existing section rather than adding a new
   top-level file, so the 1→7 path stays the whole map.

**Data-quality / provenance roadmap:** a comparison with
[prismpy](https://github.com/izuku-franck1555/prismpy) — known defects plus a
phased plan (QC ranges, provenance manifests, SPAM, IDW, NASA POWER, HWSD,
ISIMIP3b) — is in [docs/prismpy_comparison.md](docs/prismpy_comparison.md).
**Phase 0 (fixes) and Phase 1 (quality control) are done** (v0.33.0–v0.36.1);
next is Phase 2 (provenance and reproducibility).

## Lessons that shaped the code (don't undo them)

- **Product NetCDF writes run on dask's synchronous scheduler.** With threads,
  a write that streams from lazily opened NetCDFs deadlocked about 1 run in 5
  on xarray's HDF5 locks (v0.35.0). Never add other computations to a write's
  dask graph; compute them in a separate pass.
- **Missing rain is never zero:** monthly sums use `skipna=False`; writers
  never gap-fill rain.
- **Check an external rule on real data before adopting it.** prismpy drops a
  soil layer whose texture is >5 % off 100 %; on SoilGrids that blanked the
  topsoil of ~1/3 of profiles (fixed in v0.36.1).

## Commits & CI

Trunk-based: commit and push to `origin/main`; CI (`.github/workflows/tests.yml`)
runs the pytest matrix on every push. Keep it green.
