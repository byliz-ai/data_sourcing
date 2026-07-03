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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests
from filelock import FileLock

DOWNLOAD_TIMEOUT = (30, 600)  # (connect, read) seconds
CHUNK = 8 * 1024 * 1024
# Below this size a single stream is fine; above it, parallel range
# requests meaningfully beat one TCP connection's throughput.
PART_MIN_BYTES = 64 * 1024 * 1024


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


def _split_ranges(size: int, parts: int) -> list:
    """Byte ranges [(start, end), ...] covering ``size`` bytes in ``parts``."""
    parts = max(1, min(int(parts), size))
    step = size // parts
    bounds = []
    start = 0
    for i in range(parts):
        end = size - 1 if i == parts - 1 else start + step - 1
        bounds.append((start, end))
        start = end + 1
    return bounds


def _probe(url: str):
    """(content_length, supports_ranges) — (0, False) when HEAD is unusable."""
    try:
        resp = requests.head(url, timeout=30, allow_redirects=True)
        if resp.status_code == 404:
            raise FileNotFoundError(_not_published(url))
        resp.raise_for_status()
        size = int(resp.headers.get("Content-Length") or 0)
        ok = resp.headers.get("Accept-Ranges", "").lower() == "bytes" and size > 0
        return size, ok
    except FileNotFoundError:
        raise
    except (requests.RequestException, ValueError):
        return 0, False


def _not_published(url: str) -> str:
    return (
        f"Source file not published (HTTP 404): {url}\n"
        "For the current year the provider may not have released final "
        "data yet."
    )


def _download_stream(url: str, dest: Path) -> None:
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
        if resp.status_code == 404:
            raise FileNotFoundError(_not_published(url))
        resp.raise_for_status()
        with atomic_write(dest) as tmp:
            with open(tmp, "wb") as fh:
                shutil.copyfileobj(resp.raw, fh, length=CHUNK)


def _download_segmented(url: str, dest: Path, size: int, parts: int) -> None:
    """Fetch ``parts`` byte ranges concurrently into a preallocated file."""
    with atomic_write(dest) as tmp:
        with open(tmp, "wb") as fh:
            fh.truncate(size)

        def grab(bounds):
            a, b = bounds
            with requests.get(
                url,
                headers={"Range": f"bytes={a}-{b}"},
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
            ) as resp:
                if resp.status_code != 206:
                    raise RuntimeError(
                        f"Server ignored Range request (HTTP {resp.status_code}): {url}"
                    )
                with open(tmp, "r+b") as fh:
                    fh.seek(a)
                    for chunk in resp.iter_content(CHUNK):
                        fh.write(chunk)

        with ThreadPoolExecutor(max_workers=parts) as ex:
            list(ex.map(grab, _split_ranges(size, parts)))  # re-raises first error

        got = tmp.stat().st_size
        if got != size:
            raise RuntimeError(
                f"Segmented download size mismatch for {url}: {got} != {size}"
            )


def download_file(
    url: str, dest: Path, skip_if_exists: bool = True, parts: int = 1
) -> Path:
    """Download ``url`` to ``dest`` atomically, under a lock, skipping if present.

    With ``parts > 1`` and a server that supports byte ranges, large files
    are fetched over several parallel connections (falls back to a single
    stream otherwise).
    """
    if skip_if_exists and dest.exists():
        return dest
    with locked(dest):
        if skip_if_exists and dest.exists():  # someone else finished it
            return dest
        size, ranges_ok = _probe(url)
        if parts > 1 and ranges_ok and size >= PART_MIN_BYTES:
            _download_segmented(url, dest, size, parts)
        else:
            _download_stream(url, dest)
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
