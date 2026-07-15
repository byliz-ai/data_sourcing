"""ESA WorldCover crop-mask driver: a static cropland mask via GEE.

The planting-date phenology workflow masks its NDVI/EVI series to cropland
before smoothing (``get_MODISts_PreProc.R`` step 2.4: reclassify WorldCover
class 40 → 1, everything else → NA, then multiply the composite stack).
This driver produces exactly that mask, but on the **same 1/480° grid as
the MODIS composites** so the two align pixel-for-pixel and the multiply is
a straight elementwise operation.

WorldCover is a 10 m land-cover map; a MODIS pixel is ~250 m, so each MODIS
cell covers ~530 WorldCover pixels. Rather than sample a single 10 m pixel
at the cell centre (noisy), the driver aggregates server-side: the fraction
of a cell's WorldCover pixels that are cropland (``ee.Reducer.mean`` of the
class-40 binary via ``reduceResolution``), then thresholds it at
``crop_fraction_min`` from the catalog. A cell is cropland (1.0) when at
least that fraction of it is cropland, otherwise it is masked out (NaN) —
the mask never fabricates a "maybe". Because the threshold changes which
cells are cropland, it is part of the product definition and recorded in
every manifest: bump the catalog ``version`` if you change it.

Same GEE machinery, auth and Cloud-project requirement as :mod:`.modis`
(see :mod:`.gee` and REFERENCE.md).
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from ..catalog import primary_access
from . import register
from .gee import ee_init, fetch_image_grid, grid_coords, grid_shape, plan_tiles
from .static import StaticDriver


def cropland_mask(fraction: np.ndarray, min_fraction: float) -> np.ndarray:
    """Cropland fraction in [0, 1] → binary mask (1.0 = cropland, else NaN).

    Like :func:`.modis.mask_invalid`, this only ever removes: cells below
    the threshold — and cells with no data (NaN fraction) — become NaN, so
    multiplying an NDVI stack by the mask drops non-cropland to NaN without
    inventing any value.
    """
    out = np.full(fraction.shape, np.nan, dtype="float32")
    out[np.asarray(fraction) >= min_fraction] = 1.0
    return out


@register("worldcover_gee")
class WorldCoverGeeDriver(StaticDriver):
    def _fetch_static(self, variable: str, domain: str):
        ee = ee_init(self.config.gee_project)

        access = primary_access(self.entry, "gee")
        spec = self.entry["variables"][variable]
        collection = access["collection"]
        band = spec["source_name"]
        crop_class = int(access.get("crop_class", 40))
        min_fraction = float(access.get("crop_fraction_min", 0.5))
        max_agg_px = int(access.get("reduce_max_pixels", 4096))

        bbox = self.config.bbox_for(domain)
        res = float(access.get("scale_deg", 1.0 / 480.0))
        w, s, e, n = bbox
        width, height = grid_shape(bbox, res)

        # Cropland fraction per output cell, computed server-side.
        #   .mosaic() loses the native 10 m projection, so pin it back with
        #   setDefaultProjection before reduceResolution (an EE requirement:
        #   the input grid must be finer than the aggregation target).
        native = ee.ImageCollection(collection).first().projection()
        crop = (
            ee.ImageCollection(collection)
            .mosaic()
            .eq(crop_class)
            .unmask(0)
            .setDefaultProjection(native)
        )
        grid_transform = [res, 0.0, w, 0.0, -res, n]
        fraction_img = (
            crop.reduceResolution(
                reducer=ee.Reducer.mean(), maxPixels=max_agg_px
            )
            .reproject(crs="EPSG:4326", crsTransform=grid_transform)
            .rename(band)
        )

        tiles = plan_tiles(width, height)
        arrays = fetch_image_grid(
            ee, fraction_img, [band], bbox, res, tiles, dtype="float32"
        )
        mask = cropland_mask(arrays[band], min_fraction)

        lats, lons = grid_coords(bbox, res)
        da = xr.DataArray(
            mask,
            coords={"lat": lats, "lon": lons},
            dims=("lat", "lon"),
            name=band,
        )

        return da, {
            "access": "gee",
            "gee_collection": collection,
            "scale_deg": res,
            "crop_class": crop_class,
            "crop_fraction_min": min_fraction,
        }
