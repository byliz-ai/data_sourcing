"""Two-level range quality control for harmonized products.

Every canonical variable has two ranges in its harmonized units, declared in
``qc_ranges.yaml``:

* **physical** — values outside it are impossible (a nodata or scaling error,
  e.g. 255 read as pH 25.5) and are set to NaN;
* **plausible** — values outside it are unusual but possible (an extreme
  storm, a desert afternoon) and are kept, counted and reported.

The checks run when a product is built (:func:`agwise_data.api.get_climate`,
:func:`agwise_data.api.get_static`), so the shared harmonized cache is never
altered and a request can tune the ranges without refetching. Each product
gets a ``<product>.qc.json`` report next to it.

Modes (``qc=``):

* ``"warn"`` (default) — mask physical, warn on plausible;
* ``"strict"`` — mask both (plausible outliers become NaN too);
* ``"off"`` — no checks, no masking.

``qc_ranges=`` overrides the defaults per variable, by any of its names::

    get_climate("PRCP", ..., qc_ranges={"PRCP": {"plausible": [0, 300]}})
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from pathlib import Path
from typing import Dict, Mapping, Optional

import yaml

from .cache import atomic_write

QC_MODES = ("warn", "strict", "off")
DEFAULT_MODE = "warn"
_LEVELS = ("physical", "plausible")
_RANGES_FILE = Path(__file__).parent / "qc_ranges.yaml"
_defaults: Optional[dict] = None


class QCWarning(UserWarning):
    """Values outside a variable's physical or plausible range."""


def default_ranges() -> Dict[str, dict]:
    """``{canonical: {"units", "physical", "plausible"}}`` from the YAML."""
    global _defaults
    if _defaults is None:
        with open(_RANGES_FILE) as fh:
            _defaults = yaml.safe_load(fh)["ranges"]
    return _defaults


def _canonical(variable: str) -> str:
    from .harmonize import (
        canonical_name,
        rs_canonical_name,
        static_canonical_name,
    )

    for resolve in (canonical_name, static_canonical_name, rs_canonical_name):
        try:
            return resolve(variable)
        except ValueError:
            continue
    raise ValueError(f"qc_ranges: unknown variable '{variable}'")


def _bounds(value, where: str):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(v is None or isinstance(v, (int, float)) for v in value)
    ):
        raise ValueError(f"qc_ranges {where}: expected [low, high], got {value!r}")
    lo, hi = value
    if lo is not None and hi is not None and lo > hi:
        raise ValueError(f"qc_ranges {where}: low {lo} > high {hi}")
    return [lo, hi]


def resolve_mode(mode: Optional[str]) -> str:
    mode = DEFAULT_MODE if mode is None else str(mode).lower()
    if mode not in QC_MODES:
        raise ValueError(f"qc must be one of {QC_MODES}, got {mode!r}")
    return mode


def ranges_for(variable: str, overrides: Optional[Mapping] = None) -> Optional[dict]:
    """Effective ``{"physical": [lo, hi], "plausible": [lo, hi]}`` or None.

    ``None`` means the variable has no QC ranges (e.g. TPI) and is not
    checked. ``overrides`` is the user's ``qc_ranges`` mapping; only the
    levels it names replace the defaults.
    """
    canonical = _canonical(variable)
    base = default_ranges().get(canonical)
    out = {lvl: list(base[lvl]) for lvl in _LEVELS} if base else None
    for key, levels in (overrides or {}).items():
        if _canonical(key) != canonical:
            continue
        if not isinstance(levels, Mapping) or set(levels) - set(_LEVELS):
            raise ValueError(
                f"qc_ranges['{key}']: expected a mapping with keys {_LEVELS}, "
                f"got {levels!r}"
            )
        out = out or {lvl: [None, None] for lvl in _LEVELS}
        for lvl, value in levels.items():
            out[lvl] = _bounds(value, f"['{key}']['{lvl}']")
    return out


def validate_overrides(overrides: Optional[Mapping]) -> None:
    """Fail early on unknown variables or malformed ranges."""
    for key in overrides or {}:
        ranges_for(key, overrides)


def signature(variable: str, mode: str, overrides: Optional[Mapping] = None) -> str:
    """Short hash of what QC does to this variable (product cache key).

    Two requests with the same signature produce the same product; a change
    of mode or ranges rebuilds it.
    """
    payload = json.dumps(
        {"mode": mode, "ranges": None if mode == "off" else ranges_for(variable, overrides)},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:8]


def is_default(variable: str, mode: str, overrides: Optional[Mapping] = None) -> bool:
    return signature(variable, mode, overrides) == signature(variable, DEFAULT_MODE)


