# Examples

Runnable quickstarts. **Run them from the repo root**, after installing the
package (see [README §2.2](../README.md#22-install)).

| File | Run it | Needs |
| --- | --- | --- |
| [`quickstart.py`](quickstart.py) | `python examples/quickstart.py` | steps 1–2: network only · steps 3–4: credentials |
| [`quickstart.R`](quickstart.R) | `Rscript examples/quickstart.R` (or source in RStudio) | same |

Both scripts do the same thing in each language:

1. **Soil at points** (SoilGrids) — *no account needed*, runs as-is.
2. **Rainfall cube** for Rwanda (CHIRPS) — needs **Earth Engine** right now (the
   UCSB host is 403-blocked, so the driver falls back to CHIRPS on Earth Engine);
   guarded so the script still finishes without it.
3. **DSSAT input files** (commented out) — needs Copernicus CDS credentials.
4. **NDVI** (Python, commented out) — needs Google Earth Engine credentials.

Set your data roots first — on CGLabs there is nothing to set (the
[three data folders](../README.md#12-the-three-data-folders--each-with-one-job)
are the defaults); off CGLabs point them at a personal cache, per
[docs/cglabs_setup.md §2](../docs/cglabs_setup.md#2-data-roots--already-configured-on-cglabs).

Steps 2–4 need credentials — the click-by-click setup is in
[`docs/credentials_setup.md`](../docs/credentials_setup.md). For every function
and its parameters, see [`REFERENCE.md`](../REFERENCE.md).

**Going further:** to *reuse data your team already downloaded* (skip the
network entirely), set `AGWISE_LOCAL_ROOT` — see
[`docs/cglabs_setup.md`](../docs/cglabs_setup.md#performance-tuning-optional).
For soil you can also choose the source: `source="isda"` (iSDA) vs the default
SoilGrids.
