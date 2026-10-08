# Data quality control (QC) — what the layer checks for you

Since **v0.34**, every dataset the layer hands you is checked first. This page
covers what is checked, what happens to bad values, how to change the checks,
and how to read the reports. You do not have to do anything to get QC: it is
**on by default**.

> **In one sentence:** impossible values become missing (`NaN` / `-99`),
> unusual-but-possible values are kept and you get a warning, missing rainfall
> is never counted as zero, and every crop-model file is read back and checked
> after it is written.

---

## 1. What happens to your data — at a glance

| Situation | Example | What the layer does | Where you see it |
| --- | --- | --- | --- |
| Value is **physically impossible** | rainfall −3 mm, pH 25.5, 70 °C | set to missing (`NaN`) | `.qc.json` report + `QCWarning` |
| Value is **unusual but possible** | 620 mm of rain in a day, pH 10.4 | **kept**, counted | `.qc.json` report + `QCWarning` |
| Solar radiation above the top-of-atmosphere maximum | SRAD 45 MJ m⁻² d⁻¹ at the equator | set to missing | `.qc.json` (`srad_above_extraterrestrial`) |
| A **rainfall day is missing** | a gap in the source | never treated as 0 mm: a monthly total or season total that includes it is `NaN` | the value itself |
| Raster **nodata code** without a declaration | 255 in an integer soil file | removed **before** unit scaling (so 255 never becomes pH 25.5) | — |
| **TMIN > TMAX** on a day (crop-model files) | TMIN 21, TMAX 19 | the two are swapped | `qc_report.json` |
| **Short gap** in TMAX/TMIN/SRAD (crop-model files) | 1–5 missing days | linearly interpolated | `qc_report.json` (dates listed) |
| **Long gap**, or **any** rainfall gap (crop-model files) | 10 missing days | left missing (`-99`) | `qc_report.json` + validation warning |
| Soil **clay + silt + sand ≠ 100 %** | 93.4 % | rescaled to 100 %; deviations over 3 % / 5 % are flagged | `qc_report.json` |
| A **written file** is malformed | missing date, `nan` in a fixed-width row | logged, flagged as failed | `qc_report.json` + `QCWarning` |

---

## 2. Range checks — two levels per variable

Every variable has two ranges, in the units you receive:

- **physical** — outside it the value cannot exist. It is almost always a
  nodata code or a scaling error, so it is set to `NaN`.
- **plausible** — outside it the value is rare but real (a record storm, a
  desert afternoon, a high mountain). By default it is **kept** and reported.

The defaults are deliberately broad. Their job is to catch broken data, not to
judge a region's climate. They live in
[`src/agwise_data/qc_ranges.yaml`](../src/agwise_data/qc_ranges.yaml):

| Variable | Units | Physical | Plausible |
| --- | --- | --- | --- |
| `PRCP` | mm day⁻¹ | 0 – 2000 | 0 – 500 |
| `TMAX` | °C | −90 – 60 | −40 – 50 |
| `TMIN` | °C | −90 – 60 | −50 – 40 |
| `TEMP` | °C | −90 – 60 | −45 – 45 |
| `SRAD` | MJ m⁻² day⁻¹ | 0 – 50 *(and ≤ Ra, §4)* | 0 – 35 |
| `RHUM` | % | 0 – 100 | 1 – 100 |
| `WIND` | m s⁻¹ | 0 – 75 | 0 – 20 |
| `ELEV` | m | −500 – 9000 | −450 – 6500 |
| `SLOPE` | degree | 0 – 90 | 0 – 70 |
| `ASPECT` | degree | 0 – 360 | 0 – 360 |
| `CLAY`, `SAND`, `SILT` | % | 0 – 100 | 0 – 100 |
| `PH` | pH | 0 – 14 | 3 – 10 |
| `SOC` | g kg⁻¹ | 0 – 1000 | 0 – 200 |
| `NITROGEN` | g kg⁻¹ | 0 – 1000 | 0 – 30 |
| `CEC` | cmol(c) kg⁻¹ | 0 – 500 | 0 – 150 |
| `BDOD` | kg dm⁻³ (= g cm⁻³) | 0 – 2.65 | 0.1 – 2.0 |
| `CFVO` | vol % | 0 – 100 | 0 – 90 |
| `EXTP` | mg kg⁻¹ | 0 – 10000 | 0 – 500 |
| `WV0010`, `WV0033` | vol % | 0 – 100 | 0 – 80 |
| `WV1500` | vol % | 0 – 100 | 0 – 70 |
| `CROPLAND` | 1 | 0 – 1 | 0 – 1 |

