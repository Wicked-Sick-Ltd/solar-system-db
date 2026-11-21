"""Tests for the packaged API console entry point."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Markers the request in the Sentry capture test carries. Module level on purpose:
# Sentry sends a few source lines around each stack frame, so they must not sit
# next to the route that raises.
REQUEST_SECRETS = {
    "path": "PATH-TOKEN-7f3a",
    "query": "QUERY-VALUE-7f3a",
    "cookie": "COOKIE-VALUE-7f3a",
    "api_key": "PU-KEY-VALUE-7f3a",
    "client": "203.0.113.77",
    "lat": "51.4778123",
    "lon": "-0.0015123",
    "horizon_mask": "12.3456789",
}


def test_run_starts_uvicorn_from_environment(monkeypatch):
    import main
    import uvicorn

    calls = []
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "9123")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    main.run()

    assert calls == [
        (("main:app",), {"host": "127.0.0.1", "port": 9123, "reload": False})
    ]


def test_sentry_is_off_without_a_dsn():
    # conftest.py clears SENTRY_DSN before main is imported.
    import main
    import sentry_sdk

    assert main.app.title == "solar-system-db REST API"
    assert not sentry_sdk.is_initialized()


def test_sentry_events_carry_nothing_from_the_request(monkeypatch):
    """With a DSN, an error event and a trace hold no URL, path token, query string,
    header, cookie, body field (observer site) or client address. The DSN is a closed
    loopback port and the transport is a list: nothing is sent."""
    import json
    import logging

    import main
    import sentry_sdk
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    s = REQUEST_SECRETS
    monkeypatch.setenv("SENTRY_DSN", "http://public@127.0.0.1:9/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "1")  # opt in, so traces are checked too
    main._init_sentry()
    sent = []
    monkeypatch.setattr(sentry_sdk.get_client().transport, "capture_envelope", sent.append)
    probe = FastAPI()  # the SDK config is process-wide; main.app's routes stay untouched

    @probe.post("/boom/{token}")
    async def boom(token: str, payload: dict):
        logging.getLogger("solar_db").error("handling %s", token)
        raise RuntimeError("boom")

    headers = {
        "X-PU-Api-Key": s["api_key"],
        "Cookie": f"sid={s['cookie']}",
        "CF-Connecting-IP": s["client"],
    }
    body = {"lat": float(s["lat"]), "lon": float(s["lon"]), "horizon_mask": [[0, float(s["horizon_mask"])]]}
    try:
        client = TestClient(probe, raise_server_exceptions=False)
        for path in ("/boom/", "/missing/"):  # raises inside a route; matches no route
            client.post(path + s["path"], params={"q": s["query"], "lat": s["lat"]}, headers=headers, json=body)
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)

    items = [item for envelope in sent for item in envelope.items if item.payload.json]
    assert {"event", "transaction"} <= {item.type for item in items}
    sent_text = json.dumps([item.payload.json for item in items])
    assert [name for name, value in s.items() if value in sent_text] == []
    assert not sentry_sdk.is_initialized()
