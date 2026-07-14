"""Shared Google Earth Engine fetch machinery.

Both the MODIS composite driver (:mod:`.modis`) and the ESA WorldCover
crop-mask driver (:mod:`.worldcover`) pull raster data the same way:
initialize the Earth Engine client once on the high-volume endpoint, split
the domain window into ``<= GEE_TILE_PX`` blocks so each
``ee.data.computePixels`` request stays under the API's size ceiling, and
read every tile onto a pixel-edge grid aligned to the domain bbox.

Authentication is personal Earth Engine credentials plus a Cloud project
registered for Earth Engine (``AGWISE_GEE_PROJECT`` env var or
``gee_project`` in ``~/.config/agwise_data.yaml``) — see
HANDOFF.md. Never hardcode credentials in scripts.
"""

from __future__ import annotations

import math
import threading
from typing import List, Optional, Sequence

import numpy as np

# Keep each computePixels request well under the API's ~48 MB ceiling:
# 2048 px squared at 2 int16 bands is ~17 MB.
GEE_TILE_PX = 2048

_EE_LOCK = threading.Lock()
_EE_READY = False


def ee_init(project: Optional[str]):
    """Initialize the Earth Engine client once per process.

    Uses the high-volume endpoint, the one Google asks programmatic
    pixel-pull workloads to use.
    """
    global _EE_READY
    try:
        import ee
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Earth Engine downloads need the 'earthengine-api' package: "
            "pip install earthengine-api"
        ) from exc
    with _EE_LOCK:
        if not _EE_READY:
            ee.Initialize(
                project=project or None,
                url="https://earthengine-highvolume.googleapis.com",
            )
            _EE_READY = True
    return ee


def plan_tiles(width: int, height: int, tile: int = GEE_TILE_PX) -> List[tuple]:
    """Split a raster window into <= tile x tile pixel blocks.

    Returns ``[(x_off, y_off, block_w, block_h), ...]`` covering the full
    window — the request plan for the per-image GEE pixel pulls.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"Empty window: {width} x {height}")
    return [
        (x, y, min(tile, width - x), min(tile, height - y))
        for y in range(0, height, tile)
        for x in range(0, width, tile)
    ]


def grid_shape(bbox: Sequence[float], res: float) -> tuple:
    """Pixel (width, height) of the domain bbox at resolution ``res`` (deg)."""
    w, s, e, n = bbox
    width = int(math.ceil((e - w) / res))
    height = int(math.ceil((n - s) / res))
    return width, height


def grid_coords(bbox: Sequence[float], res: float) -> tuple:
    """Pixel-center ``(lats, lons)`` for the bbox grid, top row (max lat) first."""
    w, s, e, n = bbox
    width, height = grid_shape(bbox, res)
    lats = n - res * (np.arange(height) + 0.5)
    lons = w + res * (np.arange(width) + 0.5)
    return lats, lons


def _tile_grid(x0: int, y0: int, bw: int, bh: int, bbox, res: float) -> dict:
    """The computePixels ``grid`` block for one tile of the bbox grid."""
    w, s, e, n = bbox
    return {
        "dimensions": {"width": bw, "height": bh},
        "affineTransform": {
            "scaleX": res,
            "shearX": 0,
            "translateX": w + x0 * res,
            "shearY": 0,
            "scaleY": -res,
            "translateY": n - y0 * res,
        },
        "crsCode": "EPSG:4326",
    }


def fetch_image_grid(
    ee,
    image,
    bands: Sequence[str],
    bbox: Sequence[float],
    res: float,
    tiles: Sequence[tuple],
    dtype: str = "int16",
) -> dict:
    """Pull ``image``'s ``bands`` over the tiled bbox grid into arrays.

    Returns ``{band: np.ndarray(height, width)}`` on the pixel-edge grid
    aligned to ``bbox`` at resolution ``res`` (degrees). ``tiles`` is a
    :func:`plan_tiles` plan for that grid. One image, all tiles serial —
    callers parallelize across images (composites) when there are many.
    """
    width, height = grid_shape(bbox, res)
    out = {b: np.empty((height, width), dtype=dtype) for b in bands}
    img = image.select(list(bands))
    for x0, y0, bw, bh in tiles:
        block = ee.data.computePixels(
            {
                "expression": img,
                "fileFormat": "NUMPY_NDARRAY",
                "grid": _tile_grid(x0, y0, bw, bh, bbox, res),
            }
        )
        for b in bands:
            out[b][y0 : y0 + bh, x0 : x0 + bw] = block[b]
    return out
