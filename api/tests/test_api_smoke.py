"""REST API smoke tests — one per endpoint family."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def test_objects_planets(client):
    r = client.get("/api/v1/objects", params={"type": "planet"})
    assert r.status_code == 200
    names = {o["name"] for o in r.json()["results"]}
    assert {"Earth", "Mars", "Jupiter"} <= names


def test_get_object(client):
    r = client.get("/api/v1/objects/Ceres")
    assert r.status_code == 200
    assert r.json()["object_type"] == "dwarf_planet"


def test_get_object_404(client):
    r = client.get("/api/v1/objects/Nibiru")
    assert r.status_code == 404


def test_planet_moons(client):
    r = client.get("/api/v1/planets/Saturn/moons")
    assert r.status_code == 200
    assert len(r.json()["results"]) > 50


def test_planet_rings(client):
    r = client.get("/api/v1/planets/Saturn/rings")
    assert r.status_code == 200
    assert any(x["name"] == "B Ring" for x in r.json()["results"])


def test_dwarf_planets(client):
    r = client.get("/api/v1/dwarf-planets")
    assert r.status_code == 200
    assert len(r.json()["results"]) == 5


def test_neos(client):
    r = client.get("/api/v1/neos", params={"limit": 50})
    assert r.status_code == 200
    assert len(r.json()["results"]) > 10


def test_periodic_comets(client):
    r = client.get("/api/v1/comets/periodic", params={"limit": 2000})
    assert r.status_code == 200
    assert any("Halley" in (c["name"] or "") for c in r.json()["results"])


def test_tnos(client):
    r = client.get("/api/v1/tnos", params={"limit": 30})
    assert r.status_code == 200
    assert len(r.json()["results"]) > 0


def test_search(client):
    r = client.get("/api/v1/search", params={"q": "Pluto"})
    assert r.status_code == 200
    assert any(x["object_type"] == "dwarf_planet" for x in r.json()["results"])


def test_positions_earth(client):
    r = client.get("/api/v1/positions/Earth", params={"date": "2025-06-01"})
    assert r.status_code == 200
    body = r.json()
    assert 0.95 < body["distance_from_sun_au"] < 1.05


def test_positions_pluto(client):
    # Dwarf planets get orbital elements from SBDB (Stage 1b); Pluto's spkid
    # must be the small-body id 2134340, not NAIF 999 (asteroid Zachia).
    r = client.get("/api/v1/positions/dwarf-pluto", params={"date": "2026-09-15"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "Pluto"
    assert 29 < d["distance_from_sun_au"] < 50


def test_next_perihelion_halley(client):
    r = client.get("/api/v1/perihelion/1P%2FHalley")
    assert r.status_code == 200
    assert r.json()["next_perihelion_jd"] > 2470000  # post-2061-ish


def test_object_types(client):
    r = client.get("/api/v1/object-types")
    assert r.status_code == 200
    types = {t["object_type"] for t in r.json()["results"]}
    assert {"planet", "moon", "comet"} <= types


def test_sources(client):
    r = client.get("/api/v1/sources")
    assert r.status_code == 200
    assert len(r.json()["results"]) > 0


def test_schema(client):
    r = client.get("/api/v1/schema")
    assert r.status_code == 200
    assert "CREATE TABLE" in r.text


def test_stats(client):
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    assert r.json()["total_objects"] > 2000


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_openapi(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/v1/objects" in paths
    assert "/api/v1/planets/{name}/moons" in paths


def test_no_astrology_paths(client):
    """Make sure no astrology terms snuck into the OpenAPI."""
    r = client.get("/openapi.json")
    spec_text = r.text.lower()
    for term in ("horoscope", "natal", "ascendant", "zodiac"):
        assert term not in spec_text, f"Astrology term {term!r} in OpenAPI"


def test_sky_mars(client):
    r = client.get("/api/v1/sky/Mars", params={"date": "2026-09-15T00:00:00Z"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "Mars"
    assert d["constellation"]["abbr"] == "Gem"
    assert d["observer"] is None and d["resolved_from"] is None


def test_sky_observer(client):
    r = client.get("/api/v1/sky/Mars", params={"date": "2026-09-15T21:00:00Z", "lat": 51.5, "lon": -0.12})
    assert r.status_code == 200, r.text
    o = r.json()["observer"]
    assert o["lat"] == 51.5 and o["lon"] == -0.12
    assert isinstance(o["is_up"], bool) and isinstance(o["is_dark"], bool)


def test_sky_lat_without_lon_is_422(client):
    assert client.get("/api/v1/sky/Mars", params={"lat": 51.5}).status_code == 422


def test_sky_moon_uses_parent(client):
    r = client.get("/api/v1/sky/Titan", params={"date": "2026-09-15"})
    assert r.status_code == 200, r.text
    assert r.json()["resolved_from"] == "planet-saturn"


def test_sky_sun(client):
    r = client.get("/api/v1/sky/sun", params={"date": "2026-09-15"})
    assert r.status_code == 200, r.text
    assert r.json()["elongation_deg"] == 0.0


def test_sky_404_without_match(client):
    assert client.get("/api/v1/sky/nothing-here-xyz").status_code == 404


def test_get_object_v2_blocks(client):
    d = client.get("/api/v1/objects/Vesta").json()
    assert d["orbital"]["orbit_class_code"] == "MBA" and d["orbital"]["moid_au"] is not None
    assert d["visual"]["spectral_type"] == "V" and d["physical"]["gm_km3_s2"] is not None
    assert d["discovery"]["discoverer"] == "Olbers, H. W." and d["discovery"]["site"] == "Bremen"
    kinds = {x["designation"]: x["kind"] for x in d["designations"]}
    assert kinds["4"] == "number" and kinds["Vesta"] == "name"
    assert "close_approach_count" in d and "atmosphere" in d


def test_ceres_is_the_curated_dwarf_planet_with_sbdb_orbit_and_gap_fill(client):
    d = client.get("/api/v1/objects/Ceres").json()
    assert d["id"] == "dwarf-ceres"
    assert d["physical"]["radius_km"] == 469.73          # NASA fact sheet wins
    assert d["physical"]["gm_km3_s2"] is not None        # SBDB fills the gap
    assert d["orbital"]["orbit_class_code"] == "MBA" and d["discovery"]["site"] == "Palermo"


def test_resolve_by_provisional_designation_and_number(client):
    assert client.get("/api/v1/objects/A801 AA").json()["name"] == "Ceres"
    assert client.get("/api/v1/objects/1").json()["name"] == "Ceres"
    assert client.get("/api/v1/objects/134340").json()["id"] == "dwarf-pluto"


def test_some_object_has_close_approaches(client):
    import os
    import sqlite3
    conn = sqlite3.connect(os.environ["SOLAR_DB_PATH"])
    obj_id = conn.execute("SELECT object_id FROM close_approaches LIMIT 1").fetchone()[0]
    d = client.get(f"/api/v1/objects/{obj_id}").json()
    assert d["close_approach_count"] >= 1
