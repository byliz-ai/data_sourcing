# Examples

Runnable quickstarts. **Run them from the repo root**, after installing the
package (`conda activate agwise_data` then `pip install -e ".[all]"` — see the
[main README](../README.md)).

| File | Run it | Needs |
| --- | --- | --- |
| [`quickstart.py`](quickstart.py) | `python examples/quickstart.py` | steps 1–2: network only · steps 3–4: credentials |
| [`quickstart.R`](quickstart.R) | `Rscript examples/quickstart.R` (or source in RStudio) | same |

Both scripts do the same thing in each language:

1. **Rainfall cube** for Rwanda (CHIRPS) — *no account needed*.
2. **Soil at points** (SoilGrids) — *no account needed*.
3. **DSSAT input files** (commented out) — needs Copernicus CDS credentials.
4. **NDVI** (Python, commented out) — needs Google Earth Engine credentials.

Set your cache location first so downloads have a home:

```bash
export AGWISE_DATA_ROOT=~/agwise_data/cache
```

Steps 3–4 need credentials — the click-by-click setup is in
[`docs/credentials_setup.md`](../docs/credentials_setup.md). For every function
and its parameters, see [`REFERENCE.md`](../REFERENCE.md).
