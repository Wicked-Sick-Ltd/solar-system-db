"""Legacy sky estimates must not turn Earth into a fabricated lunar position."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from solar_db.sky_lookup import SkyLookupError, resolve_and_report


@pytest.fixture
def moon_db():
    db = Mock()
    db.get_object.return_value = {"id": "moon-luna", "name": "Moon", "object_type": "moon", "parent_id": "planet-earth"}
    return db


def test_luna_has_no_parent_earth_proxy(moon_db):
    with pytest.raises(SkyLookupError, match="lunar ephemeris") as error:
        resolve_and_report(moon_db, "Moon", "2026-10-01T22:00:00Z", 51.5, -0.12)
    assert error.value.status == 404
    moon_db.get_orbital_elements.assert_not_called()


def test_rest_and_mcp_luna_report_unavailable(moon_db, monkeypatch):
    import api.main as api
    monkeypatch.setattr(api, "db", moon_db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    with TestClient(api.app) as client:
        response = client.get("/api/v1/sky/Moon", params={"lat": 51.5, "lon": -0.12})
    assert response.status_code == 404
    assert "lunar ephemeris" in response.json()["detail"]
    path = Path(__file__).resolve().parents[2] / "mcp-server/server.py"
    spec = importlib.util.spec_from_file_location("lunar_mcp", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: moon_db)
    result = server.get_sky_position("Moon", lat=51.5, lon=-0.12)
    assert "lunar ephemeris" in result["error"]
    assert "observer" not in result


def test_other_moon_proxy_is_labelled(monkeypatch):
    import solar_db.sky_lookup as lookup
    db = Mock()
    db.get_object.return_value = {"id": "moon-titan", "name": "Titan", "object_type": "moon", "parent_id": "planet-saturn"}
    db.get_orbital_elements.return_value = {"semi_major_axis_au": 9.5}
    monkeypatch.setattr(lookup, "sky_report", lambda *args, **kwargs: {"accuracy_note": "Two-body approximation."})
    result = resolve_and_report(db, "Titan", "2026-10-01")
    assert result["resolved_from"] == "planet-saturn"
    assert "not an independent position" in result["accuracy_note"]
