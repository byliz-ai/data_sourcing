"""Configuration and cache layout for the AgWise data access layer.

The single most important setting is the *data root*: the directory where
all raw downloads, harmonized yearly files and derived products live.
On CGLabs point it at a shared location so one download serves everyone::

    export AGWISE_DATA_ROOT=/home/jovyan/common_data/agwise_data

Layout under the data root::

    raw/<source>/                                transient downloads (deleted
                                                 unless keep_raw is true)
    harmonized/<source>/<domain>/<VAR>/          Daily_<VAR>_<year>.nc
    products/<region_tag>/                       Monthly_PRCP_2005_2024.nc, .tif
    boundaries/                                  cached admin boundaries (geojson)

Every harmonized/product file gets a JSON sidecar (``<file>.meta.json``)
recording provenance: source URL/request, download time, catalog id and
processing steps. That manifest is what the CGIAR data hubs will ingest
when these products migrate there.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Optional, Sequence

import yaml

# bbox = [west, south, east, north] in EPSG:4326
DEFAULT_DOMAINS = {
    "africa": {"bbox": [-20.0, -40.0, 55.0, 40.0]},
    "global": {"bbox": [-180.0, -50.0, 180.0, 50.0]},
}

ENV_ROOT = "AGWISE_DATA_ROOT"
ENV_DOMAIN = "AGWISE_DATA_DOMAIN"
ENV_CONFIG = "AGWISE_DATA_CONFIG"
ENV_WORKERS = "AGWISE_DATA_WORKERS"
ENV_SCOPE = "AGWISE_DATA_SCOPE"


# ---------------------------------------------------------------------------
# Region-scoped cache domains ("rg_*"): when a request covers a small area
# (one country, a trial-site bbox), fetching the whole default domain is a
# waste — a Rwanda request needs ~0.03% of the global CHIRPS file. These
# helpers give such a request its own stable, filesystem-safe domain so the
# fetch is bounded to (a padded, rounded version of) the area actually
# needed, and later requests for the same area reuse it.

def round_region_bbox(bbox: Sequence[float], pad: float = 0.5) -> list:
    """Pad a bbox and round it outward to whole degrees (stable cache key)."""
    w, s, e, n = bbox
    return [
        max(-180.0, float(math.floor(w - pad))),
        max(-90.0, float(math.floor(s - pad))),
        min(180.0, float(math.ceil(e + pad))),
        min(90.0, float(math.ceil(n + pad))),
    ]


def _fmt_deg(v: float) -> str:
    return str(int(v)).replace("-", "m")


def region_domain_name(bbox: Sequence[float]) -> str:
    w, s, e, n = bbox
    return f"rg_{_fmt_deg(w)}_{_fmt_deg(s)}_{_fmt_deg(e)}_{_fmt_deg(n)}"


def parse_region_name(name: str) -> Optional[list]:
    m = re.fullmatch(r"rg_(m?\d+)_(m?\d+)_(m?\d+)_(m?\d+)", name)
    if not m:
        return None
    return [float(g.replace("m", "-")) for g in m.groups()]


class Config:
    """Resolved configuration: data root, default domain, cache behaviour."""

    def __init__(
        self,
        root: Optional[os.PathLike] = None,
        domain: str = "africa",
        keep_raw: bool = False,
        domains: Optional[dict] = None,
        refresh_partial_days: int = 30,
        max_workers: int = 4,
        fetch_scope: str = "auto",
        download_parts: int = 4,
        cog_workers: int = 3,
        region_max_area_deg2: float = 400.0,
    ):
        self.root = Path(root).expanduser() if root else Path.home() / "agwise_data"
        self.domain = domain
        self.keep_raw = keep_raw
        self.domains = dict(DEFAULT_DOMAINS)
        if domains:
            self.domains.update(domains)
        # A harmonized file for the current (incomplete) year is refreshed
        # when it is older than this many days.
        self.refresh_partial_days = refresh_partial_days
        # Parallel (variable, year) fetches (CDS queue waits overlap etc.).
        self.max_workers = int(max_workers)
        # "auto": small requests get a region-scoped cache; "domain": always
        # fetch the full default domain (bulk/CGLabs mode); "region" is
        # implied by auto for small areas.
        self.fetch_scope = fetch_scope
        # Parallel HTTP range connections for large single-file downloads.
        self.download_parts = int(download_parts)
        # Parallel per-day COG window reads inside one year fetch. Kept low
        # on purpose: data providers rate-limit aggressive clients (UCSB
        # answers HTTP 403 and can temporarily ban the IP).
        self.cog_workers = int(cog_workers)
        # Region-scoped fetching kicks in below this bbox area (deg^2);
        # 400 = a 20x20 degree box, comfortably any single country.
        self.region_max_area_deg2 = float(region_max_area_deg2)
        self._discover_region_domains()

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        """Build a Config from the environment and optional YAML file.

        Precedence: environment variables > YAML config file > defaults.
        The YAML file is looked up at ``$AGWISE_DATA_CONFIG`` or
        ``~/.config/agwise_data.yaml``.
        """
        file_cfg: dict = {}
        cfg_path = os.environ.get(ENV_CONFIG) or str(
            Path.home() / ".config" / "agwise_data.yaml"
        )
        if Path(cfg_path).expanduser().is_file():
            with open(Path(cfg_path).expanduser()) as fh:
                file_cfg = yaml.safe_load(fh) or {}

        root = os.environ.get(ENV_ROOT) or file_cfg.get("root")
        domain = os.environ.get(ENV_DOMAIN) or file_cfg.get("domain", "africa")
        keep_raw = bool(file_cfg.get("keep_raw", False))
        domains = file_cfg.get("domains")
        workers = os.environ.get(ENV_WORKERS) or file_cfg.get("max_workers", 4)
        scope = os.environ.get(ENV_SCOPE) or file_cfg.get("fetch_scope", "auto")
        return cls(
            root=root,
            domain=domain,
            keep_raw=keep_raw,
            domains=domains,
            max_workers=int(workers),
            fetch_scope=scope,
            download_parts=int(file_cfg.get("download_parts", 4)),
            cog_workers=int(file_cfg.get("cog_workers", 8)),
            region_max_area_deg2=float(file_cfg.get("region_max_area_deg2", 400.0)),
        )

    # ------------------------------------------------------------------
    def register_domain(self, name: str, bbox: Sequence[float]) -> None:
        self.domains[name] = {"bbox": list(bbox)}

    def _discover_region_domains(self) -> None:
        """Re-register region domains found on disk from previous sessions,
        so their caches keep being reused across processes."""
        hdir = self.root / "harmonized"
        if not hdir.is_dir():
            return
        try:
            for p in hdir.glob("*/rg_*"):
                if p.name in self.domains:
                    continue
                bbox = parse_region_name(p.name)
                if bbox:
                    self.domains[p.name] = {"bbox": bbox}
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Path helpers — the single place that defines the cache layout.
    def raw_dir(self, source: str) -> Path:
        return self.root / "raw" / source

    def harmonized_path(self, source: str, domain: str, short: str, year: int) -> Path:
        return (
            self.root
            / "harmonized"
            / source
            / domain
            / short
            / f"Daily_{short}_{year}.nc"
        )

    def static_path(self, source: str, domain: str, short: str) -> Path:
        return (
            self.root
            / "harmonized"
            / source
            / domain
            / short
            / f"Static_{short}.nc"
        )

    def products_dir(self, region_tag: str) -> Path:
        return self.root / "products" / region_tag

    def boundaries_dir(self) -> Path:
        return self.root / "boundaries"

    def bbox_for(self, domain: str) -> list:
        try:
            return list(self.domains[domain]["bbox"])
        except KeyError:
            raise KeyError(
                f"Unknown domain '{domain}'. Known domains: {sorted(self.domains)}"
            )

    def containing_domains(self, bbox: list, include_regions: bool = True) -> list:
        """Names of configured domains fully containing ``bbox``, smallest
        first. With ``include_regions=False`` region-scoped ``rg_*`` caches
        are skipped (used by ``fetch_scope="domain"``)."""
        w, s, e, n = bbox
        candidates = []
        for name, spec in self.domains.items():
            if not include_regions and name.startswith("rg_"):
                continue
            dw, ds, de, dn = spec["bbox"]
            if dw <= w and ds <= s and de >= e and dn >= n:
                area = (de - dw) * (dn - ds)
                candidates.append((area, name))
        return [name for _, name in sorted(candidates)]

    def choose_domain(self, bbox: list, include_regions: bool = True) -> str:
        """The smallest configured domain fully containing ``bbox``."""
        domains = self.containing_domains(bbox, include_regions)
        return domains[0] if domains else "global"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Config(root={self.root}, domain={self.domain}, "
            f"keep_raw={self.keep_raw})"
        )