`TPI`, `TRI`, `NDVI` and `EVI` have no default ranges and are not checked
(MODIS already applies its own valid range). You can still check them by giving
a range yourself (§2.2).

Why these numbers: the physical weather limits sit just outside the world
records (rainfall 1825 mm in one day; air temperature +56.7 °C / −89.2 °C), soil
bulk density cannot exceed mineral particle density (2.65 g cm⁻³), and pH is
bounded by 0–14. See [§7 Sources](#7-sources-and-credits).

### 2.1 Choose how strict to be — `qc=`

| `qc=` | Physical outliers | Plausible outliers |
| --- | --- | --- |
| `"warn"` **(default)** | → `NaN` | kept, `QCWarning` |
| `"strict"` | → `NaN` | → `NaN` |
| `"off"` | kept | kept (no checks at all) |

### 2.2 Change a range — `qc_ranges=`

Give only what you want to change. Any name form works (`PRCP`, `AGRO.PRCP`,
`Precipitation`), and `None` (R: `NA`, JSON: `null`) leaves a side open:

```python
from agwise_data import get_climate
get_climate("PRCP", years=2020, country="Kenya", freq="daily",
            qc_ranges={"PRCP": {"plausible": [0, 300]}})       # warn above 300 mm
```
```r
ad_get_climate("PRCP", 2020, country = "Kenya", freq = "daily",
               qc = "strict", qc_ranges = list(PRCP = list(plausible = c(0, 300))))
```
```bash
agwise-data get --vars PRCP --years 2020:2020 --country Kenya --freq daily \
    --qc strict --qc-ranges '{"PRCP": {"plausible": [0, 300]}}'
# or keep the ranges in a file (JSON or YAML) and pass its path:
agwise-data get ... --qc-ranges my_ranges.yaml
```

A malformed range (an unknown variable, low > high, a typo in `plausible`) is
rejected **before** anything is downloaded.

### 2.3 Where the range checks run

| Function | Checked | Report |
| --- | --- | --- |
| `get_climate`, `get_static` (+ `get_dem`, `get_soil`) | every value of the product (climate: the **daily** values, before any monthly aggregation) | `<product>.qc.json`, returned as `res[var]["qc"]` |
| `get_seasonal` | every ensemble member, **before** `ensemble="mean"/"median"` | `<product>.qc.json` |
| `get_season` | region: inherits the `get_climate` product checks · points: the extracted values | region: product · points: `df.attrs["qc"]` |
| `extract_points`, `extract_growing_season` | the extracted daily point values, then aggregated | `df.attrs["qc"]` (CLI: `<out>.qc.json`) |
| `extract_static_points` | the point values; a masked point is filled from the nearest valid pixel, like NoData | `df.attrs["qc"]` (CLI: `<out>.qc.json`) |

All of them take the same `qc=` / `qc_ranges=` arguments in Python, R
(`qc =`, `qc_ranges =`) and the CLI (`--qc`, `--qc-ranges`).

**Cached products and QC.** The shared cache is never altered: QC runs when a
product is built. Products made with the default QC keep their usual names.
`qc="off"` adds `_qcoff` and custom ranges add `_qc<hash>`, so your custom
product never overwrites the one everybody else uses. Products built before
v0.34 are rebuilt once from the cache (nothing is downloaded again).

---

## 3. Missing data is never silently zero

- **Rainfall is never filled.** A missing rainfall day stays missing everywhere.
- **Monthly rainfall totals** (`freq="monthly"`) are `NaN` for a month that has
  any missing day. Counting the gap as 0 mm would understate the total.
  Monthly *means* (temperature, radiation …) simply skip missing days.
- **Season totals** in `extract_growing_season` (`totalRF`, `nrRainyDays`) are
  `NaN` when any day of that trial's season is missing.
- **Crop-model files** write a missing value as the model's own code (`-99` for
  DSSAT/ORYZA). A missing date is a missing row, never a jump in the dates.

---

## 4. Solar radiation can't exceed the top of the atmosphere

Radiation at the surface can never exceed the radiation arriving at the top of
the atmosphere, **Ra**. Ra depends only on latitude and day of year (FAO-56,
eq. 21). Any `SRAD > Ra` is set to missing, in gridded products as well as in
crop-model files. The function is public:

