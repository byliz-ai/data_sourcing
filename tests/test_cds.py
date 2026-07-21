"""Resilient CDS retrieval: retry/backoff, partial cleanup, exhaustion."""

import pytest

from agwise_data import cds


class _FakeClient:
    """A cdsapi-like client that fails the first ``fail`` retrieves.

    On success it writes a stub file to the target (as the real client does);
    on failure it writes a *partial* file then raises, so the retry logic's
    "delete the partial between attempts" behaviour is exercised.
    """

    def __init__(self, fail: int):
        self.fail = fail
        self.calls = 0

    def retrieve(self, dataset, request, target):
        self.calls += 1
        with open(target, "w") as fh:
            fh.write("partial")
        if self.calls <= self.fail:
            raise ConnectionError("network dropped mid-download")
        with open(target, "w") as fh:
            fh.write("complete")


def test_retrieve_succeeds_first_try(tmp_path):
    c = _FakeClient(fail=0)
    out = cds.retrieve("ds", {}, tmp_path / "x.nc", client=c, base_delay=0)
    assert out.read_text() == "complete" and c.calls == 1


def test_retrieve_retries_then_succeeds(tmp_path):
    c = _FakeClient(fail=2)  # fail twice, succeed on the third
    out = cds.retrieve("ds", {}, tmp_path / "x.nc", client=c, attempts=3, base_delay=0)
    assert out.read_text() == "complete" and c.calls == 3


def test_retrieve_raises_after_exhausting_attempts(tmp_path):
    c = _FakeClient(fail=5)
    target = tmp_path / "x.nc"
    with pytest.raises(RuntimeError, match="failed after 3"):
        cds.retrieve("ds", {}, target, client=c, attempts=3, base_delay=0)
    assert c.calls == 3
    assert not target.exists()  # no partial file left behind


def test_config_exposes_cds_retries():
    from agwise_data.config import Config

    assert Config(root="/tmp/x").cds_retries == 3
    assert Config(root="/tmp/x", cds_retries=5).cds_retries == 5
