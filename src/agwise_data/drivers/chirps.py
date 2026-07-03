"""CHIRPS driver: yearly NetCDF files from the UCSB Climate Hazards Center.

Downloads the global yearly file (~1.1 GB compressed), crops it to the
configured domain, harmonizes it and stores it as ``Daily_PRCP_<year>.nc``.
The raw download is deleted afterwards unless ``keep_raw`` is set, so disk
usage is one harmonized copy shared by all.
"""

from __future__ import annotations

from datetime import date

import xarray as xr

from .. import cache
from ..catalog import primary_access, variable_spec
from ..harmonize import apply_conversion
from ..spatial import subset_bbox
from . import register
from .base import Driver


@register("chirps")
class ChirpsDriver(Driver):
    def _fetch_year(self, variable: str, year: int, domain: str):
        access = primary_access(self.entry, "https")
        urls = access["urls"]
        # Use the domain-specific file when it exists (smaller); otherwise
        # fall back to the global file and crop.
        url_key = domain if domain in urls else "global"
        url = urls[url_key].format(year=year)

        raw = self.config.raw_dir(self.source_id) / url.rsplit("/", 1)[-1]
        # The current year's file grows as UCSB publishes new months, so a
        # kept raw copy must not satisfy a partial-year refresh.
        cache.download_file(url, raw, skip_if_exists=year < date.today().year)

        spec = variable_spec(self.source_id, variable)
        with xr.open_dataset(raw, chunks={"time": 92}) as ds:
            da = ds[spec["source_name"]]
            da = subset_bbox(da, self.config.bbox_for(domain))
            da = apply_conversion(da, spec.get("conversion"))
            da = da.load()

        if not self.config.keep_raw:
            raw.unlink(missing_ok=True)

        return da, {"source_url": url}
