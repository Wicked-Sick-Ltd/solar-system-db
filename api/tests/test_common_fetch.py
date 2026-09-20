"""Retry policy of the network helpers: transient upstream failures must not
kill a two-hour build, and the wait between attempts must grow."""
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import common


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} Server Error")
            err.response = self
            raise err

    def json(self):
        return self._payload


def test_fetch_json_survives_a_run_of_502s_and_backs_off(monkeypatch):
    responses = iter([_Resp(502), _Resp(502), _Resp(502), _Resp(200, {"ok": True})])
    sleeps: list[float] = []
    monkeypatch.setattr(common.session, "get", lambda *a, **k: next(responses))
    monkeypatch.setattr(common.time, "sleep", sleeps.append)
    monkeypatch.setattr(common.random, "uniform", lambda lo, hi: hi)  # deterministic: take the cap

    assert common.fetch_json("https://x", retries=6) == {"ok": True}
    assert sleeps == [2.0, 4.0, 8.0]  # exponential, not the old 1,2,3


def test_fetch_json_gives_up_with_the_last_status_after_all_attempts(monkeypatch):
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(502))
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match=r"gave up after 6 attempts \(last status 502\)"):
        common.fetch_json("https://x")


def test_backoff_is_capped_and_never_negative():
    assert 0 <= common.backoff_seconds(20) <= 120.0
    assert 0 <= common.backoff_seconds(0) <= 2.0