def _outside(da, lo, hi):
    """(below, above) boolean masks; NaN is never outside."""
    below = da < lo if lo is not None else None
    above = da > hi if hi is not None else None
    return below, above


def apply(da, variable: str, mode: str, overrides: Optional[Mapping] = None):
    """Mask ``da`` per its ranges; return ``(masked_da, lazy_stats)``.

    ``lazy_stats`` holds (possibly dask) scalar counts; evaluate them with
    :func:`compute` (not inside a NetCDF write's graph — that deadlocks on
    the HDF5 locks) and pass the result to :func:`build_report`.
    """
    ranges = None if mode == "off" else ranges_for(variable, overrides)
    if ranges is None:
        return da, {}

    stats = {"n_values": da.size, "n_missing_input": da.isnull().sum()}
    keep = None
    for lvl in _LEVELS:
        lo, hi = ranges[lvl]
        below, above = _outside(da, lo, hi)
        for side, mask in (("below", below), ("above", above)):
            stats[f"{lvl}_{side}"] = 0 if mask is None else mask.sum()
            if mask is not None and (lvl == "physical" or mode == "strict"):
                keep = ~mask if keep is None else keep & ~mask
    out = da.where(keep) if keep is not None else da
    out.attrs.update(da.attrs)
    out.attrs["qc"] = mode
    stats["min"] = out.min()
    stats["max"] = out.max()
    return out, stats


def merge_stats(parts) -> dict:
    """Combine computed stats of several pieces (e.g. point cells) into one."""
    parts = [p for p in parts if p]
    if not parts:
        return {}
    out = {}
    for key in parts[0]:
        vals = [p[key] for p in parts]
        if key in ("min", "max"):
            vals = [float(v) for v in vals if not math.isnan(float(v))]
            pick = min if key == "min" else max
            out[key] = pick(vals) if vals else float("nan")
        else:
            out[key] = sum(int(v) for v in vals)
    return out


def compute(stats: dict) -> dict:
    """Evaluate lazy stats (dask) quietly; all-NaN chunks are expected."""
    if not stats:
        return stats
    import dask

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
        (out,) = dask.compute(stats)
    return out


def build_report(
    variable: str,
    source_id: str,
    mode: str,
    stats: dict,
    overrides: Optional[Mapping] = None,
) -> dict:
    """JSON-ready QC report from computed ``stats`` (see :func:`apply`)."""
    canonical = _canonical(variable)
    ranges = None if mode == "off" else ranges_for(variable, overrides)
    report = {
        "variable": canonical,
        "source_id": source_id,
        "mode": mode,
        "signature": signature(variable, mode, overrides),
        "ranges": ranges,
    }
    if not stats:
        report["checked"] = False
        return report

    def num(x):
        x = float(x)
        return None if math.isnan(x) else x

    counts = {k: int(stats[k]) for k in stats if k.startswith(_LEVELS)}
    report.update(
        {
            "checked": True,
            "n_values": int(stats["n_values"]),
            "n_missing_input": int(stats["n_missing_input"]),
            "physical": {
                "below": counts["physical_below"],
                "above": counts["physical_above"],
                "action": "set to NaN",
            },
            "plausible": {
                "below": counts["plausible_below"],
                "above": counts["plausible_above"],
                "action": "set to NaN" if mode == "strict" else "kept",
            },
            "min_after_qc": num(stats["min"]),
            "max_after_qc": num(stats["max"]),
        }
    )
    report["warnings"] = _messages(report)
    return report


def _messages(report: dict) -> list:
    out = []
    name = f"{report['variable']} ({report['source_id']})"
    for lvl in _LEVELS:
        info = report[lvl]
        n = info["below"] + info["above"]
        if not n:
            continue
        lo, hi = report["ranges"][lvl]
        out.append(
            f"{name}: {n} values outside the {lvl} range [{lo}, {hi}] "
            f"({info['below']} below, {info['above']} above) — {info['action']}"
        )
    return out


def emit_warnings(report: dict, stacklevel: int = 4) -> None:
    """Raise each report message as a :class:`QCWarning`.

    The default ``stacklevel`` points at the caller of a public ``get_*``
    function, via its private helper.
    """
    for msg in report.get("warnings", []):
        warnings.warn(msg, QCWarning, stacklevel=stacklevel)


def report_path(product_path: Path) -> Path:
    return product_path.with_name(product_path.name.rsplit(".", 1)[0] + ".qc.json")


def write_report(product_path: Path, report: dict) -> Path:
    path = report_path(Path(product_path))
    with atomic_write(path) as tmp:
        tmp.write_text(json.dumps(report, indent=2))
    return path
