"""The CGLabs data-root defaults: present -> use them, absent -> fall back,
env always overrides."""

from agwise_data import config as C
from agwise_data.config import Config


def _point_cglabs_at(monkeypatch, tmp_path, exists: bool):
    land = tmp_path / "Landing"
    proc = tmp_path / "Processed"
    if exists:
        land.mkdir()
        proc.mkdir()
    monkeypatch.setattr(C, "CGLABS_LANDING", land)
    monkeypatch.setattr(C, "CGLABS_PROCESSED", proc)
    return land, proc


def test_defaults_used_when_cglabs_tree_present(monkeypatch, tmp_path):
    land, proc = _point_cglabs_at(monkeypatch, tmp_path, exists=True)
    assert C.default_local_root() == str(land)
    assert C.default_data_root() == str(proc)


def test_defaults_none_when_cglabs_tree_absent(monkeypatch, tmp_path):
    _point_cglabs_at(monkeypatch, tmp_path, exists=False)
    assert C.default_local_root() is None
    assert C.default_data_root() is None


def test_env_overrides_cglabs_defaults(monkeypatch, tmp_path):
    _point_cglabs_at(monkeypatch, tmp_path, exists=True)
    monkeypatch.setenv("AGWISE_DATA_ROOT", str(tmp_path / "mycache"))
    monkeypatch.setenv("AGWISE_LOCAL_ROOT", str(tmp_path / "mylanding"))
    monkeypatch.setenv("AGWISE_DATA_CONFIG", str(tmp_path / "none.yaml"))
    cfg = Config.load()
    assert str(cfg.root) == str(tmp_path / "mycache")
    assert str(cfg.local_root) == str(tmp_path / "mylanding")


def test_load_falls_back_to_home_when_nothing_set(monkeypatch, tmp_path):
    _point_cglabs_at(monkeypatch, tmp_path, exists=False)
    monkeypatch.delenv("AGWISE_DATA_ROOT", raising=False)
    monkeypatch.delenv("AGWISE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("AGWISE_DATA_CONFIG", str(tmp_path / "none.yaml"))
    cfg = Config.load()
    assert cfg.local_root is None
    assert str(cfg.root).endswith("agwise_data")


def test_load_defaults_to_cglabs_when_present(monkeypatch, tmp_path):
    land, proc = _point_cglabs_at(monkeypatch, tmp_path, exists=True)
    monkeypatch.delenv("AGWISE_DATA_ROOT", raising=False)
    monkeypatch.delenv("AGWISE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("AGWISE_DATA_CONFIG", str(tmp_path / "none.yaml"))
    cfg = Config.load()
    assert str(cfg.root) == str(proc)
    assert str(cfg.local_root) == str(land)


# ---------------------------------------------------------------------------
# Rainfall source preference: CGLabs prefers local CHIRPS v3 for PRCP.


def test_rainfall_source_prefers_chirps_v3_when_staged(tmp_path):
    land = tmp_path / "Landing"
    (land / "Rainfall" / "chirps_v3").mkdir(parents=True)
    cfg = Config(root=tmp_path / "cache", local_root=land)
    assert cfg.rainfall_source == "chirps_v3"


def test_rainfall_source_none_when_v3_absent(tmp_path):
    land = tmp_path / "Landing"
    land.mkdir()  # no Rainfall/chirps_v3 subtree
    cfg = Config(root=tmp_path / "cache", local_root=land)
    assert cfg.rainfall_source is None


def test_rainfall_source_none_off_cglabs(tmp_path):
    cfg = Config(root=tmp_path / "cache")           # no local_root at all
    assert cfg.rainfall_source is None


def test_rainfall_source_explicit_arg_wins(tmp_path):
    land = tmp_path / "Landing"
    (land / "Rainfall" / "chirps_v3").mkdir(parents=True)
    cfg = Config(root=tmp_path / "c", local_root=land, rainfall_source="chirps")
    assert cfg.rainfall_source == "chirps"          # forced back to v2


def test_effective_source_applies_rainfall_preference():
    from agwise_data.api import _effective_source
    from agwise_data.config import Config

    cfg = Config(root="/tmp/x")
    cfg.rainfall_source = "chirps_v3"
    # PRCP, no explicit source, years covered by v3 (1981-2023) -> chirps_v3
    assert _effective_source("PRCP", None, cfg, [2023]) == "chirps_v3"
    # a year outside v3's coverage -> fall back to the catalog default (None)
    assert _effective_source("PRCP", None, cfg, [2024]) is None
    # unknown years -> can't promise coverage -> default
    assert _effective_source("PRCP", None, cfg, None) is None
    # an explicit source always wins
    assert _effective_source("PRCP", "chirps", cfg, [2023]) == "chirps"
    # non-rainfall variables are untouched by the preference
    assert _effective_source("TMAX", None, cfg, [2023]) is None


def test_effective_cpu_is_positive():
    from agwise_data.config import effective_cpu

    n = effective_cpu()
    assert isinstance(n, int) and n >= 1


def test_read_workers_default_and_override(monkeypatch):
    from agwise_data.config import Config, ENV_READ_WORKERS, effective_cpu

    # default: derived from the effective (cgroup) CPU count, capped at 8
    c = Config()
    assert c.read_workers == min(effective_cpu(), 8)
    assert 1 <= c.read_workers <= 8
    # an explicit constructor value always wins
    assert Config(read_workers=2).read_workers == 2
    assert Config(read_workers=1).read_workers == 1
    # env override flows through load()
    monkeypatch.setenv(ENV_READ_WORKERS, "3")
    assert Config.load().read_workers == 3
