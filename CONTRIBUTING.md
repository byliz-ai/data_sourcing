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

It reproduces the README [first success](README.md#first-success-no-credentials-needed)
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

- Each doc has **one job** — the canonical
  [documentation map](README.md#documentation-map) in the README lists them
  (README = entry, `REFERENCE.md` = function reference, `docs/` = credential &
  server setup, `examples/` = runnable scripts). Keep them concise and
  cross-linked; **don't duplicate** setup steps or a doc list across files —
  add a link to the one canonical place instead.
- When you add or change a public function, keep the README
  [task table](README.md#what-do-you-want-to-do) and its `REFERENCE.md` entry
  in step, and add an `examples/` line if it opens a new workflow.
- Every public function has a docstring (summary, params, returns, example
  where useful) that matches its signature. Update the docs when the API
  changes — README/REFERENCE/`examples/` snippets are expected to run.

## Commits & CI

Trunk-based: commit and push to `origin/main`; CI (`.github/workflows/tests.yml`)
runs the pytest matrix on every push. Keep it green.
