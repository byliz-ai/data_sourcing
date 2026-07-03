"""Shared-cache primitives: atomic downloads, locking and provenance manifests.

Several people share the same data root on CGLabs, so every write goes
through a file lock (two users asking for the same year of CHIRPS at the
same time results in one download, not a corrupted file) and lands via an
atomic rename (a killed process never leaves a half-written file that
would then be trusted by ``skip-if-exists``).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests
from filelock import FileLock

DOWNLOAD_TIMEOUT = (30, 600)  # (connect, read) seconds
CHUNK = 8 * 1024 * 1024


@contextmanager
def locked(path: Path):
    """File lock guarding the creation of ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock")
    with lock:
        yield


@contextmanager
def atomic_write(path: Path):
    """Yield a temporary path that is atomically renamed to ``path`` on success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        yield tmp_path
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def download_file(url: str, dest: Path, skip_if_exists: bool = True) -> Path:
    """Stream ``url`` to ``dest`` atomically, under a lock, skipping if present."""
    if skip_if_exists and dest.exists():
        return dest
    with locked(dest):
        if skip_if_exists and dest.exists():  # someone else finished it
            return dest
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"Source file not published (HTTP 404): {url}\n"
                    "For the current year the provider may not have released "
                    "final data yet."
                )
            resp.raise_for_status()
            with atomic_write(dest) as tmp:
                with open(tmp, "wb") as fh:
                    shutil.copyfileobj(resp.raw, fh, length=CHUNK)
    return dest


# ---------------------------------------------------------------------------
def manifest_path(data_path: Path) -> Path:
    return data_path.with_name(data_path.name + ".meta.json")


def write_manifest(data_path: Path, meta: dict) -> Path:
    """Write the provenance sidecar for a cached file."""
    record = {
        "file": data_path.name,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "created_by": "agwise-data",
        **meta,
    }
    mpath = manifest_path(data_path)
    with atomic_write(mpath) as tmp:
        tmp.write_text(json.dumps(record, indent=2, default=str))
    return mpath


def read_manifest(data_path: Path) -> dict:
    mpath = manifest_path(data_path)
    if not mpath.exists():
        return {}
    try:
        return json.loads(mpath.read_text())
    except json.JSONDecodeError:
        return {}


def is_stale_partial(data_path: Path, max_age_days: int) -> bool:
    """True if the file is a partial-year product old enough to refresh."""
    meta = read_manifest(data_path)
    if not meta.get("partial"):
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(
        meta["created_utc"]
    ).astimezone(timezone.utc)
    return age.days >= max_age_days