```python
from agwise_data.qc import extraterrestrial_radiation
extraterrestrial_radiation(-20.0, 246)   # 32.2 MJ m-2 day-1 (FAO-56 example 8)
```

On real data this check should find nothing: AgERA5 over Kenya, 2019–2020
(6 million values) had **0** values above Ra.

---

## 5. Crop-model files (`to_dssat`, `to_apsim`, `to_wofost`, `to_oryza`)

### 5.1 Before writing: the weather of each point

`agwise_data.qc.check_weather` runs on every point's daily series:

1. **Continuous dates.** Every calendar day between the first and last date
   gets a row. Leading and trailing empty days are trimmed.
2. **TMIN ≤ TMAX.** A crossed day is swapped (as the legacy `readGeo_CM`
   scripts did) and counted.
3. **SRAD ≤ Ra** (§4).
4. **Short gaps filled.** Runs of up to **5** missing days in TMAX, TMIN or
   SRAD that have data on both sides are linearly interpolated, and each filled
   date is listed. Longer gaps stay missing. **Rainfall is never filled.**
   Change the limit with `gapfill_days=` on the single-file writers (`0`
   disables).

WOFOST needs a gapless series, so after these steps it keeps only complete days
(as the legacy `complete.cases` did). The report counts the dropped days.

### 5.2 Before writing: the soil profile

Clay + silt + sand are rescaled to sum to 100 % in every layer. A layer more
than 3 % off is counted; one more than 5 % off is flagged as a *large
deviation*. **No layer is dropped.** SoilGrids predicts each fraction
separately. In its 0–5 cm layer, 28 % (Rwanda), 39 % (Kenya) and 40 % (Ethiopia)
of pixels are more than 5 % off (deeper layers are within 1 %). Dropping those
layers would blank the topsoil of a third of all profiles. See the v0.36.1 note
in the [CHANGELOG](../CHANGELOG.md).

### 5.3 After writing: read back and validate

Every file is re-parsed exactly as written and checked:

| File | Checks |
| --- | --- |
| DSSAT `.WTH`, APSIM `.met`, WOFOST weather CSV, ORYZA CABO weather | continuous dates (no gaps or duplicates), no malformed or `nan` rows, missing values per column, physical ranges, TMIN ≤ TMAX; `.met`: numeric `tav`/`amp`; WOFOST/APSIM: no missing values (the models need a complete series) |
| DSSAT `.SOL` | increasing layer depths, SLLL < SDUL < SSAT, bulk density 0–2.65, pH 0–14, clay + silt ≤ 100 % |

The validators are public and work on any file, including ones you did not
write with this tool:

```python
from agwise_data.writers.validate import validate_wth, validate_sol
validate_wth("DSSAT/EXTE0001/WHTE0001.WTH")   # {"ok": True, "problems": [], "n_days": ..., ...}
```

### 5.4 The run report — `<out_dir>/qc_report.json`

Every `to_*` run writes one report next to the per-point folders and returns the
same record per point as `"qc"`. If any file fails validation you get one
`QCWarning` that points to it. An excerpt from a real run (3 points in Rwanda,
season March–July 2020; the validation records are shortened):

```json
{
  "summary": {
    "n_points": 3, "files_checked": 6, "files_with_problems": 0,
    "days_gapfilled": 0, "tmin_gt_tmax_swapped": 0, "texture_layers_over_5pct": 1
  },
  "points": [
    {
      "point": 2, "dir": "DSSAT/EXTE0003",
      "weather": {"n_days": 153, "dates_inserted": 0, "tmin_gt_tmax_swapped": 0,
                  "srad_above_extraterrestrial": 0, "gapfilled": {},
                  "gapfilled_dates": {}, "gapfill_max_days": 5,
                  "missing_after": {"TMAX": 0, "TMIN": 0, "SRAD": 0, "RAIN": 0}},
      "texture": {"renormalized": 4, "deviation_over_3pct": 1,
                  "large_deviation_over_5pct": 1, "max_deviation_pct": 6.6},
      "validation": {"wth": {"ok": true, "problems": []},
                     "sol": {"ok": true, "problems": []}}
    }
  ]
}
```

The CLI prints `qc_report` (its path) and a `qc_ok` flag per point.

---

## 6. Reading the reports, and common questions

**Product report (`<product>.qc.json`)** — counts below/above each range, how
many values were missing in the input, min/max after QC, the ranges and mode
used, and the warnings in plain text.

