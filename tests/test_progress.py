"""Progress helper: gating, pass-through, and future draining."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from agwise_data import progress


def test_enabled_honours_env(monkeypatch):
    monkeypatch.setenv("AGWISE_PROGRESS", "always")
    assert progress.enabled() is True
    monkeypatch.setenv("AGWISE_PROGRESS", "never")
    assert progress.enabled() is False
    monkeypatch.setenv("AGWISE_PROGRESS", "0")
    assert progress.enabled() is False


def test_enabled_auto_is_off_without_tty(monkeypatch):
    # pytest captures stderr (not a TTY), so auto must be quiet
    monkeypatch.setenv("AGWISE_PROGRESS", "auto")
    assert progress.enabled() is False


def test_track_passthrough_disabled(monkeypatch):
    monkeypatch.setenv("AGWISE_PROGRESS", "never")
    assert list(progress.track(range(5), desc="x")) == [0, 1, 2, 3, 4]


def test_track_passthrough_enabled(monkeypatch):
    # even with the bar on, every item is yielded unchanged
    monkeypatch.setenv("AGWISE_PROGRESS", "always")
    assert list(progress.track(iter([10, 20, 30]), total=3, desc="x")) == [10, 20, 30]


def test_drain_futures_runs_all_and_propagates(monkeypatch):
    monkeypatch.setenv("AGWISE_PROGRESS", "never")
    seen = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(seen.append, i) for i in range(6)]
        progress.drain_futures(futs, desc="x")
    assert sorted(seen) == [0, 1, 2, 3, 4, 5]

    def boom():
        raise ValueError("nope")

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(boom)]
        with pytest.raises(ValueError, match="nope"):
            progress.drain_futures(futs, desc="x")
