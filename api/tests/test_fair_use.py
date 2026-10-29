"""Fair-use tiers: anonymous, free, and institution keys.

These tests mount the middleware on a stub app so they do not need the
catalogue database.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fair_use import (  # noqa: E402
    ANONYMOUS,
    FREE,
    INSTITUTION,
    FairUseGate,
    KeyStore,
    attach,
    hash_key,
)


def gate(tmp_path, **kwargs) -> FairUseGate:
    defaults = dict(
        anon_per_minute=60,
        anon_per_day=5,
        free_per_minute=60,
        free_per_day=8,
        institution_per_minute=60,
        institution_per_day=100000,
        invalid_per_minute=3,
        api_access_url="https://publicuniverse.net/api-access",
        clock=lambda: 1_700_000_000,
    )
    defaults.update(kwargs)
    return FairUseGate(KeyStore(str(tmp_path / "keys.sqlite")), **defaults)


def client_for(fair: FairUseGate, peer: str = "203.0.113.10") -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/stats")
    def stats():
        return {"ok": True}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    attach(app, fair)
    return TestClient(app, client=(peer, 50000))


def test_anonymous_limit_returns_429_with_retry_after_and_access_link(tmp_path):
    fair = gate(tmp_path, anon_per_day=2)
    client = client_for(fair)
    assert client.get("/api/v1/stats").status_code == 200
    assert client.get("/api/v1/stats").headers["X-PU-Key-Tier"] == ANONYMOUS
    blocked = client.get("/api/v1/stats")
    assert blocked.status_code == 429
    assert blocked.headers["X-PU-Key-Tier"] == ANONYMOUS
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.json()["api_access_url"] == "https://publicuniverse.net/api-access"
    assert "/api-access" in blocked.headers["Link"]
    assert "invalid" not in blocked.json()["detail"].lower()


def test_missing_invalid_and_revoked_keys_share_the_anonymous_ip_budget(tmp_path):
    fair = gate(tmp_path, anon_per_day=3, invalid_per_minute=10)
    _key_id, raw = fair.issue(FREE, "lab")
    fair.store.revoke(_key_id)
    client = client_for(fair, "203.0.113.20")

    missing = client.get("/api/v1/stats")
    blank = client.get("/api/v1/stats", headers={"X-PU-Api-Key": "   "})
    invalid = client.get("/api/v1/stats", headers={"X-PU-Api-Key": "pu_not-a-real-key"})
    revoked = client.get("/api/v1/stats", headers={"X-PU-Api-Key": raw})
    assert [missing.status_code, blank.status_code, invalid.status_code] == [200, 200, 200]
    assert revoked.status_code == 429
    for response in (missing, blank, invalid, revoked):
        assert response.headers["X-PU-Key-Tier"] == ANONYMOUS


def test_invalid_key_attempts_are_limited_per_ip_before_the_daily_budget(tmp_path):
    fair = gate(tmp_path, anon_per_day=100, invalid_per_minute=2)
    client = client_for(fair, "203.0.113.30")
    headers = {"X-PU-Api-Key": "pu_wrong"}
    assert client.get("/api/v1/stats", headers=headers).status_code == 200
    assert client.get("/api/v1/stats", headers=headers).status_code == 200
    blocked = client.get("/api/v1/stats", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["X-PU-Key-Tier"] == ANONYMOUS
    # A request with no key still uses the anonymous budget, which is not exhausted.
    assert client.get("/api/v1/stats").status_code == 200


def test_valid_free_key_has_its_own_budget_and_tier_header(tmp_path):
    fair = gate(tmp_path, anon_per_day=1, free_per_day=3)
    _key_id, raw = fair.issue(FREE, "classroom")
    client = client_for(fair, "203.0.113.40")
    headers = {"X-PU-Api-Key": raw}
    for _ in range(3):
        response = client.get("/api/v1/stats", headers=headers)
        assert response.status_code == 200
        assert response.headers["X-PU-Key-Tier"] == FREE
    blocked = client.get("/api/v1/stats", headers=headers)
    assert blocked.status_code == 429
    assert blocked.headers["X-PU-Key-Tier"] == FREE
    assert blocked.json()["api_access_url"].endswith("/api-access")
    # The key's budget does not consume the caller's anonymous allowance.
    anonymous = client.get("/api/v1/stats")
    assert anonymous.status_code == 200
    assert anonymous.headers["X-PU-Key-Tier"] == ANONYMOUS


def test_institution_limit_comes_from_configuration(tmp_path):
    fair = gate(tmp_path, institution_per_day=2)
    _key_id, raw = fair.issue(INSTITUTION, "university")
    client = client_for(fair, "203.0.113.50")
    headers = {"X-PU-Api-Key": raw}
    assert client.get("/api/v1/stats", headers=headers).headers["X-PU-Key-Tier"] == INSTITUTION
    assert client.get("/api/v1/stats", headers=headers).status_code == 200
    assert client.get("/api/v1/stats", headers=headers).status_code == 429


def test_institution_default_is_100000_calls_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("PU_API_KEYS_PATH", str(tmp_path / "keys.sqlite"))
    monkeypatch.delenv("PU_INSTITUTION_PER_DAY", raising=False)
    monkeypatch.delenv("PU_API_KEYS", raising=False)
    built = FairUseGate.from_environ()
    assert built.limits[INSTITUTION] == (5000, 100000)


def test_query_string_does_not_authenticate(tmp_path):
    fair = gate(tmp_path, anon_per_day=2)
    _key_id, raw = fair.issue(FREE, "lab")
    client = client_for(fair, "203.0.113.60")
    response = client.get("/api/v1/stats", params={"api_key": raw, "X-PU-Api-Key": raw})
    assert response.status_code == 200
    assert response.headers["X-PU-Key-Tier"] == ANONYMOUS


def test_separate_ips_do_not_share_an_anonymous_budget(tmp_path):
    fair = gate(tmp_path, anon_per_day=1)
    first = client_for(fair, "203.0.113.70")
    second = client_for(fair, "203.0.113.71")
    assert first.get("/api/v1/stats").status_code == 200
    assert first.get("/api/v1/stats").status_code == 429
    assert second.get("/api/v1/stats").status_code == 200


def test_keys_are_hashed_at_rest_and_health_is_not_limited(tmp_path):
    fair = gate(tmp_path, anon_per_day=1)
    _key_id, raw = fair.issue(FREE, "secret lab")
    assert not fair.store.contains_text(raw)
    stored = hash_key(raw, "")
    assert fair.store.contains_text(stored)
    client = client_for(fair, "203.0.113.80")
    assert client.get("/api/v1/stats").status_code == 200
    assert client.get("/api/v1/stats").status_code == 429
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
        assert "X-PU-Key-Tier" not in client.get("/healthz").headers


def test_direct_loopback_without_a_key_is_the_colocated_site(tmp_path):
    fair = gate(tmp_path, anon_per_day=1)
    client = client_for(fair, "127.0.0.1")
    for _ in range(4):
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        assert response.headers["X-PU-Key-Tier"] == ANONYMOUS


def test_forwarded_client_ip_is_limited_and_untrusted_peers_cannot_spoof(tmp_path):
    fair = gate(tmp_path, anon_per_day=1)
    proxied = client_for(fair, "127.0.0.1")
    headers = {"X-Real-IP": "198.51.100.8"}
    assert proxied.get("/api/v1/stats", headers=headers).status_code == 200
    assert proxied.get("/api/v1/stats", headers=headers).status_code == 429

    spoofed = client_for(fair, "198.51.100.9")
    stolen = spoofed.get("/api/v1/stats", headers={"X-Real-IP": "203.0.113.1", "CF-Connecting-IP": "192.0.2.9"})
    assert stolen.status_code == 200
    again = spoofed.get("/api/v1/stats", headers={"X-Real-IP": "203.0.113.99"})
    assert again.status_code == 429


def test_cloudflare_connecting_ip_wins_from_a_trusted_proxy(tmp_path):
    fair = gate(tmp_path, anon_per_day=1)
    client = client_for(fair, "127.0.0.1")
    headers = {"CF-Connecting-IP": "192.0.2.20", "X-Real-IP": "203.0.113.5"}
    assert client.get("/api/v1/stats", headers=headers).status_code == 200
    other = client.get("/api/v1/stats", headers={"X-Real-IP": "203.0.113.5"})
    assert other.status_code == 200
    assert client.get("/api/v1/stats", headers=headers).status_code == 429


def test_bootstrap_hashes_env_keys_and_ignores_a_bad_payload(tmp_path, monkeypatch, capsys):
    path = tmp_path / "keys.sqlite"
    monkeypatch.setenv("PU_API_KEYS_PATH", str(path))
    monkeypatch.setenv("PU_API_KEY_PEPPER", "pepper")
    monkeypatch.setenv(
        "PU_API_KEYS",
        '[{"key":"pu_bootstrap_secret_value","tier":"institution","label":"env","id":"boot1"}]',
    )
    built = FairUseGate.from_environ()
    decision = built.evaluate("203.0.113.4", {"x-pu-api-key": "pu_bootstrap_secret_value"})
    assert decision.allowed and decision.tier == INSTITUTION
    assert not built.store.contains_text("pu_bootstrap_secret_value")

    monkeypatch.setenv("PU_API_KEYS", "not-json")
    FairUseGate.from_environ()
    assert "ignoring PU_API_KEYS" in capsys.readouterr().err


def test_minute_window_sets_retry_after_until_the_next_minute(tmp_path):
    # 1_700_000_010 is 30 seconds into its UTC minute, so Retry-After is 30.
    fair = gate(tmp_path, anon_per_minute=1, anon_per_day=100, clock=lambda: 1_700_000_010)
    client = client_for(fair, "203.0.113.90")
    assert client.get("/api/v1/stats").status_code == 200
    blocked = client.get("/api/v1/stats")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "30"
