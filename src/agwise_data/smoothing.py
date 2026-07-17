"""Gap-filling and Savitzky-Golay smoothing of MODIS vegetation-index stacks.

The MODIS drivers deliver NDVI/EVI composites with cloud/QA-rejected pixels
left as NaN (see :mod:`agwise_data.drivers.modis`: "the downstream smoothing
interpolates the gaps"). This module turns that raw composite stack into the
gap-filled, smoothed time series the planting-date phenology workflow consumes,
reproducing the legacy ``get_MODISts_PreProc.R`` step:

1. each pixel's NaN gaps are filled with that pixel's temporal mean, and
2. a Savitzky-Golay filter (polynomial order 3, window 9 by default — the
   MODIS 8/16-day choice) is run along the time axis.

Pixels with no valid observation at all (e.g. masked-out non-cropland, or
permanent water) stay NaN. The numeric core (:func:`savgol_gapfill`) is kept
free of xarray so it is trivially testable; :func:`smooth_stack` wraps it for a
``(time, lat, lon)`` DataArray and :func:`apply_cropmask` handles the optional
cropland masking.
"""

from __future__ import annotations

import warnings

import numpy as np
import xarray as xr
from scipy.signal import savgol_filter


def _validate_savgol(window: int, polyorder: int, n_time: int) -> None:
    if window < 3 or window % 2 == 0:
        raise ValueError(f"window must be an odd integer >= 3, got {window}")
    if polyorder >= window:
        raise ValueError(
            f"polyorder ({polyorder}) must be smaller than window ({window})"
        )
    if n_time < window:
        raise ValueError(
            f"the time series has {n_time} step(s) but window is {window}; "
            "need at least `window` composites — widen the year range or "
            "reduce `window`"
        )


GAPFILL_METHODS = ("linear", "mean")


def _apply_savgol(filled: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """Savitzky-Golay along axis 0, preserving all-NaN pixels as NaN.

    ``filled`` must be gap-filled already except for pixels that were entirely
    NaN (no valid observation); those stay NaN in the output. Returns float32.
    """
    all_nan = np.isnan(filled).all(axis=0, keepdims=True)
    # savgol_filter cannot accept NaN; blank the all-NaN pixels to 0 for the
    # filter and restore them afterwards.
    safe = np.where(np.isnan(filled), 0.0, filled)
    smoothed = savgol_filter(
        safe, window_length=window, polyorder=polyorder, axis=0, mode="interp"
    )
    smoothed = np.where(np.broadcast_to(all_nan, smoothed.shape), np.nan, smoothed)
    return smoothed.astype("float32")


def _linear_gapfill(values: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Per-pixel linear interpolation of NaN gaps along axis 0.

    ``x`` is the 1-D time coordinate (so uneven Terra/Aqua spacing is honoured).
    Interior gaps are interpolated; leading/trailing gaps are carried from the
    nearest valid step (``numpy.interp`` clamps to the endpoints). Fully valid
    and all-NaN columns are passed through unchanged.
    """
    flat = values.reshape(values.shape[0], -1)
    out = flat.copy()
    for j in range(flat.shape[1]):
        col = flat[:, j]
        valid = ~np.isnan(col)
        nvalid = int(valid.sum())
        if nvalid == 0 or nvalid == col.shape[0]:
            continue  # nothing to fill, or nothing to fill from
        out[:, j] = np.interp(x, x[valid], col[valid])
    return out.reshape(values.shape)


def savgol_gapfill(values: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """Mean gap-fill then Savitzky-Golay along axis 0 (time).

    ``values`` is ``(time, ...)``. NaN gaps in each pixel's series are filled
    with that pixel's mean over the valid steps, the filter is applied, and
    pixels that were entirely NaN are restored to NaN. This is the numeric core
    of the legacy ``gapfill="mean"`` path; returns float32.
    """
    arr = np.asarray(values, dtype="float64")
    with warnings.catch_warnings():
        # an all-NaN pixel makes nanmean warn and return NaN; that is intended
        # (the pixel is restored to NaN below), so silence the noise.
        warnings.simplefilter("ignore", category=RuntimeWarning)
        mean = np.nanmean(arr, axis=0, keepdims=True)
    filled = np.where(np.isnan(arr), np.broadcast_to(mean, arr.shape), arr)
    return _apply_savgol(filled, window, polyorder)


def smooth_stack(
    da: xr.DataArray, window: int = 9, polyorder: int = 3, gapfill: str = "linear"
) -> xr.DataArray:
    """Gap-fill and Savitzky-Golay smooth a ``(time, lat, lon)`` VI stack.

    ``gapfill`` controls how cloud/QA gaps are filled before the filter:

    * ``"linear"`` (default) — linear interpolation along the *time coordinate*
      (edge gaps carried from the nearest valid step). This matches the MODIS
      driver's stated intent ("the downstream smoothing interpolates the gaps")
      and tracks a peaked seasonal signal far better than the mean.
    * ``"mean"`` — fill each pixel's gaps with its temporal mean, reproducing
      the legacy ``get_MODISts_PreProc.R`` (``substituteNA(type="mean")``).

    Pixels with no valid observation stay NaN either way. Returns a new
    DataArray on the same grid/time axis; the CRS coordinate and attributes are
    preserved and a ``smoothing`` attribute records the method for provenance.
    """
    if gapfill not in GAPFILL_METHODS:
        raise ValueError(
            f"gapfill must be one of {GAPFILL_METHODS}, got '{gapfill}'"
        )
    if "time" not in da.dims:
        raise ValueError("smooth_stack needs a 'time' dimension")
    ordered = da.transpose("time", ...)
    _validate_savgol(window, polyorder, ordered.sizes["time"])

    if gapfill == "mean":
        smoothed = savgol_gapfill(ordered.values, window, polyorder)
    else:  # linear
        t = ordered["time"].values.astype("datetime64[ns]").astype("float64")
        filled = _linear_gapfill(ordered.values.astype("float64"), t)
        smoothed = _apply_savgol(filled, window, polyorder)

    out = xr.DataArray(
        smoothed,
        coords=ordered.coords,
        dims=ordered.dims,
        name=ordered.name,
        attrs=dict(ordered.attrs),
    )
    out.attrs["smoothing"] = (
        f"savitzky_golay(window={window},polyorder={polyorder}); "
        f"gapfill={gapfill}"
    )
    return out


def apply_cropmask(da: xr.DataArray, mask: xr.DataArray) -> xr.DataArray:
    """Multiply a VI stack by a 1/NaN cropland mask, aligning grids by nearest.

    The mask (:func:`agwise_data.api.get_cropmask`: 1 = cropland, NaN
    elsewhere) is resampled onto the VI grid with nearest-neighbour selection —
    it is categorical, so no interpolation — then broadcast over time.
    Non-cropland pixels become NaN and are dropped by the smoothing step.
    """
    mask2d = mask.squeeze(drop=True)
    if "time" in mask2d.dims:
        raise ValueError("the cropland mask must be a static (time-free) layer")
    aligned = mask2d.interp(lat=da["lat"], lon=da["lon"], method="nearest")
    masked = da * aligned
    masked.name = da.name
    masked.attrs = dict(da.attrs)
    return masked
