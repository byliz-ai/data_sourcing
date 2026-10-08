# Provenance and reproducibility

Every file the layer writes says **where its data came from, which version
produced it, and what was done to it**. You can check a file has not changed,
rebuild the same result later, and cite the data correctly, without keeping
notes by hand.

| You want to… | Look at |
| --- | --- |
| Cite the data in a report or paper | `<out_dir>/METHODS.md`, or `methods_text(path)` |
| Know which datasets and versions a crop-model run used | `<out_dir>/manifest.json` → `variables`, `sources` |
| Check that files were not modified or corrupted | `manifest.json` → `files[].sha256` |
| Compare two runs | `diff run1/manifest.json run2/manifest.json` |
| Know where a cached cube came from | `<file>.meta.json` next to it |
| See the source inside a `.WTH`/`.SOL` | its `!` comment lines |

---

## 1. Crop-model runs — `manifest.json` and `METHODS.md`

`to_dssat`, `to_apsim`, `to_wofost`, `to_oryza` and `forecast_to_dssat` write
three files next to the per-point `EXTE<n>/` folders:

| File | Content |
| --- | --- |
| `qc_report.json` | data-quality checks per point ([quality_control.md §5.4](quality_control.md)) |
| `manifest.json` | everything needed to reproduce and audit the run (below) |
| `METHODS.md` | a methods paragraph plus full references, ready to paste |

`manifest.json` fields:

| Field | Meaning |
| --- | --- |
| `agwise_data_version` | package version that wrote the run |
| `function`, `parameters` | the call and its arguments (dates, sources, options). A frame you passed in is recorded as `"<DataFrame supplied>"` |
| `points` | number of points and a SHA256 of their coordinates. Same points give the same digest |
| `variables` | per variable: `source`, `recipe` (see §3), and the `qc` mode and `qc_signature` when range QC ran. `"user-supplied"` when you passed the frame yourself |
| `sources` | title, version, license and citation of each dataset, from the catalog |
| `transforms` | the processing steps, in plain words (extraction, QC, gap-filling, texture rescaling, pedotransfer, wind height, bias correction…) |
| `method_references` | papers behind those steps (FAO-56, Saxton & Rawls 2006, QDM) |
| `qc` | summary of `qc_report.json` |
| `files` | every file written: relative path, SHA256, size |

The manifest is **deterministic**: keys are sorted, paths are relative, and
there are no timestamps. Running the same call into the same `out_dir` gives a
byte-identical manifest. A difference therefore means something really
changed: the data, the parameters or the package version.

Check the files later:

```python
import json, pathlib
from agwise_data.provenance import sha256_file
out = pathlib.Path("DSSAT")
m = json.loads((out / "manifest.json").read_text())
changed = [f["path"] for f in m["files"] if sha256_file(out / f["path"]) != f["sha256"]]
print(changed or "all files unchanged")
```

`qc_report.json` stores each point's folder as an absolute path. Its hash, and
so the manifest, only match between runs written into the **same** `out_dir`.

## 2. Declarations inside the files

The weather and soil files carry comment lines naming the package version and
the sources. The models skip these lines.

```
$WEATHER:
! agwise-data 0.37.0; sources: agera5 v2.0 (SRAD,TMAX,TMIN);
! chirps_v3 v3.0 (PRCP); cop_dem30 v2021 (ELEV) - see manifest.json
```

| File | Prefix | Where |
| --- | --- | --- |
| DSSAT `.WTH` | `!` | after `$WEATHER:` |
| DSSAT `.SOL` | `!` | after `*SOILS:` |
| APSIM `.met` | `!` | after the first comment line |
| ORYZA weather / `.sol` | `*` | in the header |

The WOFOST CSVs and the APSIM soil-layer CSV have no comment syntax, so they
have no declaration. Their provenance is in `manifest.json`.

## 3. Cached files — `<file>.meta.json`

Every cached file has a sidecar. That covers the harmonized yearly files in the
shared cache and the products (`Daily_PRCP_2015_2024.nc`, …). Next to the
request fields (source, variable, region, years, QC), it records:

| Field | Meaning |
| --- | --- |
| `agwise_data_version` | version that wrote the file |
| `sha256`, `bytes` | the file's checksum and size |
| `recipe` | 8-character digest of the catalog's dataset version **and** how this variable is read (source name, statistic, unit conversion, nodata) |
| `catalog_version` | the dataset version from the catalog (harmonized files) |
| `transforms` | steps applied: harmonized units, crop to domain, region subset, range QC, monthly aggregation, smoothing, bias correction |

**The recipe protects the shared cache.** When someone edits how a variable is
read in `src/agwise_data/catalog/*.yaml`, its recipe changes. Every cached file
built with the old recipe is then rebuilt on its next request, both the
harmonized year and the product, so nobody gets data converted the old way.
Edits that do not change the data, such as a description or a mirror URL,
leave the recipe unchanged and keep the cache. Files written before v0.37 have
no recipe and stay valid.

## 4. Methods text

```python
from agwise_data import methods_text
print(methods_text("DSSAT"))                                   # a run folder
print(methods_text("…/Daily_PRCP_2015_2024.nc"))               # a cached product
```

```bash
agwise-data methods DSSAT --out methods.md
```

```r
cat(ad_methods_text("DSSAT"))
```

The text names the agwise-data version, which variables came from which dataset
and version, the processing steps, and the full references. Those are the
dataset citations from the catalog and the method papers. Check it before
publishing: it describes what the layer did, not what you did afterwards.

## 5. Sources

The design follows prismpy's run manifest and the soil-source declaration in
its files. Their permalinks are in
[prismpy_comparison.md §7](prismpy_comparison.md#7-fuentes).
