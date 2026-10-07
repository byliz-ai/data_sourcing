"""Bounded retries with exponential backoff for transient network failures.

Every non-CDS fetch path (HTTP downloads, the SoilGrids WCS, geoBoundaries,
Earth Engine ``computePixels``) used to make a single attempt, so one dropped
connection or a 503 aborted a whole run. :func:`retry_call` re-runs a call on
*transient* errors only — connection drops, timeouts, HTTP 408/429/5xx, and
Earth Engine rate-limit / internal errors — with exponential backoff and
jitter. Permanent failures (HTTP 404, a bad request, a wrong credential) are
raised immediately, untouched. CDS keeps its own wrapper (:mod:`.cds`), which
also cycles the client between attempts.

Pattern adapted from prismpy's ``sources/common/retry.py``.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger("agwise_data")

T = TypeVar("T")

TRANSIENT_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Lower-cased fragments of Earth Engine error messages worth a retry. Memory
# or computation-size errors are deliberately absent: retrying won't fix them.
_EE_TRANSIENT_MARKERS = (
    "too many concurrent",
    "too many requests",
    "rate limit",
    "internal error",
    "service unavailable",
    "deadline exceeded",
    "connection",
    "429",
    "502",
    "503",
)


class TransientError(RuntimeError):
    """A failure the raiser knows is worth retrying (e.g. truncated transfer)."""


def is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` is a transient failure worth retrying."""
    if isinstance(exc, (TransientError, ConnectionError, TimeoutError)):
        return True
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a core dependency
        requests = None
    if requests is not None:
        if isinstance(exc, requests.HTTPError):
            status = getattr(exc.response, "status_code", None)
            return status in TRANSIENT_HTTP_STATUS
        if isinstance(
            exc,
            (
                requests.ConnectionError,
                requests.Timeout,
                requests.exceptions.ChunkedEncodingError,
            ),
        ):
            return True
    if type(exc).__name__ == "EEException":
        msg = str(exc).lower()
        return any(m in msg for m in _EE_TRANSIENT_MARKERS)
    return False


def retry_call(
    fn: Callable[[], T],
    *,
    what: str,
    attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
) -> T:
    """Call ``fn()``; on a transient error retry with exponential backoff.

    The delay before retry *i* is ``base_delay * 2**(i-1)`` capped at
    ``max_delay``, scaled by a random ±``jitter`` fraction so parallel
    workers don't retry in lockstep. ``what`` names the call in log lines.
    ``on_retry(attempt, exc)`` runs before each sleep (e.g. to discard a
    partial file). Non-transient errors, and the last transient one once
    ``attempts`` is exhausted, propagate unchanged.
    """
    attempts = max(1, int(attempts))
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — filtered by is_transient
            if i == attempts or not is_transient(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (i - 1)))
            if jitter:
                delay *= 1.0 + random.uniform(-jitter, jitter)
            logger.warning(
                "%s failed (attempt %d/%d: %s) — retrying in %.1fs",
                what, i, attempts, exc, delay,
            )
            if on_retry is not None:
                on_retry(i, exc)
            if delay > 0:
                sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