**Point extractions** — `df.attrs["qc"]` is a dict `{variable: report}` with the
same fields. (`attrs` is not saved by `to_csv`. The CLI writes it to
`<out>.qc.json` for you.)

**"I got a `QCWarning` — is my data broken?"** Read the message. *Physical*
means values were removed: check the report, and consider another source or
year. *Plausible* means nothing was changed; it tells you about real extremes.

**"A real extreme was flagged as plausible-outlier."** That's expected and
harmless with `qc="warn"`. Widen the range with `qc_ranges` if the warning is
noise for your region.

**"I want the raw values."** `qc="off"`. You get a separate `_qcoff` product,
so the shared one is untouched.

**"Silence the warnings in a script."**

```python
import warnings
from agwise_data.qc import QCWarning
warnings.filterwarnings("ignore", category=QCWarning)
```

---

## 7. Sources and credits

**prismpy.** The QC design follows patterns from
[prismpy](https://github.com/izuku-franck1555/prismpy), reviewed (read-only) at
commit [`cfe0219`](https://github.com/izuku-franck1555/prismpy/tree/cfe0219a8f8ef59d43c5af8cabea01d4097643d3).
No prismpy code is imported or copied; the ideas were re-implemented and adapted:

| Idea in agwise-data | prismpy source | Adapted how |
| --- | --- | --- |
| Two-level physical / plausible ranges (`qc.py`, `qc_ranges.yaml`) | [`validators/scientific.py` L119](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/validators/scientific.py#L119) | declared per canonical variable in YAML; user overrides per call |
| Nodata gate before scaling (`harmonize.integer_sentinel`) | [`pipeline/executor.py` L1879-1900](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/pipeline/executor.py#L1879-L1900) | dtype sentinel when a raster declares no nodata |
| Missing rain is never zero | [`tests/structural/test_no_silent_zero_rain.py`](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/tests/structural/test_no_silent_zero_rain.py) | monthly sums and season totals are `NaN` with a missing day, with tests |
| Short climate gap-fill, never rain | [`sources/climate/_gapfill.py`](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/sources/climate/_gapfill.py) | ≤ 5 days, TMAX/TMIN/SRAD, every filled date reported |
| Texture renormalization | [`harmonize/texture_renormalize.py`](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/harmonize/texture_renormalize.py) | **changed:** always rescale and flag; prismpy's ">5 % → exclude" removed (§5.2) |
| Post-write validation (`writers/validate.py`) | [`translators/_shared/dssat_sol_validator.py` L125](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/translators/_shared/dssat_sol_validator.py#L125) | extended to `.WTH`, `.met`, WOFOST and ORYZA weather |
| Retry with backoff for every download (v0.33, `retry.py`) | [`sources/common/retry.py` L26](https://github.com/izuku-franck1555/prismpy/blob/cfe0219a8f8ef59d43c5af8cabea01d4097643d3/src/prismpy/sources/common/retry.py#L26) | one `retry_call` helper used by all drivers |

The full comparison and roadmap (in Spanish) is in
[prismpy_comparison.md](prismpy_comparison.md).

**Science and data references.**

- Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998). *Crop
  evapotranspiration — Guidelines for computing crop water requirements.* FAO
  Irrigation and Drainage Paper 56. Eq. 21 (extraterrestrial radiation Ra,
  checked against example 8) and eq. 47 (wind at 10 m → 2 m, used by the
  WOFOST/ORYZA writers since v0.33).
  <https://www.fao.org/4/x0490e/x0490e00.htm>
- WMO Weather and Climate Extremes Archive — world records behind the physical
  weather limits (greatest 24-h rainfall 1825 mm, Foc-Foc, La Réunion, 1966;
  highest temperature 56.7 °C, Death Valley, 1913; lowest −89.2 °C, Vostok,
  1983). <https://wmo.asu.edu/>
- Poggio, L. et al. (2021). SoilGrids 2.0: producing soil information for the
  globe with quantified spatial uncertainty. *SOIL* 7, 217–240.
  <https://doi.org/10.5194/soil-7-217-2021> — texture fractions are predicted
  separately, hence the sums that are not 100 % (§5.2).
- Saxton, K.E., Rawls, W.J. (2006). Soil water characteristic estimates by
  texture and organic matter for hydrologic solutions. *SSSA J.* 70,
  1569–1578 — the hydraulics (SLLL/SDUL/SSAT) checked in `.SOL` validation.
