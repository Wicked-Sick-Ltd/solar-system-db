"""Nested atmosphere composition must not fail a public catalogue read.

A nightly artefact can pass scripts/pull_latest.sh and still store a deeply
nested composition_json. These tests fail while that cell is decoded with
json.loads and only ValueError is handled. They pass when the same cell is
treated like malformed JSON: the read succeeds and composition is null.

Covered reads: SolarDB.get_object, SolarDB.get_atmosphere, GET
/api/v1/objects/{name}, GET /api/v1/planets/{name}/atmosphere, GET
/api/v1/sky/{name}, and the MCP tools get_object, get_atmosphere, and
get_sky_position. List and position routes do not decode this column.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "mcp-server"))

from solar_db import SolarDB

EARTH_COMPOSITION = [{"species": "N2", "fraction": 0.78, "unit": "%"}]
VENUS_COMPOSITION = "{not-json"
MARS_PRESSURE_BAR = 0.006
VENUS_PRESSURE_BAR = 92.0
SKY_DATE = "2026-09-15T00:00:00Z"


def _nested_array_json() -> str:
    """A short, syntactically valid document this interpreter cannot decode."""
    depth = sys.getrecursionlimit()
    while depth <= 1_000_000:
        document = "[" * depth + "0" + "]" * depth
        try:
            json.loads(document)
        except RecursionError:
            return document
        depth *= 2
    raise RuntimeError("json.loads accepted a nested array past the search limit")


def _elements(object_id: str, semi_major_axis_au: float, eccentricity: float,
              inclination_deg: float, period_days: float) -> tuple:
    return (
        object_id, "J2000", "Sun", semi_major_axis_au, eccentricity, inclination_deg,
        0.0, 0.0, 0.0, period_days, 2451545.0,
    )


def _build_catalogue(path: Path, mars_composition: str) -> None:
    schema = (ROOT / "schema" / "schema.sql").read_text()
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema)
        conn.execute("PRAGMA foreign_keys = ON")
        bodies = [
            ("planet-earth", "Earth", "planet"),
            ("planet-mars", "Mars", "planet"),
            ("planet-venus", "Venus", "planet"),
        ]
        bodies.extend((f"ast-{index}", f"Filler {index}", "asteroid") for index in range(1001))
        conn.executemany(
            "INSERT INTO objects (id, name, object_type) VALUES (?, ?, ?)",
            bodies,
        )
        conn.executemany(
            """INSERT INTO orbital_elements (
                   object_id, frame, centre, semi_major_axis_au, eccentricity,
                   inclination_deg, longitude_ascending_node_deg, argument_periapsis_deg,
                   mean_anomaly_deg, orbital_period_days, epoch_jd
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _elements("planet-earth", 1.0, 0.0167, 0.0, 365.256),
                _elements("planet-mars", 1.5237, 0.0934, 1.85, 686.98),
            ),
        )
        conn.executemany(
            """INSERT INTO atmospheres (
                   object_id, surface_pressure_bar, composition_json, source
               ) VALUES (?, ?, ?, ?)""",
            (
                ("planet-earth", 1.0, json.dumps(EARTH_COMPOSITION), "NASA Planetary Fact Sheet"),
                ("planet-mars", MARS_PRESSURE_BAR, mars_composition, "NASA Planetary Fact Sheet"),
                ("planet-venus", VENUS_PRESSURE_BAR, VENUS_COMPOSITION, "NASA Planetary Fact Sheet"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _publish_and_install(tmp_path: Path, catalogue: Path) -> Path:
    if shutil.which("zstd") is None:
        pytest.fail("zstd CLI is required to exercise scripts/pull_latest.sh")
    out = tmp_path / "out"
    data = tmp_path / "data"
    out.mkdir()
    artefact_name = "solar_system-20261116.sqlite.zst"
    artefact = out / artefact_name
    subprocess.run(["zstd", "-q", "-f", "-o", str(artefact), str(catalogue)], check=True)
    digest = hashlib.sha256(artefact.read_bytes()).hexdigest()
    manifest = {
        "artefact": artefact_name,
        "url": (out / artefact_name).as_uri(),
        "sha256": digest,
    }
    (out / "latest.json").write_text(json.dumps(manifest))
    env = {
        **os.environ,
        "MANIFEST_URL": (out / "latest.json").as_uri(),
        "DATA_DIR": str(data),
        "RESTART_CMD": "true",
    }
    completed = subprocess.run(
        ["bash", str(ROOT / "scripts" / "pull_latest.sh")],
        check=False, env=env, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "integrity ok" in completed.stdout
    installed = data / "solar_system.sqlite"
    assert installed.is_file()
    with sqlite3.connect(installed) as conn:
        count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        stored = conn.execute(
            "SELECT composition_json FROM atmospheres WHERE object_id = ?",
            ("planet-mars",),
        ).fetchone()[0]
    assert count > 1000
    assert stored == _NESTED_COMPOSITION
    return installed


_NESTED_COMPOSITION = _nested_array_json()


@pytest.fixture(scope="module")
def installed_catalogue(tmp_path_factory):
    root = tmp_path_factory.mktemp("nested-atmosphere")
    catalogue = root / "source.sqlite"
    _build_catalogue(catalogue, _NESTED_COMPOSITION)
    return _publish_and_install(root, catalogue)


def _assert_mars(record: dict) -> None:
    assert record["surface_pressure_bar"] == pytest.approx(MARS_PRESSURE_BAR)
    assert record["composition"] is None
    assert "composition_json" not in record


def _assert_earth(record: dict) -> None:
    assert record["composition"] == EARTH_COMPOSITION


def _assert_venus(record: dict) -> None:
    assert record["surface_pressure_bar"] == pytest.approx(VENUS_PRESSURE_BAR)
    assert record["composition"] is None
    assert "composition_json" not in record


def _api_client(monkeypatch, installed_catalogue: Path) -> TestClient:
    # Importing the app constructs SolarDB from SOLAR_DB_PATH. Point that at
    # the installed artefact when the suite has not already built one.
    os.environ.setdefault("SOLAR_DB_PATH", str(installed_catalogue))
    import main

    monkeypatch.setattr(main, "db", SolarDB(installed_catalogue))
    return TestClient(main.app, raise_server_exceptions=False)


def _mcp(monkeypatch, installed_catalogue: Path):
    import server

    catalogue = SolarDB(installed_catalogue)
    monkeypatch.setattr(server, "db", lambda: catalogue)
    return server


def test_pull_installs_nested_composition_without_reading_it(installed_catalogue):
    """The installer gate is object count and quick_check, not this column."""
    assert installed_catalogue.is_file()


def test_get_atmosphere_survives_nested_composition(installed_catalogue):
    _assert_mars(SolarDB(installed_catalogue).get_atmosphere("Mars"))


def test_get_object_survives_nested_composition(installed_catalogue):
    detail = SolarDB(installed_catalogue).get_object("Mars")
    assert detail["name"] == "Mars"
    _assert_mars(detail["atmosphere"])


def test_valid_and_malformed_composition_still_decode(installed_catalogue):
    db = SolarDB(installed_catalogue)
    _assert_earth(db.get_atmosphere("Earth"))
    _assert_venus(db.get_atmosphere("Venus"))
    untouched = db.get_object("Filler 0")
    assert untouched["name"] == "Filler 0"
    assert untouched["atmosphere"] is None


def test_rest_atmosphere_survives_nested_composition(installed_catalogue, monkeypatch):
    client = _api_client(monkeypatch, installed_catalogue)
    atmosphere = client.get("/api/v1/planets/Mars/atmosphere")
    assert atmosphere.status_code == 200, atmosphere.text
    _assert_mars(atmosphere.json())


def test_rest_object_survives_nested_composition(installed_catalogue, monkeypatch):
    client = _api_client(monkeypatch, installed_catalogue)
    detail = client.get("/api/v1/objects/Mars")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["name"] == "Mars"
    _assert_mars(body["atmosphere"])


def test_rest_sky_survives_nested_composition(installed_catalogue, monkeypatch):
    client = _api_client(monkeypatch, installed_catalogue)
    sky = client.get("/api/v1/sky/Mars", params={"date": SKY_DATE})
    assert sky.status_code == 200, sky.text
    assert "ra_deg" in sky.json()


def test_rest_other_bodies_stay_readable(installed_catalogue, monkeypatch):
    client = _api_client(monkeypatch, installed_catalogue)
    earth = client.get("/api/v1/planets/Earth/atmosphere")
    assert earth.status_code == 200, earth.text
    _assert_earth(earth.json())
    venus = client.get("/api/v1/planets/Venus/atmosphere")
    assert venus.status_code == 200, venus.text
    _assert_venus(venus.json())
    other = client.get("/api/v1/objects/Filler 0")
    assert other.status_code == 200, other.text
    assert other.json()["atmosphere"] is None


def test_mcp_atmosphere_survives_nested_composition(installed_catalogue, monkeypatch):
    server = _mcp(monkeypatch, installed_catalogue)
    _assert_mars(server.get_atmosphere("Mars"))


def test_mcp_object_survives_nested_composition(installed_catalogue, monkeypatch):
    server = _mcp(monkeypatch, installed_catalogue)
    detail = server.get_object("Mars")
    assert detail["name"] == "Mars"
    _assert_mars(detail["atmosphere"])


def test_mcp_sky_survives_nested_composition(installed_catalogue, monkeypatch):
    server = _mcp(monkeypatch, installed_catalogue)
    sky = server.get_sky_position("Mars", SKY_DATE)
    assert "error" not in sky
    assert "ra_deg" in sky
