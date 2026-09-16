"""Seasonal-forecast bias correction (scope-map #3).

Turns the raw SEAS5 forecast (:func:`agwise_data.get_seasonal`) into
bias-adjusted, analysis-ready fields by learning the model's systematic error
from the **hindcast vs observations** (:func:`agwise_data.get_climate`) over a
calibration period and applying **Quantile Delta Mapping** (QDM, Cannon et al.
2015, J. Climate) to the target forecast. This reproduces the *method* of the
planting-date module's ``03_bias_correction_forecast_multiVar.R`` (which calls
climate4R's ``biasCorrection(method="qdm", ...)``); it is not a byte-clone of
that library.

Per-variable transform (matches the reference's ``scaling.type``):
additive for temperatures (TMAX/TMIN/TEMP), multiplicative for PRCP and SRAD.

QDM preserves the model's *own* projected change at each quantile:

    tau      = F_forecast(x)                      # x's quantile in the forecast
    additive:        x_bc = F_obs^-1(tau) + (x - F_hind^-1(tau))
    multiplicative:  x_bc = F_obs^-1(tau) * (x / F_hind^-1(tau))

where F_obs/F_hind are the observed/hindcast empirical CDFs over the
calibration period (hindcast members pooled = the model climatology).

The whole-season case (``window_days=None``, the only mode used by the DSSAT
export path) runs through a vectorized, row-chunked implementation
(:func:`_bias_correct_cube_vectorized`); the windowed-calibration case keeps
the per-pixel loop.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
from scipy.stats import rankdata

from . import progress

# scaling.type per variable short-name (from 03_bias_correction_forecast_multiVar.R)
DEFAULT_KIND = {
    "PRCP": "multiplicative",
    "TMAX": "additive",
    "TMIN": "additive",
    "TEMP": "additive",
    "SRAD": "multiplicative",
}


def _cdf_positions(values, dist):
    """Plotting-position quantile (tau in (0,1)) of each ``values`` in ``dist``."""
    dist = np.sort(dist)
    n = dist.size
    # fraction of dist strictly below + half the ties, then Weibull-style (k/(n+1))
    left = np.searchsorted(dist, values, side="left")
    right = np.searchsorted(dist, values, side="right")
    rank = (left + right) / 2.0 + 0.5
    return np.clip(rank / (n + 1), 1e-6, 1 - 1e-6)


def quantile_delta_map(values, obs, hind, kind="additive"):
    """QDM-correct ``values`` given calibration ``obs`` and ``hind`` samples.

    All inputs are 1-D arrays; NaNs are ignored in the calibration samples and
    preserved in ``values``. Returns an array shaped like ``values``. With too
    few calibration samples the values are returned unchanged. Multiplicative
    output is clipped at 0 (no negative rainfall/radiation).
    """
    values = np.asarray(values, dtype="float64")
    obs = np.asarray(obs, dtype="float64")
    hind = np.asarray(hind, dtype="float64")
    obs = obs[np.isfinite(obs)]
    hind = hind[np.isfinite(hind)]
    out = values.copy()
    finite = np.isfinite(values)
    if obs.size < 2 or hind.size < 2 or not finite.any():
        return out
    v = values[finite]
    tau = _cdf_positions(v, v)  # x's quantile in the forecast distribution
    obs_q = np.quantile(obs, tau)
    hind_q = np.quantile(hind, tau)
    if kind == "additive":
        out[finite] = obs_q + (v - hind_q)
    elif kind == "multiplicative":
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(hind_q > 1e-9, v / hind_q, 1.0)
        corrected = obs_q * ratio
        bad = ~np.isfinite(corrected)
        corrected[bad] = obs_q[bad]
        out[finite] = np.maximum(corrected, 0.0)
    else:
        raise ValueError(f"kind must be 'additive' or 'multiplicative', got '{kind}'")
    return out


def _doy(times) -> np.ndarray:
    return xr.DataArray(times).dt.dayofyear.values


def _regrid_to(src, obs):
    """Downscale a coarse forecast/hindcast cube onto the fine ``obs`` grid.

    Linear interpolation smooths where the source has >=2 cells per axis, but
    yields all-NaN when the source is a single cell (a small AOI on the coarse
    1-degree SEAS5 grid is often just 1x1). Fill those NaNs with a nearest-cell
    downscaling so the corrected cube is never empty — otherwise every point
    samples NaN and is dropped as "no weather in season".

    Callers that pass a multi-member cube should loop over ``member``
    themselves before calling this (see ``_regrid_chunk_per_member`` below) --
    calling ``.interp()`` once on the whole (member, time, lat, lon) cube shows
    pathological super-linear scaling with member count (25 members over a
    ~80k-pixel grid: didn't finish in 90s; looped one member at a time: ~37s
    total, linear).
    """
    # Keep the regridded cube in float32 (interp promotes to float64): these
    # member x time x fine-grid cubes are the biggest allocation here, and the
    # per-pixel QDM upcasts its 1-D inputs to float64 anyway, so mapping
    # precision is unchanged while peak roughly halves.
    lin = src.interp(lat=obs["lat"], lon=obs["lon"], method="linear").astype("float32")
    # interp does not extrapolate: obs cells beyond the (few) source cell centres
    # stay NaN. Only then fall back to a nearest-cell regrid (which maps every
    # obs cell to its nearest source cell) — skip building that second full cube
    # entirely when linear already covered everything (the common multi-cell case).
    if bool(np.isnan(lin).any()):
        near = src.reindex(
            lat=obs["lat"], lon=obs["lon"], method="nearest"
        ).astype("float32")
        lin = lin.fillna(near)
    return lin


def _regrid_chunk_per_member(coarse_da, obs_chunk):
    """Regrid every member of ``coarse_da`` onto ``obs_chunk``'s grid.

    Looping members (instead of regridding the whole member x time cube in
    one ``.interp()`` call) avoids the pathological slowdown described in
    ``_regrid_to``. Regridding only a row-chunk at a time (instead of the
    full grid for all members, before chunking) additionally bounds peak
    memory: regridding the full grid for all members up front OOM-killed at
    Mozambique's ~80k-pixel grid (~28 GB -- all 25 hindcast members' full-
    grid regridded arrays held at once before concatenation). Chunking the
    regrid itself keeps peak memory to one chunk x all members (~9 GB at
    Mozambique's scale), at a ~35% regrid-time cost from the extra per-call
    overhead of many smaller calls -- an accepted, necessary tradeoff for
    memory safety at arbitrary country size.
    """
    n_members = coarse_da.sizes.get("member", 1)
    pieces = []
    for m in range(n_members):
        sel = coarse_da.isel(member=[m]) if "member" in coarse_da.dims else coarse_da
        pieces.append(_regrid_to(sel, obs_chunk))
    return xr.concat(pieces, dim="member") if "member" in coarse_da.dims else pieces[0]


def _batched_quantile_lookup(sorted_dist, tau):
    """Linear-interpolated quantile lookup, vectorized over the leading axis.

    Equivalent to calling ``np.quantile(sorted_dist[:, i, j], tau[:, i, j])``
    independently at every pixel (i, j), but batched across the whole chunk.
    float64 throughout to match ``np.quantile``'s own internal precision:
    CHIRPS's long-tailed rainfall distribution is sensitive to this at
    extreme quantiles -- a float32 version of this same lookup produced up
    to 0.21 mm/day of spurious drift vs. the per-pixel ``np.quantile``
    reference at a handful of extreme-rainfall pixels; float64 matches it
    exactly (0.0 max diff).
    """
    n = sorted_dist.shape[0]
    idx = tau * (n - 1)
    idx_lo = np.floor(idx).astype(np.int64)
    idx_hi = np.ceil(idx).astype(np.int64)
    frac = (idx - idx_lo).astype("float64")
    lo = np.take_along_axis(sorted_dist, idx_lo, axis=0)
    hi = np.take_along_axis(sorted_dist, idx_hi, axis=0)
    return lo + frac * (hi - lo)


def _bias_correct_cube_vectorized(obs, hind, fcst, kind, chunk_rows=30):
    """Vectorized, row-chunked replacement for the per-pixel QDM loop.

    Only handles the whole-season case (no day-of-year windowing) -- the
    only case actually exercised by the DSSAT export path (``window_days``
    is always ``None`` there). Same QDM math as ``quantile_delta_map``/
    ``_cdf_positions`` above, just batched across all pixels in a latitude
    row-chunk at once instead of pixel-by-pixel. ``chunk_rows`` bounds peak
    memory (one chunk x all members x all time steps in memory at a time)
    and is purely a local compute/memory knob -- it has no relationship to
    how CDS data is requested/downloaded.

    Validated bit-for-bit identical (max abs diff 0.0 across ~64M points
    combined) against ``bias_correct_cube``'s own per-pixel loop, for both
    Rwanda (tiny grid) and Mozambique (~24x more pixels) against real
    production output, from genuinely fresh (cold-cache) CDS downloads.
    Roughly 50-100x faster end-to-end (see companion email for timings).
    """
    H, W = obs.sizes["lat"], obs.sizes["lon"]
    Mh = hind.sizes.get("member", 1)
    Mf = fcst.sizes.get("member", 1)
    Tf = fcst.sizes["time"]
    out = np.full((Mf, Tf, H, W), np.nan, dtype="float32")

    n_chunks = (H + chunk_rows - 1) // chunk_rows
    for row0 in progress.track(
        range(0, H, chunk_rows), total=n_chunks,
        desc=f"Bias-correcting ({H}x{W} px, vectorized)",
    ):
        row1 = min(row0 + chunk_rows, H)

        obs_chunk = obs.isel(lat=slice(row0, row1))
        hind_r = _regrid_chunk_per_member(hind, obs_chunk)
        fcst_r = _regrid_chunk_per_member(fcst, obs_chunk)

        obs_v = obs_chunk.transpose("time", "lat", "lon").values.astype("float64", copy=True)
        hind_v = hind_r.transpose("member", "time", "lat", "lon").values.astype("float64", copy=False)
        fcst_v = fcst_r.transpose("member", "time", "lat", "lon").values.astype("float64", copy=False)
        Th = hind_v.shape[1]
        Kh, Kf = Mh * Th, Mf * Tf
        hind_flat = hind_v.reshape(Kh, row1 - row0, W).copy()
        fcst_flat = fcst_v.reshape(Kf, row1 - row0, W).copy()
        del hind_v, fcst_v

        # tau = each forecast value's own quantile within the forecast
        # distribution at that pixel (matches _cdf_positions(v, v) above).
        tau_rank = rankdata(fcst_flat, method="average", axis=0)
        tau = np.clip(tau_rank / (Kf + 1), 1e-6, 1 - 1e-6)
        del tau_rank

        obs_v.sort(axis=0)
        hind_flat.sort(axis=0)

        obs_q = _batched_quantile_lookup(obs_v, tau)
        hind_q = _batched_quantile_lookup(hind_flat, tau)

        if kind == "multiplicative":
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(hind_q > 1e-9, fcst_flat / hind_q, 1.0)
            corrected = obs_q * ratio
            bad = ~np.isfinite(corrected)
            corrected = np.where(bad, obs_q, corrected)
            corrected = np.maximum(corrected, 0.0)
        elif kind == "additive":
            corrected = obs_q + (fcst_flat - hind_q)
        else:
            raise ValueError(f"kind must be 'additive' or 'multiplicative', got '{kind}'")

        valid_px = np.isfinite(obs_v).any(axis=0) & np.isfinite(fcst_flat).any(axis=0)
        corrected = np.where(valid_px[None, :, :], corrected, np.nan).astype("float32")

        out[:, :, row0:row1, :] = corrected.reshape(Mf, Tf, row1 - row0, W)
        del obs_v, hind_flat, fcst_flat, tau, obs_q, hind_q, corrected

    return xr.DataArray(
        out,
        dims=("member", "time", "lat", "lon"),
        coords={
            "member": fcst["member"].values if "member" in fcst.coords else np.arange(Mf),
            "time": fcst["time"].values,
            "lat": obs["lat"].values,
            "lon": obs["lon"].values,
        },
        name=fcst.name,
    )


def bias_correct_cube(obs, hind, fcst, kind="additive", window_days=None):
    """QDM-correct a forecast cube against hindcast+observation cubes.

    ``obs`` is ``(time, lat, lon)``; ``hind`` and ``fcst`` are
    ``(member, time, lat, lon)``. ``hind``/``fcst`` are first interpolated onto
    the ``obs`` grid (downscaling the coarse forecast, as the reference does).
    Hindcast members are pooled into the model climatology. ``window_days``
    (half-width) restricts calibration to samples within +/- that many
    days-of-year of each forecast step; ``None`` pools the whole season.
    Returns a corrected cube shaped like the regridded ``fcst``.

    When ``window_days is None`` (the only mode used by the DSSAT export
    path), this uses ``_bias_correct_cube_vectorized`` -- same math, ~50-100x
    faster, validated bit-for-bit identical to the loop below. The per-pixel
    loop is kept as-is for the windowed-calibration case, which isn't
    exercised in production and hasn't been validated against the fast path.
    """
    if window_days is None:
        return _bias_correct_cube_vectorized(obs, hind, fcst, kind)

    hind = _regrid_to(hind, obs)
    fcst = _regrid_to(fcst, obs)

    obs_doy = _doy(obs["time"].values)
    hind_doy = _doy(hind["time"].values)
    fcst_doy = _doy(fcst["time"].values)

    obs_v = obs.transpose("time", "lat", "lon").values
    hind_v = hind.transpose("member", "time", "lat", "lon").values
    fcst_v = fcst.transpose("member", "time", "lat", "lon").values
    M, T, H, W = fcst_v.shape
    out = np.full_like(fcst_v, np.nan)

    groups = []
    for i in range(T):
        d = fcst_doy[i]
        fsel = np.zeros(T, bool); fsel[i] = True
        osel = np.abs(((obs_doy - d + 182) % 365) - 182) <= window_days
        hsel = np.abs(((hind_doy - d + 182) % 365) - 182) <= window_days
        groups.append((fsel, osel, hsel))

    for y in progress.track(range(H), desc=f"Bias-correcting ({H}x{W} px)"):
        for x in range(W):
            o_px = obs_v[:, y, x]
            h_px = hind_v[:, :, y, x]
            f_px = fcst_v[:, :, y, x]
            if not np.isfinite(o_px).any() or not np.isfinite(f_px).any():
                continue
            for fsel, osel, hsel in groups:
                obs_s = o_px[osel]
                hind_s = h_px[:, hsel].ravel()
                out[:, fsel, y, x] = quantile_delta_map(
                    f_px[:, fsel], obs_s, hind_s, kind
                ).reshape(M, int(fsel.sum()))
    return fcst.copy(data=out)
