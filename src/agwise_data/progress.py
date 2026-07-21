"""Progress bars for long-running fetches and per-point/per-tile loops.

A single call can spend minutes downloading many (variable, year) files,
pulling MODIS composites tile by tile, or writing crop-model files for a
long list of points — and without feedback the user cannot tell a slow job
from a hung one. These helpers put a lightweight bar on that work.

Design:
- **stderr, never stdout.** The CLI prints one JSON line to *stdout* that
  callers parse; progress goes to *stderr* so it never corrupts that.
- **Auto-quiet.** By default the bar shows only when stderr is a terminal, so
  piped/cron/CI runs and the JSON consumers stay clean. Override with
  ``AGWISE_PROGRESS=always`` (force on) or ``AGWISE_PROGRESS=never`` (force off).
- **Soft dependency.** Uses ``tqdm`` when importable (rate + ETA); otherwise a
  minimal built-in bar. Either way the wrapped work runs unchanged.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import Future, as_completed
from typing import Iterable, Iterator, Optional, Sequence

_ENV = "AGWISE_PROGRESS"


def enabled() -> bool:
    """Whether to draw a bar: ``AGWISE_PROGRESS`` override, else stderr-is-a-TTY."""
    mode = os.environ.get(_ENV, "auto").strip().lower()
    if mode in ("0", "off", "never", "false", "no"):
        return False
    if mode in ("1", "on", "always", "true", "yes"):
        return True
    try:
        return bool(sys.stderr.isatty())
    except Exception:  # noqa: BLE001 — a weird stderr just means "no bar"
        return False


def track(iterable: Iterable, total: Optional[int] = None, desc: str = "") -> Iterator:
    """Yield from ``iterable`` while drawing a progress bar on stderr.

    A no-op pass-through when progress is disabled, so it is always safe to
    wrap a loop with this.
    """
    if not enabled():
        yield from iterable
        return
    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = None
    try:
        from tqdm.auto import tqdm

        yield from tqdm(iterable, total=total, desc=desc, file=sys.stderr, leave=False)
        return
    except Exception:  # noqa: BLE001 — fall back to the built-in bar
        pass
    yield from _basic_bar(iterable, total, desc)


def _basic_bar(iterable: Iterable, total: Optional[int], desc: str) -> Iterator:
    """Dependency-free fallback: ``desc [####----] n/total`` on stderr."""
    n = 0
    width = 24

    def render():
        if total:
            filled = int(width * n / total)
            bar = "#" * filled + "-" * (width - filled)
            sys.stderr.write(f"\r{desc} [{bar}] {n}/{total}")
        else:
            sys.stderr.write(f"\r{desc} {n}")
        sys.stderr.flush()

    render()
    for item in iterable:
        yield item
        n += 1
        render()
    sys.stderr.write("\n")
    sys.stderr.flush()


def drain_futures(futures: Sequence[Future], desc: str = "") -> None:
    """Wait on parallel ``futures``, advancing a bar as each finishes.

    Re-raises the first failure (like ``fut.result()`` in a plain loop), so it
    is a drop-in for ``for fut in futures: fut.result()``.
    """
    for fut in track(as_completed(futures), total=len(futures), desc=desc):
        fut.result()
