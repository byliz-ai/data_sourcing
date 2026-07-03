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

import os
from pathlib import Path
from typing import Optional

import yaml

# bbox = [west, south, east, north] in EPSG:4326
DEFAULT_DOMAINS = {
    "africa": {"bbox": [-20.0, -40.0, 55.0, 40.0]},
    "global": {"bbox": [-180.0, -50.0, 180.0, 50.0]},
}

ENV_ROOT = "AGWISE_DATA_ROOT"
ENV_DOMAIN = "AGWISE_DATA_DOMAIN"
ENV_CONFIG = "AGWISE_DATA_CONFIG"


class Config:
    """Resolved configuration: data root, default domain, cache behaviour."""

    def __init__(
        self,
        root: Optional[os.PathLike] = None,
        domain: str = "africa",
        keep_raw: bool = False,
        domains: Optional[dict] = None,
        refresh_partial_days: int = 30,
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
        return cls(root=root, domain=domain, keep_raw=keep_raw, domains=domains)

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

    def choose_domain(self, bbox: list) -> str:
        """Pick the smallest configured domain fully containing ``bbox``."""
        w, s, e, n = bbox
        candidates = []
        for name, spec in self.domains.items():
            dw, ds, de, dn = spec["bbox"]
            if dw <= w and ds <= s and de >= e and dn >= n:
                area = (de - dw) * (dn - ds)
                candidates.append((area, name))
        if not candidates:
            return "global"
        return min(candidates)[1]

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Config(root={self.root}, domain={self.domain}, "
            f"keep_raw={self.keep_raw})"
        )
