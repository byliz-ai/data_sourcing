"""Detect the real memory budget and estimate an operation's peak.

On CGLabs the process runs in a container with a hard cgroup memory limit
(~32 GiB) — but ``free`` / ``psutil`` / ``/proc/meminfo`` report the ~220 GiB
*host*, 7x over the real cap. Sizing anything off those guarantees an OOM, so
the limit here is read ONLY from the cgroup:

- cgroup v2: ``/sys/fs/cgroup/memory.max`` (``"max"`` = unlimited),
- cgroup v1: ``/sys/fs/cgroup/memory/memory.limit_in_bytes`` (a near-INT64
  sentinel = unlimited).

``AGWISE_MEM_LIMIT_GB`` overrides detection; ``AGWISE_MEM_HEADROOM_GB`` sets the
reserve left free (NFS write-back pages + a co-resident user). ``None`` from
:func:`detect_limit_bytes` means "no cgroup cap" — off CGLabs the layer keeps
its static defaults and does no budgeting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_CGROUP_V2 = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
# cgroup v1 writes a value near INT64_MAX when there is no limit.
_UNLIMITED_MIN = 2 ** 62

ENV_MEM_LIMIT_GB = "AGWISE_MEM_LIMIT_GB"
ENV_MEM_HEADROOM_GB = "AGWISE_MEM_HEADROOM_GB"

DEFAULT_HEADROOM_GB = 8.0
# A materialized cube typically coexists with ~2 transient copies (harmonize,
# encode, regrid); size peak at ~3x the logical array unless told otherwise.
DEFAULT_TRANSIENT_FACTOR = 3.0


def _read_int(path: Path) -> Optional[int]:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if text == "max":
        return None  # v2 unlimited
    try:
        return int(text)
    except ValueError:
        return None


def detect_limit_bytes() -> Optional[int]:
    """The process's memory limit in bytes, or ``None`` if effectively unlimited.

    Order: ``AGWISE_MEM_LIMIT_GB`` env → cgroup v2 → cgroup v1. Never consults
    the host RAM (psutil/free/meminfo), which is 7x the real cap on CGLabs.
    """
    env = os.environ.get(ENV_MEM_LIMIT_GB)
    if env:
        try:
            return int(float(env) * 1024 ** 3)
        except ValueError:
            pass
    for path in (_CGROUP_V2, _CGROUP_V1):
        if path.exists():
            val = _read_int(path)
            if val is None or val >= _UNLIMITED_MIN:
                return None
            return val
    return None


def usable_budget_bytes(
    limit: Optional[int] = None, headroom_gb: Optional[float] = None
) -> Optional[int]:
    """Bytes a single process should treat as its ceiling, or ``None`` (unlimited).

    ``usable = limit - headroom``. Headroom (default 8 GiB, or
    ``AGWISE_MEM_HEADROOM_GB``) covers transiently non-reclaimable NFS
    write-back pages and one co-resident user on the shared box.
    """
    if limit is None:
        limit = detect_limit_bytes()
    if limit is None:
        return None
    if headroom_gb is None:
        headroom_gb = float(os.environ.get(ENV_MEM_HEADROOM_GB, DEFAULT_HEADROOM_GB))
    return max(1024 ** 3, int(limit - headroom_gb * 1024 ** 3))


def estimate_peak_bytes(
    width: int,
    height: int,
    n_time: int = 1,
    n_member: int = 1,
    itemsize: int = 4,
    transient_factor: float = DEFAULT_TRANSIENT_FACTOR,
) -> int:
    """Approximate the peak RAM to materialize a ``(member, time, h, w)`` cube.

    One logical array is ``width*height*n_time*n_member*itemsize``; the peak is
    that times ``transient_factor`` to cover the copies a fetch/harmonize/write
    holds at once. Used to right-size worker pools and to warn before an
    operation would exceed the budget.
    """
    base = int(width) * int(height) * max(1, int(n_time)) * max(1, int(n_member))
    return int(base * int(itemsize) * float(transient_factor))


# Representative per-fetch peak used only to right-size the worker pool.
WORKER_PEAK_BYTES = 2 * 1024 ** 3


def derive_max_workers(budget: Optional[int], cpu: int, baseline: int) -> int:
    """Worker count that keeps ``workers x per-fetch-peak`` under ``budget``.

    Only ever *reduces* from ``baseline`` (never raises it): on a small
    container the pool shrinks so concurrent fetches can't OOM, while on the
    standard box it stays at the baseline. Raising concurrency for throughput
    is a separate, tier-aware change (it also hits provider rate limits), so it
    is deliberately not done here. ``budget=None`` (no cap) keeps ``baseline``.
    """
    if budget is None:
        return max(1, baseline)
    cap = max(1, budget // WORKER_PEAK_BYTES)
    return max(1, min(baseline, cap, max(1, cpu)))


def warn_if_over_budget(
    budget: Optional[int], sizes: dict, itemsize: int, logger, what: str
) -> None:
    """Log a warning if materializing a cube of ``sizes`` likely exceeds budget.

    ``sizes`` is an xarray ``.sizes`` mapping; missing dims default to 1. No-op
    when there is no budget (unlimited container). Advisory only — it does not
    block; it points the user at a smaller region or ``AGWISE_MEM_LIMIT_GB``.
    """
    if not budget:
        return
    w = int(sizes.get("lon", sizes.get("longitude", 1)))
    h = int(sizes.get("lat", sizes.get("latitude", 1)))
    # a static cube stacks over `depth` where a daily one stacks over `time`
    n_layers = int(sizes.get("time", 1)) * int(sizes.get("depth", 1))
    peak = estimate_peak_bytes(w, h, n_layers, int(sizes.get("member", 1)), itemsize)
    if peak > budget:
        logger.warning(
            "%s: estimated peak ~%.1f GB exceeds the ~%.1f GB memory budget — "
            "risk of an OOM kill. Use a smaller region/period, or set "
            "AGWISE_MEM_LIMIT_GB if the container is actually larger.",
            what, peak / 1024 ** 3, budget / 1024 ** 3,
        )


def grid_pixels(bbox, res: float) -> tuple:
    """(width, height) cells of ``bbox`` at ``res`` degrees."""
    import math

    w, s, e, n = bbox
    return (
        max(1, int(math.ceil((e - w) / res))),
        max(1, int(math.ceil((n - s) / res))),
    )
