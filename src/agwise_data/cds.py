"""Resilient Copernicus Climate Data Store (CDS) retrieval.

A cold seasonal-forecast run issues many SEAS5 requests back to back, and a
single network drop mid-download would otherwise abort the whole run (the
partial file is discarded and nothing is written). :func:`retrieve` wraps
``cdsapi``'s ``retrieve`` with bounded retries and exponential backoff,
removing any partial download between attempts and starting from a fresh
client each try so a broken connection is reset. The submit → queue → download
cycle is re-run on retry, but CDS caches a completed result server-side, so a
retried download usually returns quickly.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger("agwise_data")


def retrieve(
    dataset: str,
    request: dict,
    target,
    *,
    attempts: int = 3,
    base_delay: float = 10.0,
    client=None,
) -> Path:
    """Download a CDS ``dataset`` request to ``target``, retrying on failure.

    Retries any exception up to ``attempts`` times with exponential backoff
    (``base_delay`` doubling each try), deleting a partial ``target`` between
    attempts. ``client`` is for tests (a real ``cdsapi.Client`` is built per
    attempt otherwise). Raises ``RuntimeError`` (chained to the last error)
    once attempts are exhausted.
    """
    target = Path(target)
    attempts = max(1, int(attempts))
    last_exc = None
    for i in range(1, attempts + 1):
        c = client
        if c is None:
            import cdsapi

            c = cdsapi.Client()
        try:
            c.retrieve(dataset, request, str(target))
            return target
        except Exception as exc:  # noqa: BLE001 — any failure is worth a retry
            last_exc = exc
            try:
                target.unlink(missing_ok=True)  # drop the partial download
            except OSError:
                pass
            if i == attempts:
                break
            delay = base_delay * (2 ** (i - 1))
            logger.warning(
                "CDS retrieve failed for '%s' (attempt %d/%d: %s) — "
                "retrying in %.0fs",
                dataset, i, attempts, exc, delay,
            )
            if delay > 0:
                time.sleep(delay)
    raise RuntimeError(
        f"CDS retrieve for '{dataset}' failed after {attempts} attempt(s)"
    ) from last_exc
