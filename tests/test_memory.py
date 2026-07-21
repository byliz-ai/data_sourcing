"""Memory-budget detection and peak estimation (deterministic, no host deps)."""

from agwise_data import memory as m

GIB = 1024 ** 3


def test_env_limit_override_wins(monkeypatch):
    monkeypatch.setenv("AGWISE_MEM_LIMIT_GB", "16")
    assert m.detect_limit_bytes() == 16 * GIB


def test_detect_reads_cgroup_v2(monkeypatch, tmp_path):
    monkeypatch.delenv("AGWISE_MEM_LIMIT_GB", raising=False)
    v2 = tmp_path / "memory.max"
    v2.write_text("34359738368\n")
    monkeypatch.setattr(m, "_CGROUP_V2", v2)
    monkeypatch.setattr(m, "_CGROUP_V1", tmp_path / "nope")
    assert m.detect_limit_bytes() == 34359738368


def test_detect_v2_max_means_unlimited(monkeypatch, tmp_path):
    monkeypatch.delenv("AGWISE_MEM_LIMIT_GB", raising=False)
    v2 = tmp_path / "memory.max"
    v2.write_text("max\n")
    monkeypatch.setattr(m, "_CGROUP_V2", v2)
    monkeypatch.setattr(m, "_CGROUP_V1", tmp_path / "nope")
    assert m.detect_limit_bytes() is None


def test_detect_v1_sentinel_means_unlimited(monkeypatch, tmp_path):
    monkeypatch.delenv("AGWISE_MEM_LIMIT_GB", raising=False)
    v1 = tmp_path / "memory.limit_in_bytes"
    v1.write_text("9223372036854771712\n")  # v1 "no limit" sentinel
    monkeypatch.setattr(m, "_CGROUP_V2", tmp_path / "nope")
    monkeypatch.setattr(m, "_CGROUP_V1", v1)
    assert m.detect_limit_bytes() is None


def test_usable_budget_subtracts_headroom(monkeypatch):
    monkeypatch.delenv("AGWISE_MEM_HEADROOM_GB", raising=False)
    assert m.usable_budget_bytes(limit=32 * GIB) == int(32 * GIB - 8 * GIB)
    monkeypatch.setenv("AGWISE_MEM_HEADROOM_GB", "4")
    assert m.usable_budget_bytes(limit=32 * GIB) == int(32 * GIB - 4 * GIB)


def test_usable_budget_unlimited_is_none(monkeypatch):
    # no cgroup cap detected -> no budget (off-container: keep static defaults)
    monkeypatch.setattr(m, "detect_limit_bytes", lambda: None)
    assert m.usable_budget_bytes(limit=None) is None


def test_usable_budget_never_below_1gib():
    # tiny limit + big headroom must not go negative
    assert m.usable_budget_bytes(limit=2 * GIB, headroom_gb=8) == GIB


def test_estimate_peak_scales_with_shape():
    one = m.estimate_peak_bytes(100, 100, 1, 1, itemsize=4, transient_factor=1.0)
    assert one == 100 * 100 * 4
    # members and time multiply; transient factor scales
    assert m.estimate_peak_bytes(100, 100, 10, 5, 4, 3.0) == 100 * 100 * 10 * 5 * 4 * 3


def test_grid_pixels():
    assert m.grid_pixels([30.0, -5.0, 32.0, -3.0], 0.5) == (4, 4)
    # sub-cell bbox still yields at least 1x1
    assert m.grid_pixels([30.0, -5.0, 30.1, -4.9], 1.0) == (1, 1)


def test_derive_max_workers_only_reduces():
    G = GIB
    assert m.derive_max_workers(int(25.8 * G), 40, 4) == 4     # big budget -> baseline
    assert m.derive_max_workers(1 * G, 40, 4) == 1             # tiny budget -> 1
    assert m.derive_max_workers(None, 40, 4) == 4              # unlimited -> baseline
    assert m.derive_max_workers(int(25.8 * G), 2, 4) == 2      # capped by cpu


class _CapLogger:
    def __init__(self): self.msgs = []
    def warning(self, fmt, *args): self.msgs.append(fmt % args if args else fmt)


def test_warn_if_over_budget_fires_only_when_over():
    G = GIB
    lg = _CapLogger()
    # 1000x1000 x 365 float32 x3 transient ~ 4.4 GB > 2 GB budget -> warn
    m.warn_if_over_budget(2 * G, {"lat": 1000, "lon": 1000, "time": 365}, 4, lg, "x")
    assert lg.msgs and "exceeds" in lg.msgs[0]
    lg2 = _CapLogger()
    # small cube under budget -> silent
    m.warn_if_over_budget(2 * G, {"lat": 20, "lon": 20, "time": 90}, 4, lg2, "x")
    assert lg2.msgs == []
    lg3 = _CapLogger()
    m.warn_if_over_budget(None, {"lat": 9999, "lon": 9999, "time": 999}, 4, lg3, "x")
    assert lg3.msgs == []  # no budget -> never warns
