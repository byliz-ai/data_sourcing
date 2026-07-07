"""
=============================================================================
SCRIPT 1 — Download_Stack_Smooth()
=============================================================================
Builds the multi-index Sentinel-1/2 smoothed stack used by every downstream
script. 

Downloads composites for every requested S2 index and S1 band, resamples
S1 (20m) onto the S2 (10m) grid, and SG-smooths each index's pixel-wise time
series (vectorized — no per-pixel Python loop).

Time axis: DAYS SINCE SEASON START (cross-year safe — Rwanda season B,
Sep→Feb, works). Bands are labelled with the real calendar date
("EVI_20200914_SG"), and the meta CSV carries both "date" and "doy".


Checkpoints :
  Checkpoint A — raw time series plot
  Checkpoint B — raw vs SG-smoothed, interactively re-tunable
=============================================================================
"""

import sys
from pathlib import Path
from datetime import timedelta, datetime
import numpy as np
import pandas as pd
import rasterio
from rasterio.mask import mask as rio_mask
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent))
from agwise_phenology_utils import (
    get_gadm_boundary, admin_unit_col, get_boundary,
    get_cropland_mask_gee, get_croptype_mask_gee,
    path_stack_dir, path_stack_file, path_stack_meta,
    season_bounds, date_list,
    get_s2_composite, get_s1_composite, download_composite, load_band_resampled,
    vectorized_regrid_and_smooth,
    S2_INDICES_DEFAULT, S1_BANDS_DEFAULT,
)


