"""Country/admin boundaries from geoBoundaries, cached locally.

geoBoundaries is the same source the legacy AgWise GEE code uses
(WM/geoLab/geoBoundaries), so masks stay consistent across old and new
pipelines. Boundaries are cached as GeoJSON under the shared data root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

import requests

from .cache import atomic_write, locked
from .config import Config

_GB_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM{level}/"


def iso3(country: str) -> str:
    """Resolve a country name (or ISO3 code) to its ISO3 code."""
    c = country.strip()
    if re.fullmatch(r"[A-Za-z]{3}", c) and c.isupper():
        return c
    import pycountry

    try:
        return pycountry.countries.lookup(c).alpha_3
    except LookupError:
        pass
    try:
        matches = pycountry.countries.search_fuzzy(c)
        if matches:
            return matches[0].alpha_3
    except LookupError:
        pass
    raise ValueError(
        f"Could not resolve country '{country}' to an ISO3 code. "
        "Pass the ISO3 code directly (e.g. 'KEN')."
    )


def boundary_file(config: Config, country: str, level: int = 0) -> Path:
    """Download (once) and return the cached GeoJSON for a country/admin level."""
    code = iso3(country)
    dest = config.boundaries_dir() / f"{code}_ADM{level}.geojson"
    if dest.exists():
        return dest
    with locked(dest):
        if dest.exists():
            return dest
        api = _GB_API.format(iso3=code, level=level)
        resp = requests.get(api, timeout=60)
        resp.raise_for_status()
        info = resp.json()
        if isinstance(info, list):  # API returns a list for some queries
            info = info[0]
        url = info.get("simplifiedGeometryGeoJSON") or info.get("gjDownloadURL")
        if not url:
            raise RuntimeError(f"geoBoundaries returned no geometry URL for {code} ADM{level}")
        data = requests.get(url, timeout=300)
        data.raise_for_status()
        with atomic_write(dest) as tmp:
            tmp.write_bytes(data.content)
    return dest


def load_geometry(
    config: Config,
    country: str,
    level: int = 0,
    admin_name: Optional[str] = None,
):
    """Return a GeoDataFrame for a country (optionally one admin unit)."""
    import geopandas as gpd

    path = boundary_file(config, country, level)
    gdf = gpd.read_file(path)
    if admin_name:
        if "shapeName" not in gdf.columns:
            raise RuntimeError(f"No 'shapeName' column in {path}")
        match = gdf[gdf["shapeName"].str.casefold() == admin_name.casefold()]
        if match.empty:
            raise ValueError(
                f"Admin unit '{admin_name}' not found at ADM{level} of {country}. "
                f"Examples: {sorted(gdf['shapeName'].head(20))}"
            )
        gdf = match
    return gdf


def geometry_bbox(gdf) -> Tuple[float, float, float, float]:
    w, s, e, n = gdf.total_bounds
    return float(w), float(s), float(e), float(n)


def region_tag(
    country: Optional[str] = None,
    level: int = 0,
    admin_name: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> str:
    """Filesystem-safe tag identifying a region, used for product paths."""
    if country:
        tag = iso3(country)
        if admin_name:
            clean = re.sub(r"[^A-Za-z0-9]+", "-", admin_name).strip("-")
            tag += f"_ADM{level}_{clean}"
        return tag
    if bbox:
        def fmt(v: float) -> str:
            return f"{v:g}".replace("-", "m").replace(".", "p")

        w, s, e, n = bbox
        return f"bbox_{fmt(w)}_{fmt(s)}_{fmt(e)}_{fmt(n)}"
    raise ValueError("Provide either country or bbox")
