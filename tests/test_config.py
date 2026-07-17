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
