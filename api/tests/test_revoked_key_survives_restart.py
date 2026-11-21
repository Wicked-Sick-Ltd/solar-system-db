"""Revoked bootstrap keys must stay revoked across an API process start.

Operators install keys by setting PU_API_KEYS. The API hashes that list into
the key database on startup (`FairUseGate.from_environ`, which `api/main.py`
calls while building the app). Revocation is `python api/fair_use.py revoke`.
The next process start, including the one `scripts/pull_latest.sh` triggers
after installing a catalogue artefact, reads the same environment.

These tests fail while startup treats that bootstrap as a fresh issue and
clears `revoked_at`. They pass once an existing revoked row stays revoked.
"""
from __future__ import annotations

import json
import subprocess
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
    attach,
    hash_key,
    main,
)

# Obvious placeholders. These are not credentials.
PEPPER = "placeholder-pepper"
INSTITUTION_KEY = "placeholder-institution-key"
FREE_KEY = "placeholder-free-key"
ACTIVE_KEY = "placeholder-active-key"
FRESH_KEY = "placeholder-fresh-key"
FROZEN_CLOCK = 1_700_000_000

# A second interpreter, as a service restart is: import the fair-use module
# and run the same startup function the API process runs. The placeholder
# keys stay in the environment; this process must not print them.
RESTART_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from fair_use import FairUseGate
FairUseGate.from_environ().store.close()
"""


def _client(fair: FairUseGate, peer: str) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/stats")
    def stats():
        return {"ok": True}

    attach(app, fair)
    return TestClient(app, client=(peer, 50000))


def _freeze(fair: FairUseGate) -> FairUseGate:
    fair._clock = lambda: FROZEN_CLOCK
    return fair


def _limits(monkeypatch, db: Path) -> None:
    monkeypatch.setenv("PU_API_KEYS_PATH", str(db))
    monkeypatch.setenv("PU_API_KEY_PEPPER", PEPPER)
    monkeypatch.setenv("PU_ANON_PER_MINUTE", "1")
    monkeypatch.setenv("PU_ANON_PER_DAY", "1000")
    monkeypatch.setenv("PU_FREE_PER_MINUTE", "300")
    monkeypatch.setenv("PU_FREE_PER_DAY", "10000")
    monkeypatch.setenv("PU_INSTITUTION_PER_MINUTE", "5000")
    monkeypatch.setenv("PU_INSTITUTION_PER_DAY", "100000")
    monkeypatch.setenv("PU_INVALID_KEY_PER_MINUTE", "100")


def _assert_anonymous_budget(fair: FairUseGate, peer: str, raw: str) -> None:
    """A revoked key spends the per-IP anonymous budget, not its old tier."""
    client = _client(fair, peer)
    headers = {"X-PU-Api-Key": raw}
    first = client.get("/api/v1/stats", headers=headers)
    second = client.get("/api/v1/stats", headers=headers)
    assert first.status_code == 200
    assert first.headers["X-PU-Key-Tier"] == ANONYMOUS
    assert second.status_code == 429
    assert second.headers["X-PU-Key-Tier"] == ANONYMOUS


def _assert_tier_budget(fair: FairUseGate, peer: str, raw: str, tier: str) -> None:
    client = _client(fair, peer)
    headers = {"X-PU-Api-Key": raw}
    first = client.get("/api/v1/stats", headers=headers)
    second = client.get("/api/v1/stats", headers=headers)
    assert first.status_code == 200, first.text
    assert first.headers["X-PU-Key-Tier"] == tier
    assert second.status_code == 200, second.text
    assert second.headers["X-PU-Key-Tier"] == tier


def test_process_restart_leaves_revoked_bootstrap_keys_revoked(tmp_path, monkeypatch):
    """Bootstrap, revoke, then start again the way the API process does.

    The restart payload repeats the revoked secrets. One of them is presented
    again under a different id and label, because the upsert matches the hash
    and not the id. A key that was not revoked stays on its tier, and a secret
    that was not stored yet is inserted.
    """
    db = tmp_path / "api_keys.sqlite"
    _limits(monkeypatch, db)
    bootstrap = [
        {"key": INSTITUTION_KEY, "tier": INSTITUTION, "label": "bulk", "id": "institution-key"},
        {"key": FREE_KEY, "tier": FREE, "label": "classroom", "id": "free-key"},
        {"key": ACTIVE_KEY, "tier": INSTITUTION, "label": "still-on", "id": "active-key"},
    ]
    monkeypatch.setenv("PU_API_KEYS", json.dumps(bootstrap))

    started = _freeze(FairUseGate.from_environ())
    first = started.evaluate("203.0.113.10", {"x-pu-api-key": INSTITUTION_KEY})
    assert first.allowed and first.tier == INSTITUTION
    assert not started.store.contains_text(INSTITUTION_KEY)
    started.store.close()

    assert main(["--db", str(db), "revoke", "--id", "institution-key"]) == 0
    assert main(["--db", str(db), "revoke", "--id", "free-key"]) == 0

    monkeypatch.delenv("PU_API_KEYS")
    revoked = _freeze(FairUseGate.from_environ())
    _assert_anonymous_budget(revoked, "203.0.113.21", INSTITUTION_KEY)
    _assert_anonymous_budget(revoked, "203.0.113.22", FREE_KEY)
    _assert_tier_budget(revoked, "203.0.113.23", ACTIVE_KEY, INSTITUTION)
    revoked.store.close()

    # Same environment a recycled API process still has, plus one new key.
    # The institution secret is sent under a new id and label; the stored row
    # is still the original id, because the conflict target is the hash.
    monkeypatch.setenv(
        "PU_API_KEYS",
        json.dumps([
            {
                "key": INSTITUTION_KEY,
                "tier": INSTITUTION,
                "label": "bulk-again",
                "id": "institution-key-reissued",
            },
            {"key": FREE_KEY, "tier": FREE, "label": "classroom", "id": "free-key"},
            {"key": ACTIVE_KEY, "tier": INSTITUTION, "label": "still-on", "id": "active-key"},
            {"key": FRESH_KEY, "tier": FREE, "label": "new", "id": "fresh-key"},
        ]),
    )
    api_dir = str(Path(__file__).resolve().parents[1])
    restarted = subprocess.run(
        [sys.executable, "-c", RESTART_SCRIPT, api_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr

    monkeypatch.delenv("PU_API_KEYS")
    serving = _freeze(FairUseGate.from_environ())
    _assert_anonymous_budget(serving, "203.0.113.31", INSTITUTION_KEY)
    _assert_anonymous_budget(serving, "203.0.113.32", FREE_KEY)
    _assert_tier_budget(serving, "203.0.113.41", ACTIVE_KEY, INSTITUTION)
    _assert_tier_budget(serving, "203.0.113.42", FRESH_KEY, FREE)

    institution = serving.store.find(hash_key(INSTITUTION_KEY, PEPPER))
    assert institution == ("institution-key", INSTITUTION, institution[2])
    assert institution[2] is not None
    free = serving.store.find(hash_key(FREE_KEY, PEPPER))
    assert free == ("free-key", FREE, free[2])
    assert free[2] is not None
    assert serving.store.find(hash_key(FRESH_KEY, PEPPER))[0] == "fresh-key"
    serving.store.close()


def test_identical_bootstrap_payload_does_not_clear_revoked_at(tmp_path, monkeypatch):
    """The restart that a catalogue pull causes resends the original list."""
    db = tmp_path / "api_keys.sqlite"
    _limits(monkeypatch, db)
    payload = json.dumps([
        {"key": INSTITUTION_KEY, "tier": INSTITUTION, "label": "bulk", "id": "institution-key"},
    ])
    monkeypatch.setenv("PU_API_KEYS", payload)
    started = FairUseGate.from_environ()
    started.store.close()

    assert main(["--db", str(db), "revoke", "--id", "institution-key"]) == 0

    api_dir = str(Path(__file__).resolve().parents[1])
    restarted = subprocess.run(
        [sys.executable, "-c", RESTART_SCRIPT, api_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr

    monkeypatch.delenv("PU_API_KEYS")
    serving = _freeze(FairUseGate.from_environ())
    _assert_anonymous_budget(serving, "203.0.113.51", INSTITUTION_KEY)
    found = serving.store.find(hash_key(INSTITUTION_KEY, PEPPER))
    assert found is not None and found[2] is not None
    serving.store.close()
