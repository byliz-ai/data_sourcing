import json
from datetime import datetime, timedelta, timezone

from agwise_data.cache import (
    atomic_write,
    is_stale_partial,
    manifest_path,
    read_manifest,
    write_manifest,
)


def test_atomic_write_cleans_up_on_failure(tmp_path):
    dest = tmp_path / "out.bin"
    try:
        with atomic_write(dest) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not dest.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_success(tmp_path):
    dest = tmp_path / "out.bin"
    with atomic_write(dest) as tmp:
        tmp.write_bytes(b"data")
    assert dest.read_bytes() == b"data"


def test_manifest_roundtrip(tmp_path):
    data = tmp_path / "Daily_PRCP_2020.nc"
    data.write_bytes(b"x")
    write_manifest(data, {"source_id": "chirps", "year": 2020, "partial": False})
    meta = read_manifest(data)
    assert meta["source_id"] == "chirps"
    assert meta["year"] == 2020
    assert "created_utc" in meta


def test_read_manifest_missing_or_corrupt(tmp_path):
    data = tmp_path / "f.nc"
    data.write_bytes(b"x")
    assert read_manifest(data) == {}
    manifest_path(data).write_text("{not json")
    assert read_manifest(data) == {}


def test_stale_partial_logic(tmp_path):
    data = tmp_path / "Daily_PRCP_2026.nc"
    data.write_bytes(b"x")

    # complete year: never stale
    write_manifest(data, {"partial": False})
    assert not is_stale_partial(data, max_age_days=0)

    # fresh partial: not stale yet
    write_manifest(data, {"partial": True})
    assert not is_stale_partial(data, max_age_days=30)
    assert is_stale_partial(data, max_age_days=0)

    # old partial: stale
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(
        timespec="seconds"
    )
    meta = read_manifest(data)
    meta["created_utc"] = old
    manifest_path(data).write_text(json.dumps(meta))
    assert is_stale_partial(data, max_age_days=30)
