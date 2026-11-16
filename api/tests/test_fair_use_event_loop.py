"""Fair-use must not commit usage on the API event loop, including on 429s.

These tests fail while the vulnerability is present. A rejection has to be
decided before any SQLite write, an allowed counter update has to run off the
server event loop, and the compose Caddyfile has to rate-limit ``/api/``
whether or not the request carries ``X-PU-Api-Key``.

``KeyStore.find`` is still a synchronous read on the invalid-key path. These
tests do not require that lookup to leave the event loop. ``X-Real-IP`` and
``X-Forwarded-For`` resolve an identity and then use the same bump as the
cases below; only ``CF-Connecting-IP`` and an untrusted peer are exercised.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
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

# Obvious placeholder. Not a credential.
PLACEHOLDER_KEY = "pu_not-a-real-key"
KEY_ID = "placeholder-key"
CLOCK = 1_700_000_000
ATTACKER = "198.51.100.10"
VICTIM = "198.51.100.20"
FORWARDED = "203.0.113.10"
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RejectCase:
    name: str
    tier: str
    limits: dict
    key: str | None
    peer: tuple[str, int]
    headers: dict[str, str]
    identity: str
    prefixes: tuple[str, ...]


def _prefixes(kind: str, identity: str) -> tuple[str, ...]:
    if kind == "key":
        subject = f"key:{KEY_ID}"
        return (f"m:{subject}:", f"d:{subject}:")
    if kind == "invalid":
        return (f"invalid:{identity}:", f"m:ip:{identity}:", f"d:ip:{identity}:")
    return (f"m:ip:{identity}:", f"d:ip:{identity}:")


def _cases() -> list[RejectCase]:
    high = dict(anon_per_minute=1000, anon_per_day=1000, invalid_per_minute=1000,
                free_per_minute=1000, free_per_day=1000,
                institution_per_minute=1000, institution_per_day=1000)
    anon_peer = (ATTACKER, 50000)
    cases = [
        RejectCase("anonymous_minute", ANONYMOUS, {**high, "anon_per_minute": 1}, None,
                   anon_peer, {}, ATTACKER, _prefixes("anon", ATTACKER)),
        RejectCase("anonymous_day", ANONYMOUS, {**high, "anon_per_day": 1}, None,
                   anon_peer, {}, ATTACKER, _prefixes("anon", ATTACKER)),
        RejectCase("anonymous_minute_via_cf_connecting_ip", ANONYMOUS, {**high, "anon_per_minute": 1},
                   None, ("127.0.0.1", 50000), {"CF-Connecting-IP": FORWARDED}, FORWARDED,
                   _prefixes("anon", FORWARDED)),
        RejectCase("unknown_key", ANONYMOUS, {**high, "invalid_per_minute": 1}, "unknown",
                   anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("invalid", ATTACKER)),
        RejectCase("revoked_key", ANONYMOUS, {**high, "invalid_per_minute": 1}, "revoked",
                   anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("invalid", ATTACKER)),
        RejectCase("free_minute", FREE, {**high, "free_per_minute": 1}, "free",
                   anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("key", ATTACKER)),
        RejectCase("free_day", FREE, {**high, "free_per_day": 1}, "free",
                   anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("key", ATTACKER)),
        RejectCase("institution_minute", INSTITUTION, {**high, "institution_per_minute": 1},
                   "institution", anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("key", ATTACKER)),
        RejectCase("institution_day", INSTITUTION, {**high, "institution_per_day": 1},
                   "institution", anon_peer, {"X-PU-Api-Key": PLACEHOLDER_KEY}, ATTACKER,
                   _prefixes("key", ATTACKER)),
    ]
    return cases


def _gate(tmp_path, case: RejectCase) -> FairUseGate:
    gate = FairUseGate(
        KeyStore(str(tmp_path / "keys.sqlite")),
        clock=lambda: CLOCK,
        **case.limits,
    )
    if case.key in ("free", "revoked"):
        gate.store.add(KEY_ID, hash_key(PLACEHOLDER_KEY, gate.pepper), FREE, "placeholder")
    elif case.key == "institution":
        gate.store.add(KEY_ID, hash_key(PLACEHOLDER_KEY, gate.pepper), INSTITUTION, "placeholder")
    if case.key == "revoked":
        assert gate.store.revoke(KEY_ID)
    return gate


def _app(gate: FairUseGate) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/stats")
    async def stats():
        return {"ok": True, "loop": threading.get_ident()}

    @app.get("/api/v1/objects")
    async def objects():
        return {"results": [], "loop": threading.get_ident()}

    attach(app, gate)
    return app


def _rows(store: KeyStore) -> list[tuple]:
    with store._lock:
        return list(store._conn.execute(
            "SELECT bucket, count FROM usage_buckets ORDER BY bucket"
        ).fetchall())


def _matching(rows: list[tuple], prefixes: tuple[str, ...]) -> list[tuple]:
    return [row for row in rows if any(row[0].startswith(prefix) for prefix in prefixes)]


def _arm_hold(store: KeyStore, prefixes: tuple[str, ...], entered: threading.Event,
              release: threading.Event) -> dict[str, bool]:
    armed = {"on": False}
    original = store.bump

    def bump(bucket: str, expires_at: int, now: int) -> int:
        if armed["on"] and any(bucket.startswith(prefix) for prefix in prefixes):
            entered.set()
            release.wait(timeout=5)
        return original(bucket, expires_at, now)

    store.bump = bump
    return armed


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, int]:
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="fair-use-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not server.started:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("uvicorn did not start")
    return server, thread, port


def _stop_server(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)


def _forwarded(identity: str) -> dict[str, str]:
    return {"CF-Connecting-IP": identity}


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.name)
def test_over_budget_rejection_does_not_commit(tmp_path, case: RejectCase):
    """The request that returns 429 must not write a usage row."""
    gate = _gate(tmp_path, case)
    calls = {"n": 0}
    original = gate.store.bump

    def bump(bucket: str, expires_at: int, now: int) -> int:
        calls["n"] += 1
        return original(bucket, expires_at, now)

    gate.store.bump = bump
    with TestClient(_app(gate), client=case.peer) as client:
        allowed = client.get("/api/v1/stats", headers=case.headers)
        assert allowed.status_code == 200
        assert allowed.headers["X-PU-Key-Tier"] == case.tier
        before = _rows(gate.store)
        assert all(any(bucket.startswith(prefix) for bucket, _count in before) for prefix in case.prefixes), before
        calls["n"] = 0
        rejected = client.get("/api/v1/stats", headers=case.headers)
    assert rejected.status_code == 429
    assert rejected.headers["X-PU-Key-Tier"] == case.tier
    assert calls["n"] == 0
    assert _rows(gate.store) == before


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.name)
def test_over_budget_rejection_does_not_block_the_worker(tmp_path, case: RejectCase):
    """A 429 and a peer catalogue request both finish while a usage bump would block.

    Requests reach a real one-worker uvicorn loop. The peer is a different
    client, so this only stays fast when the rejection does not enter bump.
    """
    gate = _gate(tmp_path, case)
    entered = threading.Event()
    release = threading.Event()
    armed = _arm_hold(gate.store, case.prefixes, entered, release)
    server, thread, port = _start_server(_app(gate))
    attacker_headers = {**case.headers, **_forwarded(case.identity)}
    victim_headers = _forwarded(VICTIM)
    attacker_box: dict = {}
    worker: threading.Thread | None = None

    def attack():
        try:
            with httpx.Client(timeout=2) as client:
                attacker_box["response"] = client.get(
                    f"http://127.0.0.1:{port}/api/v1/stats", headers=attacker_headers
                )
        except Exception as exc:  # pragma: no cover - failure path
            attacker_box["error"] = exc

    try:
        with httpx.Client(timeout=2) as client:
            allowed = client.get(f"http://127.0.0.1:{port}/api/v1/stats", headers=attacker_headers)
        assert allowed.status_code == 200
        assert allowed.headers["X-PU-Key-Tier"] == case.tier
        before = _rows(gate.store)
        assert all(any(bucket.startswith(prefix) for bucket, _count in before) for prefix in case.prefixes), before
        armed["on"] = True
        worker = threading.Thread(target=attack, name="fair-use-attacker")
        worker.start()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and not entered.is_set() and "response" not in attacker_box:
            time.sleep(0.01)
        try:
            with httpx.Client(timeout=0.4) as client:
                peer = client.get(f"http://127.0.0.1:{port}/api/v1/objects", headers=victim_headers)
        except httpx.TimeoutException:
            peer = None
        if "response" not in attacker_box:
            worker.join(timeout=0.2)
        response = attacker_box.get("response")
        assert peer is not None and peer.status_code == 200, (
            "peer catalogue request did not finish while a rejecting bump was armed"
        )
        assert response is not None and response.status_code == 429, (
            "429 did not return before the armed usage bump; the worker is still in the commit"
        )
        assert response.headers["X-PU-Key-Tier"] == case.tier
        after = _rows(gate.store)
        assert _matching(after, case.prefixes) == _matching(before, case.prefixes)
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
        _stop_server(server, thread)


@pytest.mark.parametrize(
    ("key", "tier"),
    [
        ("none", ANONYMOUS),
        ("unknown", ANONYMOUS),
        ("revoked", ANONYMOUS),
        ("free", FREE),
        ("institution", INSTITUTION),
    ],
    ids=("anonymous", "unknown_key", "revoked_key", "free", "institution"),
)
def test_allowed_bump_runs_off_the_event_loop(tmp_path, key: str, tier: str):
    """A request that is allowed still updates counters, off the server thread."""
    case = RejectCase(
        name="allowed",
        tier=tier,
        limits=dict(anon_per_minute=1000, anon_per_day=1000, invalid_per_minute=1000,
                    free_per_minute=1000, free_per_day=1000,
                    institution_per_minute=1000, institution_per_day=1000),
        key=None if key == "none" else key,
        peer=(ATTACKER, 50000),
        headers={} if key == "none" else {"X-PU-Api-Key": PLACEHOLDER_KEY},
        identity=ATTACKER,
        prefixes=(),
    )
    gate = _gate(tmp_path, case)
    threads: dict[str, int] = {}
    original = gate.store.bump

    def bump(bucket: str, expires_at: int, now: int) -> int:
        threads["bump"] = threading.get_ident()
        return original(bucket, expires_at, now)

    gate.store.bump = bump
    server, thread, port = _start_server(_app(gate))
    headers = {**case.headers, **_forwarded(ATTACKER)}
    try:
        with httpx.Client(timeout=2) as client:
            response = client.get(f"http://127.0.0.1:{port}/api/v1/stats", headers=headers)
        assert response.status_code == 200
        assert response.headers["X-PU-Key-Tier"] == tier
        assert threads["bump"] != response.json()["loop"]
        assert _rows(gate.store), "an allowed request must still record usage"
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize(
    ("key", "tier", "kind"),
    [
        ("none", ANONYMOUS, "anon"),
        ("unknown", ANONYMOUS, "invalid"),
        ("revoked", ANONYMOUS, "invalid"),
        ("free", FREE, "key"),
        ("institution", INSTITUTION, "key"),
    ],
    ids=("anonymous", "unknown_key", "revoked_key", "free", "institution"),
)
def test_allowed_bump_does_not_block_a_peer_catalogue_request(tmp_path, key: str, tier: str, kind: str):
    """Holding the allowed counter update must not freeze another catalogue call."""
    case = RejectCase(
        name="peer",
        tier=tier,
        limits=dict(anon_per_minute=1000, anon_per_day=1000, invalid_per_minute=1000,
                    free_per_minute=1000, free_per_day=1000,
                    institution_per_minute=1000, institution_per_day=1000),
        key=None if key == "none" else key,
        peer=(ATTACKER, 50000),
        headers={} if key == "none" else {"X-PU-Api-Key": PLACEHOLDER_KEY},
        identity=ATTACKER,
        prefixes=_prefixes(kind, ATTACKER),
    )
    gate = _gate(tmp_path, case)
    entered = threading.Event()
    release = threading.Event()
    armed = _arm_hold(gate.store, case.prefixes, entered, release)
    armed["on"] = True
    server, thread, port = _start_server(_app(gate))
    attacker_headers = {**case.headers, **_forwarded(ATTACKER)}
    box: dict = {}

    def attack():
        try:
            with httpx.Client(timeout=2) as client:
                box["response"] = client.get(
                    f"http://127.0.0.1:{port}/api/v1/stats", headers=attacker_headers
                )
        except Exception as exc:  # pragma: no cover - failure path
            box["error"] = exc

    worker = threading.Thread(target=attack, name="fair-use-allowed")
    worker.start()
    try:
        assert entered.wait(timeout=2), "allowed request did not reach the usage bump"
        try:
            with httpx.Client(timeout=0.4) as client:
                peer = client.get(
                    f"http://127.0.0.1:{port}/api/v1/objects",
                    headers=_forwarded(VICTIM),
                )
        except httpx.TimeoutException:
            peer = None
        assert peer is not None and peer.status_code == 200, (
            "peer catalogue request waited behind an allowed usage bump on the event loop"
        )
    finally:
        release.set()
        worker.join(timeout=5)
        _stop_server(server, thread)
    response = box.get("response")
    assert response is not None and response.status_code == 200
    assert response.headers["X-PU-Key-Tier"] == tier


def _strip_caddy_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _brace_block(text: str, marker: str) -> str:
    start = text.find(marker)
    assert start != -1, marker
    opening = text.find("{", start)
    assert opening != -1
    depth = 0
    for index in range(opening, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening:index + 1]
    raise AssertionError(f"unbalanced braces after {marker}")


def test_caddy_rate_limits_api_requests_with_or_without_an_api_key_header():
    """The compose proxy must count /api/ requests that carry X-PU-Api-Key."""
    text = _strip_caddy_comments((REPO_ROOT / "Caddyfile").read_text())
    block = _brace_block(text, "handle_path /api/*")
    assert "rate_limit" in block
    assert "x-pu-api-key" not in block.lower()
