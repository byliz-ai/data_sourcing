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


def load_aoi(geometry):
    """Load a user-supplied area of interest into an EPSG:4326 GeoDataFrame.

    ``geometry`` may be:

    * a **file path** to any vector format geopandas reads — a shapefile
      (``.shp``), GeoJSON (``.geojson``/``.json``), GeoPackage, etc.;
    * a ``GeoDataFrame`` or ``GeoSeries``;
    * a shapely geometry;
    * a GeoJSON-like ``dict`` (FeatureCollection, Feature, or bare geometry).

    The result is always reprojected to EPSG:4326 (the data CRS) so the bbox
    and the clip geometry are in degrees. All features are kept, so a
    multi-part selection (several districts, a MultiPolygon) clips as one AOI.
    """
    import geopandas as gpd
    from shapely.geometry import shape
    from shapely.geometry.base import BaseGeometry

    if isinstance(geometry, gpd.GeoDataFrame):
        gdf = geometry.copy()
    elif isinstance(geometry, gpd.GeoSeries):
        gdf = gpd.GeoDataFrame(geometry=geometry)
    elif isinstance(geometry, BaseGeometry):
        gdf = gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:4326")
    elif isinstance(geometry, dict):
        gtype = geometry.get("type")
        if gtype == "FeatureCollection":
            gdf = gpd.GeoDataFrame.from_features(geometry["features"])
        elif gtype == "Feature":
            gdf = gpd.GeoDataFrame.from_features([geometry])
        else:  # a bare GeoJSON geometry
            gdf = gpd.GeoDataFrame(geometry=[shape(geometry)], crs="EPSG:4326")
    elif isinstance(geometry, (str, Path)):
        gdf = gpd.read_file(str(geometry))
    else:
        raise TypeError(
            "geometry must be a file path, GeoDataFrame/GeoSeries, shapely "
            f"geometry, or a GeoJSON mapping (got {type(geometry).__name__})"
        )

    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError("The supplied AOI has no usable geometry")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf.reset_index(drop=True)


def aoi_tag(gdf, ref=None) -> str:
    """Stable, filesystem-safe tag for a user AOI, used for product paths.

    Derived from a hash of the geometry (so two different shapes never collide
    in the cache) and, when ``ref`` is a file path, its readable stem.
    """
    import hashlib

    payload = b"".join(g.wkb for g in gdf.geometry if g is not None)
    h = hashlib.sha1(payload).hexdigest()[:12]
    stem = ""
    if isinstance(ref, (str, Path)):
        p = Path(str(ref))
        if p.suffix:  # a real file path, not a WKT/name blob
            stem = re.sub(r"[^A-Za-z0-9]+", "-", p.stem).strip("-")[:32]
    return f"aoi_{stem}_{h}" if stem else f"aoi_{h}"


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
