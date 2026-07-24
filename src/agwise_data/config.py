"""Configuration and cache layout for the AgWise data access layer.

The single most important setting is the *data root*: the directory where
all raw downloads, harmonized yearly files and derived products live. **On
CGLabs it defaults to the shared tree automatically** (``CGLABS_PROCESSED``
below), and reusable raw inputs default to ``CGLABS_LANDING`` — so a new user
reuses the already-downloaded data with no configuration. Override only to
relocate the layer::

    export AGWISE_DATA_ROOT=~/agwise_data/cache        # off CGLabs / private cache

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
ENV_GEE_PROJECT = "AGWISE_GEE_PROJECT"
ENV_LOCAL_ROOT = "AGWISE_LOCAL_ROOT"
ENV_RAINFALL_SOURCE = "AGWISE_RAINFALL_SOURCE"
ENV_CDS_RETRIES = "AGWISE_CDS_RETRIES"
ENV_COG_WORKERS = "AGWISE_COG_WORKERS"
ENV_REGION_MAX_AREA = "AGWISE_REGION_MAX_AREA_DEG2"
ENV_DOWNLOAD_PARTS = "AGWISE_DOWNLOAD_PARTS"
ENV_READ_WORKERS = "AGWISE_READ_WORKERS"


def effective_cpu() -> int:
    """CPUs this container may actually use, read from the cgroup CPU quota.

    ``os.cpu_count()`` reports the HOST (~40 on CGLabs) but a CFS quota caps the
    container far lower (8 here); sizing a pool off ``cpu_count`` oversubscribes.
    Reads cgroup v2 ``cpu.max`` then v1 ``cpu.cfs_quota_us``/``cfs_period_us``;
    falls back to the CPU affinity set, then ``cpu_count``.
    """
    # cgroup v2: "cpu.max" is "<quota> <period>" or "max <period>".
    try:
        parts = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if parts and parts[0] != "max":
            quota, period = int(parts[0]), int(parts[1])
            if quota > 0 and period > 0:
                return max(1, quota // period)
    except (OSError, ValueError, IndexError):
        pass
    # cgroup v1.
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text().strip())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text().strip())
        if quota > 0 and period > 0:
            return max(1, quota // period)
    except (OSError, ValueError):
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return max(1, os.cpu_count() or 1)


def _cap_dask_scheduler(num_workers: int) -> None:
    """Bound dask's default threaded scheduler to ``num_workers`` (best-effort).

    Without this, each ``.load()`` fires dask's thread pool sized to
    ``os.cpu_count()`` (~40 on CGLabs) — an invisible pool that oversubscribes
    the cores and drives per-load peak memory, unaccounted by the layer's own
    worker knobs. No-op if dask is unavailable.
    """
    try:
        import dask

        dask.config.set(scheduler="threads", num_workers=max(1, int(num_workers)))
    except Exception:  # noqa: BLE001 — dask missing or config shape changed
        pass

# ---------------------------------------------------------------------------
# CGLabs shared data tree — the default home for everyone on the server, so a
# new user reuses the already-downloaded data and one shared download cache
# WITHOUT setting any environment variable. Everyone on CGLabs uses the same
# two folders; to relocate the whole layer, edit these paths (or override per
# user with $AGWISE_LOCAL_ROOT / $AGWISE_DATA_ROOT).
CGLABS_GEODATA = Path(
    "/home/jovyan/agwise-datasourcing/dataops/datasourcing/Data/Global_GeoData"
)
CGLABS_LANDING = CGLABS_GEODATA / "Landing"      # reusable raw inputs (read-only)
CGLABS_PROCESSED = CGLABS_GEODATA / "Processed"  # shared download cache (read/write)


def default_data_root() -> Optional[str]:
    """The download-cache root when the user sets nothing.

    On CGLabs this is the shared ``Processed`` folder (download once, reuse for
    everyone); elsewhere ``None`` so the caller falls back to ``~/agwise_data``.
    """
    return str(CGLABS_PROCESSED) if CGLABS_PROCESSED.is_dir() else None


def default_local_root() -> Optional[str]:
    """The read-only reusable-inputs root when the user sets nothing.

    On CGLabs this is the shared ``Landing`` tree, so already-downloaded global
    data is read from disk instead of re-downloaded; elsewhere ``None`` (the
    local-source reuse feature stays off).
    """
    return str(CGLABS_LANDING) if CGLABS_LANDING.is_dir() else None


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
        cog_workers: int = 8,
        cds_retries: int = 3,
        region_max_area_deg2: float = 400.0,
        gee_project: Optional[str] = None,
        local_root: Optional[os.PathLike] = None,
        rainfall_source: Optional[str] = None,
        read_workers: Optional[int] = None,
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
        # Parallel per-day COG window reads / tile pulls inside ONE year
        # fetch. Note this is a per-fetch fan-out: when the outer prefetch pool
        # runs several fetches in parallel, the drivers force this down to 1 so
        # peak concurrency is never max_workers x cog_workers (see api._prefetch
        # and the chirps/modis drivers). Providers rate-limit aggressive clients
        # (UCSB HTTP 403 / EE quotas), so keep it modest.
        self.cog_workers = int(cog_workers)
        # Worker PROCESSES for the parallel local-read prefetch. Threads cannot
        # parallelize netCDF reads/writes — xarray holds one global HDF5 lock
        # (HDF5 is not thread-safe), so a multi-year local read is serialized to
        # one file at a time. Separate processes each get their own lock. Reads
        # are windowed (light), so this is bounded by CPU, not the memory budget;
        # <= 1 disables it (falls back to the thread pool). See api._prefetch.
        self.read_workers = (
            int(read_workers) if read_workers is not None
            else min(effective_cpu(), 8)
        )
        self.cds_retries = int(cds_retries)
        # Region-scoped fetching kicks in below this bbox area (deg^2);
        # 400 = a 20x20 degree box, comfortably any single country.
        self.region_max_area_deg2 = float(region_max_area_deg2)
        # Real per-process memory ceiling from the cgroup (NOT the host RAM),
        # and the usable budget after headroom. None off a limited container.
        from . import memory as _mem

        self.mem_limit_bytes = _mem.detect_limit_bytes()
        self.mem_budget_bytes = _mem.usable_budget_bytes(self.mem_limit_bytes)
        # Google Cloud project registered for Earth Engine (GEE drivers).
        # Credentials themselves stay personal (~/.config/earthengine) —
        # see REFERENCE.md.
        self.gee_project = gee_project
        # Optional read-only root of already-downloaded legacy geodata (the
        # AgWise Global_GeoData/Landing tree). When set, daily drivers read a
        # matching local file (clipping to the region) instead of downloading —
        # see drivers/local.py. Default None = feature off.
        self.local_root = Path(local_root).expanduser() if local_root else None
        # Preferred rainfall source when the caller pins none. On CGLabs the
        # complete CHIRPS v3.0 series is staged locally, so default PRCP to it
        # (fast, no network) for the years it covers; off CGLabs, or for years
        # it does not cover, the climate layer falls back to CHIRPS v2.0. An
        # explicit value (env AGWISE_RAINFALL_SOURCE / this arg) always wins;
        # a user can still pass source="chirps" on any call to force v2.
        if rainfall_source is not None:
            self.rainfall_source = rainfall_source or None
        elif self.local_root and (self.local_root / "Rainfall" / "chirps_v3").is_dir():
            self.rainfall_source = "chirps_v3"
        else:
            self.rainfall_source = None
        self._discover_region_domains()

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        """Build a Config from the environment and optional YAML file.

        Precedence: environment variables > YAML config file > defaults.
        The YAML file is looked up at ``$AGWISE_DATA_CONFIG`` or
        ``~/.config/agwise_data.yaml``.
        """
        # The shared CGLabs roots live on NFS, where HDF5/NetCDF reads need
        # file locking disabled. Default it so a new user need not know this
        # (a value the user already set is left untouched).
        os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

        file_cfg: dict = {}
        cfg_path = os.environ.get(ENV_CONFIG) or str(
            Path.home() / ".config" / "agwise_data.yaml"
        )
        if Path(cfg_path).expanduser().is_file():
            with open(Path(cfg_path).expanduser()) as fh:
                file_cfg = yaml.safe_load(fh) or {}

        root = os.environ.get(ENV_ROOT) or file_cfg.get("root") or default_data_root()
        domain = os.environ.get(ENV_DOMAIN) or file_cfg.get("domain", "africa")
        keep_raw = bool(file_cfg.get("keep_raw", False))
        domains = file_cfg.get("domains")
        # max_workers: an explicit env/YAML value wins; otherwise derive it from
        # the memory budget so a smaller container shrinks the pool instead of
        # OOM-ing (it never raises above the baseline — see memory.derive_max_workers).
        from . import memory as _mem

        explicit_workers = os.environ.get(ENV_WORKERS) or file_cfg.get("max_workers")
        if explicit_workers is not None:
            workers = int(explicit_workers)
        else:
            workers = _mem.derive_max_workers(
                _mem.usable_budget_bytes(), os.cpu_count() or 1, baseline=4
            )
        # read_workers: explicit env/YAML wins; None lets the Config constructor
        # derive it from the effective (cgroup) CPU count.
        _read_workers = os.environ.get(ENV_READ_WORKERS) or file_cfg.get("read_workers")
        read_workers = int(_read_workers) if _read_workers is not None else None
        scope = os.environ.get(ENV_SCOPE) or file_cfg.get("fetch_scope", "auto")
        # Bound dask's implicit threaded scheduler — a bare .load() otherwise
        # spins up one thread per core (~40 here), an invisible pool that
        # oversubscribes CPU and inflates per-load peak memory beyond the
        # max_workers/cog_workers accounting. Cap it to the fetch worker count.
        _cap_dask_scheduler(workers)
        return cls(
            root=root,
            domain=domain,
            keep_raw=keep_raw,
            domains=domains,
            max_workers=int(workers),
            fetch_scope=scope,
            download_parts=int(
                os.environ.get(ENV_DOWNLOAD_PARTS) or file_cfg.get("download_parts", 4)
            ),
            cog_workers=int(
                os.environ.get(ENV_COG_WORKERS) or file_cfg.get("cog_workers", 8)
            ),
            read_workers=read_workers,
            cds_retries=int(
                os.environ.get(ENV_CDS_RETRIES) or file_cfg.get("cds_retries", 3)
            ),
            region_max_area_deg2=float(
                os.environ.get(ENV_REGION_MAX_AREA)
                or file_cfg.get("region_max_area_deg2", 400.0)
            ),
            gee_project=os.environ.get(ENV_GEE_PROJECT) or file_cfg.get("gee_project"),
            local_root=(
                os.environ.get(ENV_LOCAL_ROOT)
                or file_cfg.get("local_root")
                or default_local_root()
            ),
            rainfall_source=(
                os.environ.get(ENV_RAINFALL_SOURCE)
                or file_cfg.get("rainfall_source")
            ),
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

    def seasonal_path(
        self, source: str, domain: str, short: str, init_month: int, year: int
    ) -> Path:
        return (
            self.root
            / "harmonized"
            / source
            / domain
            / short
            / f"Seasonal_{short}_i{init_month:02d}_{year}.nc"
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

    def composite_path(self, source: str, domain: str, short: str, year: int) -> Path:
        return (
            self.root
            / "harmonized"
            / source
            / domain
            / short
            / f"Composite_{short}_{year}.nc"
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