def Download_Stack_Smooth(country, useCaseName, crop, level, admin_unit_name,
                          Planting_year, Harvesting_year, Planting_month,
                          Harvesting_month, CropMask=True, CropType=False,
                          crop_type_source=None, crop_type_band=None,
                          crop_type_value=None, overwrite=False,
                          # GEE/processing-specific additions:
                          S2_indices=None, S1_bands=None, gee_project=None,
                          s2_scale=10, s1_scale=20,
                          composite_days_s2=10, composite_days_s1=12,
                          s1_orbit="DESCENDING", sg_window=7, sg_poly=3,
                          base_dir=".", show_plots=True,
                          aoi_path=None, district_col=None,
                          max_download_workers=4):
    """
    Download Sentinel-1/2 composites, stack all requested indices/bands,
    and SG-smooth them. 

    S2_indices, S1_bands : list[str] or None
        Which indices/bands to build. Defaults to all 6 S2 indices
        (EVI, NDVI, NDRE, NDMI, LSWI, GCVI) and all 4 S1 bands
        (VV, VH, VHVV, RVI).

    """
    S2_indices = S2_indices or S2_INDICES_DEFAULT
    S1_bands   = S1_bands   or S1_BANDS_DEFAULT

    print(f"\n{'='*70}")
    print(f"Download_Stack_Smooth — {country} | {useCaseName} | {crop}")
    print(f"  Season: {Planting_month} {Planting_year} → "
          f"{Harvesting_month} {Harvesting_year}")
    print(f"  S2 indices: {S2_indices}  |  S1 bands: {S1_bands}")
    print(f"{'='*70}")

    stack_dir  = path_stack_dir(base_dir, country, useCaseName)
    stack_file = path_stack_file(base_dir, country, useCaseName, Planting_year, Harvesting_year)
    meta_file  = path_stack_meta(base_dir, country, useCaseName, Planting_year, Harvesting_year)
    stack_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[1] AOI boundary ...")
    if aoi_path is not None:
        print(f"  Using LOCAL file (GADM skipped): {aoi_path}")
    aoi_gdf, district_col = get_boundary(country, level, admin_unit_name,
                                          aoi_path, district_col)
    print(f"  {len(aoi_gdf)} feature(s) | unit column: {district_col}")

    if stack_file.exists() and meta_file.exists() and not overwrite:
        # Only trust the cache if it was built with the SAME indices/bands.
        # The filename encodes country/useCase/years but NOT the index set,
        # so without this check, changing S2_indices/S1_bands and re-running
        # with overwrite=False would silently return the old stack.
        # (Note: this validates the index/band set; a change to season
        #  months, scale or S1 orbit is not yet detected here — rebuild with
        #  overwrite=True if you change those.)
        requested = set(S2_indices) | set(S1_bands)
        try:
            cached = set(pd.read_csv(meta_file)["index"].astype(str).unique())
        except Exception:
            cached = None
        if cached == requested:
            print(f"\n[Cache] Found matching stack — loading (overwrite=False).")
            print(f"  {stack_file}")
            return {"stack_path": stack_file, "meta_path": meta_file, "aoi_gdf": aoi_gdf,
                    "district_col": district_col, "failed_downloads": []}
        print(f"\n[Cache] Existing stack indices {cached} != requested "
              f"{requested} — rebuilding.")

    import ee, geemap
    print("\n[2] Initialising GEE ...")
    try:
        ee.Initialize(project=gee_project)
    except Exception as init_err:
        # ee.Authenticate() opens an interactive browser/console flow — it
        # hangs a headless or cron run forever. Only attempt it on a real
        # terminal; otherwise fail with a clear instruction.
        if not sys.stdin.isatty():
            raise RuntimeError(
                "GEE is not authenticated and this is a non-interactive run "
                "(no TTY). Run `earthengine authenticate` once in a terminal "
                "before launching headless/cron jobs."
            ) from init_err
        ee.Authenticate(force=True)
        ee.Initialize(project=gee_project)
    AOI = geemap.geopandas_to_ee(aoi_gdf).geometry()

    crop_mask_img = None
    if CropMask:
        print("  Applying CropMask (ESA WorldCover cropland) ...")
        crop_mask_img = get_cropland_mask_gee(AOI)
        if CropType:
            ct = get_croptype_mask_gee(AOI, crop_type_source, crop_type_band, crop_type_value)
            if ct is not None:
                crop_mask_img = crop_mask_img.And(ct)

    start_dt, end_dt = season_bounds(Planting_year, Harvesting_year,
                                      Planting_month, Harvesting_month)
    if start_dt >= end_dt:
        raise ValueError(
            f"Invalid season window: start ({start_dt.date()}) is not before "
            f"end ({end_dt.date()}).\n"
            f"  Planting_year={Planting_year}  Planting_month={Planting_month}\n"
            f"  Harvesting_year={Harvesting_year}  Harvesting_month={Harvesting_month}\n"
            f"  → If the season stays within one calendar year (e.g. June→November), "
            f"Harvesting_year should EQUAL Planting_year.\n"
            f"  → If the season crosses a year boundary (e.g. September→March), "
            f"Harvesting_year should be Planting_year + 1."
        )
    s2_dates = date_list(start_dt, end_dt, composite_days_s2)
    s1_dates = date_list(start_dt, end_dt, composite_days_s1)
    print(f"\n[3] Building composites: S2={len(s2_dates)}  S1={len(s1_dates)}")

    tmp_dir = stack_dir / "_tmp_download"
    tmp_dir.mkdir(exist_ok=True)

    # ....Pre-compute the EXACT final band layout from date math alone ─
    # (no GEE calls needed — this only depends on the season window/step,
    #  not on which individual downloads later succeed or fail, so we can
    #  open the destination file ONCE with the correct band count and
    #  stream each index's bands into it as soon as they're ready.)
    # The time axis is DAYS SINCE SEASON START (not day-of-year): DOY wraps
    # 365→1 when a season crosses the New Year (e.g. Rwanda season B,
    # Sep→Feb) and would corrupt the regridding. Offsets are monotonic for
    # any season; bands are labelled with the REAL DATE they represent.
    def _regular_grid(dates, step=8):
        if not dates: return np.array([])
        offs = np.array([(d - start_dt).days for d in dates], dtype=float)
        return np.arange(offs.min(), offs.max() + 1, step)

    d_reg_s2 = _regular_grid(s2_dates)
    d_reg_s1 = _regular_grid(s1_dates)
    total_bands = len(S2_indices) * len(d_reg_s2) + len(S1_bands) * len(d_reg_s1)
    print(f"  Planned output bands: {int(total_bands)}  "
          f"({len(S2_indices)} S2 indices × {len(d_reg_s2)} + "
          f"{len(S1_bands)} S1 bands × {len(d_reg_s1)})")

    # ...Bootstrap: one download just to establish the reference grid ─
    ref_transform = ref_crs = ref_h = ref_w = None
    failed_downloads = []

    def process_one(idx, dt, get_fn, scale, sensor_tag):
        nonlocal ref_transform, ref_crs, ref_h, ref_w
        s = dt.strftime("%Y-%m-%d")
        e = (dt + timedelta(days=composite_days_s2 if sensor_tag=="S2" else composite_days_s1)).strftime("%Y-%m-%d")
        img = get_fn(AOI, idx, s, e) if sensor_tag=="S2" else get_fn(AOI, idx, s, e, s1_orbit)
        if crop_mask_img is not None:
            img = img.updateMask(crop_mask_img)
        fpath = tmp_dir / f"{idx}_{dt.strftime('%Y%m%d')}.tif"
        try:
            download_composite(img, fpath, AOI, scale, overwrite=overwrite)
        except Exception as ex:
            print(f"\n    ✗ FAILED after retries: {fpath.name} — {ex}")
            failed_downloads.append(f"{idx}_{dt.strftime('%Y-%m-%d')}")
            return None
        arr, t, c, h, w = load_band_resampled(fpath, ref_transform, ref_crs, ref_h, ref_w)
        if ref_transform is None:
            ref_transform, ref_crs, ref_h, ref_w = t, c, h, w
        return arr

    print("\n[3b] Establishing reference grid (one bootstrap download) ...")
    if S2_indices and s2_dates:
        process_one(S2_indices[0], s2_dates[0], get_s2_composite, s2_scale, "S2")
    elif S1_bands and s1_dates:
        process_one(S1_bands[0], s1_dates[0], get_s1_composite, s1_scale, "S1")
    if ref_h is None:
        raise RuntimeError("Could not establish a reference grid — the very "
                           "first composite download failed. Check AOI/network.")
    H, W = ref_h, ref_w
    print(f"  Reference grid: {W}×{H} px  "
          f"(~{W*H*4/1e9:.2f} GB per single-band float32 array)")
    if W * H * 4 > 1e9:
        print(f"  ⚠ This AOI is large. Each band uses "
              f"{W*H*4/1e9:.1f} GB of RAM. If you hit memory errors, increase "
              f"s2_scale/s1_scale (e.g. 20 or 30m) to shrink the pixel grid.")

    ref_tif_path = next(tmp_dir.glob("*.tif"))
    with rasterio.open(ref_tif_path) as ref:
        out_profile = ref.profile.copy()
    out_profile.update(count=int(total_bands), dtype="float32", nodata=np.nan,
                       height=H, width=W, transform=ref_transform, crs=ref_crs,
                       BIGTIFF="YES", compress="LZW", predictor=3, tiled=True)

    #....Process ONE index/band at a time: download → smooth → write → free 
    print(f"\n[4] Streaming each index to disk (peak memory ≈ one index at a time) ...")
    meta_rows, band_names_all = [], []
    band_position = 1   # 1-indexed band slot in the destination file
    checkpoint_data = {}   # lightweight per-index summaries for plotting only

    dst = rasterio.open(stack_file, "w", **out_profile)
    try:
        def process_group(names, dates, get_fn, scale, sensor_tag, d_reg_planned):
            nonlocal band_position
            # days since season start: strictly ascending even across New Year
            offs_full = np.array([(d - start_dt).days for d in dates], dtype=float)
            for name in names:
                print(f"\n  --- {sensor_tag} {name} ---")
                n_dates = len(dates)
                raw_stack = None
                # Composite downloads are the wall-clock bottleneck and are
                # independent once the reference grid exists (the bootstrap
                # download above set it). process_one only READS ref_* here,
                # and download_composite already backs off on GEE 429/5xx, so
                # fetching several at once just overlaps network waits.
                from concurrent.futures import ThreadPoolExecutor, as_completed
                results_by_i = {}
                with ThreadPoolExecutor(max_workers=max_download_workers) as pool:
                    fut2i = {pool.submit(process_one, name, dt, get_fn, scale, sensor_tag): i
                             for i, dt in enumerate(dates)}
                    done = 0
                    for fut in as_completed(fut2i):
                        results_by_i[fut2i[fut]] = fut.result()
                        done += 1
                        print(f"  {sensor_tag} {name}: {done}/{n_dates} composites", end="\r")
                print()
                for i in range(n_dates):
                    arr = results_by_i.get(i)
                    if arr is not None:
                        if raw_stack is None:
                            raw_stack = np.full((n_dates,) + arr.shape, np.nan, dtype=np.float32)
                        raw_stack[i] = arr

                if raw_stack is None:
                    print(f"  ⚠ No successful composites for {name} at all — "
                         f"writing NaN placeholder bands so file structure stays consistent.")
                    raw_mean = np.full(len(offs_full), np.nan)
                    sm_stack = np.full((len(d_reg_planned), H, W), np.nan, dtype=np.float32)
                    d_reg_actual = d_reg_planned
                    sm_mean = np.full(len(d_reg_actual), np.nan)
                    examples = []
                else:
                    raw_mean = np.nanmean(raw_stack, axis=(1,2))   # tiny — keep for checkpoint plot
                    sm_stack, d_reg_actual = vectorized_regrid_and_smooth(
                        raw_stack, offs_full, sg_window, sg_poly)
                    sm_mean = np.nanmean(sm_stack, axis=(1,2))     # tiny

                    # Sample a few individual pixels (NOT the AOI mean) for the
                    # PDF diagnostic plot — this is what shows realistic noise
                    # vs the SG curve, matching the reference figure style.
                    # Each sampled series is just a (T,) vector — negligible
                    # memory regardless of AOI size.
                    n_examples = 3
                    valid_frac = np.mean(~np.isnan(raw_stack), axis=0)   # (H,W)
                    ys, xs = np.where(valid_frac > 0.5)
                    examples = []
                    if len(ys) > 0:
                        pick = np.linspace(0, len(ys)-1, min(n_examples, len(ys))).astype(int)
                        for p in pick:
                            ry, rx = ys[p], xs[p]
                            examples.append({
                                "raw_dates": list(dates),
                                "raw_vals": raw_stack[:, ry, rx].copy(),
                                "sm_dates": [start_dt + timedelta(days=float(o))
                                             for o in d_reg_actual],
                                "sm_vals":  sm_stack[:, ry, rx].copy(),
                            })

                    del raw_stack
                    import gc; gc.collect()

                checkpoint_data[name] = {"dates": list(dates), "raw_mean": raw_mean,
                                         "sm_dates": [start_dt + timedelta(days=float(o))
                                                      for o in d_reg_actual],
                                         "sm_mean": sm_mean,
                                         "examples": examples}

                for k, off in enumerate(d_reg_actual):
                    # bands carry the REAL calendar date (cross-year safe),
                    # not day-of-year: e.g. "EVI_20200914_SG"
                    band_date = start_dt + timedelta(days=float(off))
                    bname = f"{name}_{band_date.strftime('%Y%m%d')}_SG"
                    dst.write(sm_stack[k].astype(np.float32), band_position)
                    band_names_all.append(bname)
                    meta_rows.append({
                        "index": name,
                        "date": band_date.strftime("%Y-%m-%d"),
                        "doy": int(band_date.timetuple().tm_yday),
                        "band_name": bname,
                    })
                    band_position += 1

                del sm_stack
                import gc; gc.collect()
                print(f"  {name}: wrote {len(d_reg_actual)} bands "
                     f"(running total: {band_position-1}/{int(total_bands)})")

        process_group(S2_indices, s2_dates, get_s2_composite, s2_scale, "S2", d_reg_s2)
        process_group(S1_bands,   s1_dates, get_s1_composite, s1_scale, "S1", d_reg_s1)

        dst.descriptions = tuple(band_names_all)
    finally:
        dst.close()

    pd.DataFrame(meta_rows).to_csv(meta_file, index=False)
    print(f"\n  Saved: {stack_file}")
    print(f"  Saved: {meta_file}")

    if failed_downloads:
        print(f"\n  ⚠ {len(failed_downloads)} composite(s) failed even after retries:")
        for f in failed_downloads:
            print(f"      {f}")
        print(f"  These dates are simply missing from the smoothed curve for their "
             f"index (SG handles gaps fine). Re-run with overwrite=False to retry "
             f"just these — successful ones are cached and skipped.")

    # ── Checkpoints (lightweight — AOI-mean curves only, not full rasters) ──
    if show_plots:
        plot_dir = stack_dir / "checkpoints"
        plot_dir.mkdir(exist_ok=True)
        _checkpoint_plots(checkpoint_data, plot_dir,
                          country, useCaseName, Planting_year, sg_window, sg_poly)

    return {"stack_path": stack_file, "meta_path": meta_file, "aoi_gdf": aoi_gdf,
            "district_col": district_col, "failed_downloads": failed_downloads}


