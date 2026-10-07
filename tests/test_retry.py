"""Tests for the transient-error retry helper (no network)."""

import pytest
import requests

from agwise_data.retry import TransientError, is_transient, retry_call


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=resp)


class EEException(Exception):
    """Stand-in with Earth Engine's exception class name."""


@pytest.mark.parametrize("exc, expected", [
    (requests.ConnectionError("reset"), True),
    (requests.Timeout("slow"), True),
    (TransientError("size mismatch"), True),
    (_http_error(503), True),
    (_http_error(429), True),
    (_http_error(404), False),
    (_http_error(403), False),
    (EEException("Too many concurrent aggregations."), True),
    (EEException("User memory limit exceeded."), False),
    (ValueError("bad input"), False),
    (FileNotFoundError("not published"), False),
])
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_retry_call_recovers_from_transient_failures():
    calls, sleeps = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("reset")
        return "ok"

    out = retry_call(flaky, what="t", attempts=4, base_delay=1.0, jitter=0,
                     sleep=sleeps.append)
    assert out == "ok" and len(calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff


def test_retry_call_does_not_retry_permanent_errors():
    calls = []

    def missing():
        calls.append(1)
        raise _http_error(404)

    with pytest.raises(requests.HTTPError):
        retry_call(missing, what="t", sleep=lambda s: None)
    assert len(calls) == 1


def test_retry_call_reraises_last_transient_error_when_exhausted():
    calls = []

    def down():
        calls.append(1)
        raise _http_error(503)

    with pytest.raises(requests.HTTPError):
        retry_call(down, what="t", attempts=3, sleep=lambda s: None)
    assert len(calls) == 3


def test_retry_call_delay_is_capped():
    sleeps = []

    def down():
        raise requests.Timeout("slow")

    with pytest.raises(requests.Timeout):
        retry_call(down, what="t", attempts=5, base_delay=10, max_delay=15,
                   jitter=0, sleep=sleeps.append)
    assert sleeps == [10, 15, 15, 15]
