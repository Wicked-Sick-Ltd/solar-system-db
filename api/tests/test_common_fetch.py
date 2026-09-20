"""Retry policy of the network helpers: transient upstream failures must not
kill a two-hour build, the wait between attempts must grow far enough to ride
out a multi-minute outage, and a fetch must still give up in bounded time."""
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


class _Clock:
    """A clock that only moves when the code under test sleeps, so the time
    budget can be exercised without the test actually waiting."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(common.time, "monotonic", c.monotonic)
    monkeypatch.setattr(common.time, "sleep", c.sleep)
    monkeypatch.setattr(common.random, "uniform", lambda lo, hi: hi)  # deterministic: take the cap
    return c


def test_fetch_json_survives_a_run_of_502s_and_backs_off(clock, monkeypatch):
    responses = iter([_Resp(502), _Resp(502), _Resp(502), _Resp(200, {"ok": True})])
    monkeypatch.setattr(common.session, "get", lambda *a, **k: next(responses))

    assert common.fetch_json("https://x") == {"ok": True}
    assert clock.sleeps == [2.0, 4.0, 8.0]  # exponential, not the old 1,2,3


def test_default_attempts_span_a_multi_minute_outage(clock, monkeypatch):
    """The 03:00 and 04:05 UTC JPL 502s each lasted a few minutes; the whole
    attempt ladder has to outlast that, not just the first ~30 s of it."""
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(502))

    with pytest.raises(RuntimeError):
        common.fetch_json("https://x")

    assert clock.sleeps == [2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 120.0]
    assert sum(clock.sleeps) > 240  # covers a four-minute outage
    assert len(clock.sleeps) == common.FETCH_MAX_ATTEMPTS - 1  # no sleep after the last attempt


def test_time_budget_stops_retrying_before_the_attempt_limit(clock, monkeypatch):
    """A short budget wins over the attempt ladder, and the last wait is
    trimmed so no sleep runs past the deadline."""
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(502))

    with pytest.raises(RuntimeError, match=r"gave up after 5/8 attempts in 20s of 20s budget"):
        common.fetch_json("https://x", budget=20.0)

    assert clock.sleeps == [2.0, 4.0, 8.0, 6.0]  # 16 s trimmed to the 6 s left
    assert sum(clock.sleeps) == 20.0


def test_fetch_bytes_shares_the_budget(clock, monkeypatch):
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(503))

    with pytest.raises(RuntimeError, match=r"fetch_bytes gave up after 3/3 attempts"):
        common.fetch_bytes("https://x", retries=3)

    assert clock.sleeps == [2.0, 4.0]


def test_a_final_429_does_not_sleep_before_giving_up(clock, monkeypatch):
    """Rate-limit responses back off on their own longer base, but the wait
    after the last attempt is dead time — the helper is about to raise."""
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(429))

    with pytest.raises(RuntimeError, match=r"gave up after 4/4 attempts .*last status 429"):
        common.fetch_json("https://x", retries=4)

    assert clock.sleeps == [4.0, 8.0, 16.0]  # 429 base, and nothing after attempt 4


def test_fetch_json_gives_up_with_the_last_status_after_all_attempts(clock, monkeypatch):
    monkeypatch.setattr(common.session, "get", lambda *a, **k: _Resp(502))
    with pytest.raises(RuntimeError, match=r"gave up after 8/8 attempts .*\(last status 502\)"):
        common.fetch_json("https://x")


def test_backoff_is_capped_and_never_negative():
    assert 0 <= common.backoff_seconds(20) <= 120.0
    assert 0 <= common.backoff_seconds(0) <= 2.0


def test_retry_budget_refuses_to_plan_past_its_limits(clock):
    budget = common.RetryBudget(max_attempts=3, seconds=60.0)
    assert budget.next_delay(0) == 2.0
    assert budget.next_delay(2) is None  # attempt limit reached
    clock.sleep(60.0)
    assert budget.next_delay(0) is None  # time budget spent
