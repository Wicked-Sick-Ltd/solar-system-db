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
