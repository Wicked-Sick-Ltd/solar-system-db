"""Tests for the packaged API console entry point."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
