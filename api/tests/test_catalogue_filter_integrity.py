"""Do not silently broaden scientific filters on old schemas or boundaries."""
import importlib.util
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from solar_db import SolarDB
from solar_db.data_access import UnsupportedCatalogueFilter

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_catalogue(tmp_path):
    path = tmp_path / "filter.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript((ROOT / "schema/schema.sql").read_text())
    return path


@pytest.mark.parametrize("filters", [
    {"orbit_class": "APO"}, {"max_moid_au": 0.05}, {"max_condition_code": 0},
])
def test_old_catalogue_rejects_unsupported_filters_in_rest_and_mcp(isolated_catalogue, monkeypatch, filters):
    with sqlite3.connect(isolated_catalogue) as conn:
        conn.execute("DROP TABLE designations")
    db = SolarDB(isolated_catalogue)
    with pytest.raises(UnsupportedCatalogueFilter):
        db.find_objects(**filters)
    # Old catalogues can still honour the original filters.
    assert db.find_objects(object_type="asteroid", min_diameter_km=1) == []

    import api.main as api
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    with TestClient(api.app) as client:
        response = client.get("/api/v1/objects", params=filters)
    assert response.status_code == 503
    assert "schema v2" in response.json()["detail"]

    spec = importlib.util.spec_from_file_location("filter_mcp", ROOT / "mcp-server/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: db)
    with pytest.raises(UnsupportedCatalogueFilter):
        server.find_objects(**filters)


@pytest.mark.parametrize("target", [0.0, 1.01, 180.0, 359.5])
def test_meteor_activity_window_keeps_fractional_degrees(isolated_catalogue, monkeypatch, target):
    import solar_db.data_access as access
    monkeypatch.setattr(access, "solar_longitude_deg", lambda _: target)
    offsets = [-15.9, -15.0, -14.9, 0.0, 14.9, 15.0, 15.9, 15.000001]
    with sqlite3.connect(isolated_catalogue) as conn:
        conn.executemany(
            "INSERT INTO meteor_showers (iau_no, ad_no, code, name, solar_longitude_deg, source) "
            "VALUES (?, 0, ?, ?, ?, 'synthetic test')",
            [(i + 1, f"T{i}", f"Boundary {offset}", (target + offset) % 360)
             for i, offset in enumerate(offsets)],
        )
    result = SolarDB(isolated_catalogue).list_meteor_showers(active_on="2026-10-01")
    assert [row["iau_no"] for row in result] == [2, 3, 4, 5, 6]
