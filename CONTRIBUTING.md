# Contributing to agwise-data

Thanks for improving the AgWise data-sourcing layer. This is a short guide to
the dev workflow and the conventions the codebase follows.

## Dev setup & tests

```bash
conda env create -f environment.yml && conda activate agwise_data
pip install -e ".[dev]"
pytest -q
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

- **Data is shared, credentials are personal.** Read shared raw inputs from
  `Global_GeoData/Landing` (`AGWISE_LOCAL_ROOT`, read-only), cache new region
  downloads in the shared `Global_GeoData/Processed` (`AGWISE_DATA_ROOT`), and
  write your outputs under `Data/useCase_<name>` (each writer's `out_dir`) —
  never write into `Landing`. While developing, use a throwaway `AGWISE_DATA_ROOT`.
  Never commit tokens or put them in a shared folder (see `docs/credentials_setup.md`).
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
   `TOPO.*`/`RS.*`/`LC.*` names and units.
4. **API/CLI/R** — expose it via a function in `api.py` (add to `__all__` and
   `__init__.py`), a CLI subcommand in `cli.py`, and an `ad_*` wrapper in
   `r/agwise_data.R`.
5. **Tests + docs** — add a network-free test, document the function in
   `REFERENCE.md`, and add a `CHANGELOG.md` entry + version bump.

## Documentation conventions

The docs are organized as a numbered path (the
[documentation map](README.md#documentation-map--read-in-this-order) in the
README). Each doc has **one job** and one home for each topic:

| Section | File | Owns |
| --- | --- | --- |
| 1 How it works, 2 Installation | `README.md` | workflow, folders, install essentials |
| 2 (server deep-dive) | `docs/cglabs_setup.md` | shared-server roots, R, performance |
| 3 Credentials | `docs/credentials_setup.md` | CDS + GEE create/configure/verify |
| 4 Workflow, 5 Interfaces | `docs/user_guide.md` | the dataset/area/period/output tables, Python/R/CLI examples |
| 6 Function reference | `REFERENCE.md` | every function's parameter tables |

- **Don't duplicate** setup steps, folder explanations or a doc list across
  files — link to the one canonical place instead.
- When you add or change a public function, keep in step: its docstring, its
  `REFERENCE.md` entry, the dataset/interface tables in `docs/user_guide.md`,
  and an `examples/` line if it opens a new workflow.
- README/REFERENCE/user-guide snippets are expected to run — verify them.

## Section 7 — General improvements (docs & UX)

Concrete, prioritized ideas to keep the "use it without reading the code" goal
true as the module grows:

1. **Auto-generate the REFERENCE parameter tables from the signatures.** The
   Section 6 tables were produced from `inspect.signature` so they can never
   drift from or invent a parameter. Committing that generator as a small
   `scripts/gen_reference.py` + a CI check (regenerate → `git diff --exit-code`)
   would guarantee the reference stays correct with zero manual upkeep. *(High
   value, low effort — the biggest maintenance win.)*
2. **Accept an arbitrary AOI polygon.** Today the area is `country` / admin unit
   / `bbox` / `points` only. Adding a `geometry=` (shapefile/GeoJSON) input to
   `_resolve_region` would remove the most common "how do I clip to my polygon?"
   question and is a natural extension of the existing admin-polygon clip.
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

## Commits & CI

Trunk-based: commit and push to `origin/main`; CI (`.github/workflows/tests.yml`)
runs the pytest matrix on every push. Keep it green.
