"""AgERA5 driver: Copernicus Climate Data Store (CDS) downloads.

Requests only the configured domain's bounding box (not the whole globe,
unlike the legacy scripts), merges the daily files in the returned zip
into one harmonized yearly NetCDF, and cleans up after itself.

Authentication: a free CDS account and a ``~/.cdsapirc`` file::

    url: https://cds.climate.copernicus.eu/api
    key: <your-personal-access-token>

Never hardcode the token in scripts — see REFERENCE.md.
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

import xarray as xr

from .. import cache
from ..catalog import primary_access, variable_spec
from ..harmonize import apply_conversion
from ..spatial import subset_bbox
from . import register
from .base import Driver

# AgERA5 publication lag is about a week; leave margin.
_PUBLICATION_LAG_DAYS = 10


def _months_days_for(year: int):
    """Full year for past years; up to the last safely-published month for
    the current year (requesting unpublished dates makes CDS fail)."""
    today = date.today()
    if year < today.year:
        months = list(range(1, 13))
    else:
        last_safe = today - timedelta(days=_PUBLICATION_LAG_DAYS)
        if last_safe.year < year or (last_safe.month == 1 and last_safe.day < 28):
            raise ValueError(
                f"No complete month of AgERA5 published yet for {year}"
            )
        # up to the last fully published month
        last_month = last_safe.month if last_safe.day >= 28 else last_safe.month - 1
        if last_month < 1:
            raise ValueError(f"No complete month of AgERA5 published yet for {year}")
        months = list(range(1, last_month + 1))
    return (
        [f"{m:02d}" for m in months],
        [f"{d:02d}" for d in range(1, 32)],
    )


@register("agera5")
class Agera5Driver(Driver):
    def _fetch_year(self, variable: str, year: int, domain: str):
        try:
            import cdsapi
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "AgERA5 downloads need the 'cdsapi' package: "
                "pip install 'agwise-data[cds]'"
            ) from exc

        access = primary_access(self.entry, "cds")
        spec = variable_spec(self.source_id, variable)
        w, s, e, n = self.config.bbox_for(domain)
        months, days = _months_days_for(year)

        request = {
            "variable": spec["source_name"],
            "year": str(year),
            "month": months,
            "day": days,
            "version": access.get("version", "2_0"),
            "area": [n, w, s, e],  # CDS order: North, West, South, East
        }
        if spec.get("statistic"):
            request["statistic"] = spec["statistic"]
        if spec.get("extra_request"):
            request.update(spec["extra_request"])

        raw_dir = self.config.raw_dir(self.source_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        zip_path = raw_dir / f"{self.source_id}_{variable.replace('.', '_')}_{year}_{domain}.zip"

        client = cdsapi.Client()
        client.retrieve(access["dataset"], request, str(zip_path))

        with tempfile.TemporaryDirectory(dir=raw_dir) as tmpdir:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmpdir)
            nc_files = sorted(Path(tmpdir).glob("*.nc"))
            if not nc_files:
                raise RuntimeError(
                    f"CDS zip for {variable} {year} contained no NetCDF files"
                )
            with cache.NC_LOCK:
                ds = xr.open_mfdataset(
                    nc_files, combine="by_coords", parallel=False
                )
                da = ds[_main_var(ds)]
                da = subset_bbox(da, self.config.bbox_for(domain))
                da = apply_conversion(da, spec.get("conversion"))
                da = da.load()
                ds.close()

        if not self.config.keep_raw:
            zip_path.unlink(missing_ok=True)

        return da, {
            "cds_dataset": access["dataset"],
            "cds_request": {k: v for k, v in request.items() if k not in ("month", "day")},
            "months": f"{months[0]}..{months[-1]}",
        }


def _main_var(ds: xr.Dataset) -> str:
    """The single data variable in an AgERA5 file (ignoring bounds/crs)."""
    candidates = [
        v
        for v in ds.data_vars
        if not v.endswith("_bnds") and v.lower() not in ("crs", "spatial_ref")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one data variable in AgERA5 file, found {candidates}"
        )
    return candidates[0]
