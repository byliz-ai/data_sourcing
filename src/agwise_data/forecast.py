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
"""

from __future__ import annotations

import numpy as np
import xarray as xr

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


def bias_correct_cube(obs, hind, fcst, kind="additive", window_days=None):
    """QDM-correct a forecast cube against hindcast+observation cubes.

    ``obs`` is ``(time, lat, lon)``; ``hind`` and ``fcst`` are
    ``(member, time, lat, lon)``. ``hind``/``fcst`` are first interpolated onto
    the ``obs`` grid (downscaling the coarse forecast, as the reference does).
    Hindcast members are pooled into the model climatology. ``window_days``
    (half-width) restricts calibration to samples within +/- that many
    days-of-year of each forecast step; ``None`` pools the whole season.
    Returns a corrected cube shaped like the regridded ``fcst``.
    """
    hind = hind.interp(lat=obs["lat"], lon=obs["lon"], method="linear")
    fcst = fcst.interp(lat=obs["lat"], lon=obs["lon"], method="linear")

    obs_doy = _doy(obs["time"].values)
    hind_doy = _doy(hind["time"].values)
    fcst_doy = _doy(fcst["time"].values)

    obs_v = obs.transpose("time", "lat", "lon").values
    hind_v = hind.transpose("member", "time", "lat", "lon").values
    fcst_v = fcst.transpose("member", "time", "lat", "lon").values
    M, T, H, W = fcst_v.shape
    out = np.full_like(fcst_v, np.nan)

    # forecast steps grouped by day-of-year window (whole season if None)
    if window_days is None:
        groups = [(np.ones(T, bool), np.ones(len(obs_doy), bool),
                   np.ones(len(hind_doy), bool))]
    else:
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