def _checkpoint_plots(checkpoint_data, plot_dir, country, useCaseName, year,
                      sg_window, sg_poly):
    # Examples carry REAL datetimes ("raw_dates"/"sm_dates"), so the x-axis
    # is correct for seasons that cross the calendar year; `year` is only
    # used for the output filename.
    saved_paths = []
    for name, data in checkpoint_data.items():
        examples = data.get("examples", [])
        if not examples:
            print(f"  ⚠ No example pixels available for {name} (all composites "
                 f"failed) — skipping its PDF.")
            continue

        n = len(examples)
        fig, axes = plt.subplots(n, 1, figsize=(9, 2.6*n), sharex=True)
        if n == 1: axes = [axes]

        for i, (ax, ex) in enumerate(zip(axes, examples), start=1):
            raw_dates = ex["raw_dates"]
            sm_dates  = ex["sm_dates"]

            ax.plot(raw_dates, ex["raw_vals"], color="black", lw=1.1,
                   marker="o", markersize=3, label="Raw")
            ax.plot(sm_dates, ex["sm_vals"], color="red", lw=1.6,
                   linestyle="--", label="Smoothed")

            ax.set_ylabel(name, fontsize=10)
            ax.grid(alpha=0.2)
            ax.text(1.02, 0.5, f"Example {i}", transform=ax.transAxes,
                   rotation=270, va="center", fontsize=9)
            if i == 1:
                ax.legend(title="Data", loc="upper right", fontsize=8,
                         title_fontsize=9, frameon=False)

        axes[-1].set_xlabel("Date", fontsize=10)
        fig.suptitle(f"Smoothing noisy {name} time series with the\n"
                    f"Savitzky-Golay filter (window={sg_window}, poly={sg_poly})",
                    fontsize=13, x=0.02, ha="left", y=1.02)
        fig.text(0.02, 0.965, f"{country} {useCaseName}", fontsize=10, ha="left")
        plt.tight_layout(rect=[0, 0, 1, 0.93])

        p = plot_dir / f"checkpoint_{name}_{year}.pdf"
        plt.savefig(p, format="pdf", bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(p)
        print(f"  Checkpoint PDF: {p}")

    if saved_paths and sys.stdin.isatty():
        ans = input(f"\n  → {len(saved_paths)} checkpoint PDF(s) saved to "
                   f"{plot_dir} — open and review, then press Enter to "
                   f"continue (q=quit): ").strip().lower()
        if ans == "q":
            print("Exiting — adjust sg_window/sg_poly and re-run.")
            sys.exit(0)
    elif saved_paths:
        # Non-interactive run (headless/cron): never block on input().
        print(f"\n  → {len(saved_paths)} checkpoint PDF(s) saved to {plot_dir} "
             f"(non-interactive run — continuing without pausing for review).")


# =============================================================================
# EXAMPLE CALL
# =============================================================================

if __name__ == "__main__":
    result = Download_Stack_Smooth(
        country          = "Kenya",
        useCaseName      = "SAR_Pheno",
        crop             = "Maize",
        level            = 1,
        admin_unit_name  = "Western",
        Planting_year    = 2020,
        Harvesting_year  = 2020,
        Planting_month   = "March",
        Harvesting_month = "September",
        CropType         = True,
        crop_type_source = None,
        crop_type_band   = None,
        crop_type_value  = None,
        overwrite        = False,
        gee_project      = "<your-gee-project>",  # your own EE Cloud project id
        base_dir         = "./sar_pheno",         # any writable output folder
        show_plots       = True,
        S2_indices       = ["EVI", "NDRE"],
        S1_bands         = ["VV", "VH", "VHVV", "RVI"],
        s2_scale         = 20,
        s1_scale         = 20,
        # If GADM (geodata.ucdavis.edu) is unreachable from  network,
        # point this at a local shapefile/GeoJSON instead-GADM is then
        # skipped entirely:
        aoi_path         = r"D:\Agwise\SAR\sar_pheno_python\Western.shp",   # e.g. r"D:\Agwise\SAR\Kenya_region.geojson"
        district_col     = "NAME_1",   # e.g. "district"
        
    )
    print(result)
