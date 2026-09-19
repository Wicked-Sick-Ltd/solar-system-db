"""Smoke tests — one per tool family. These are the breakage canary; if the
DB schema changes or a tool signature drifts, these fail."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the package importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def db():
    from solar_db import SolarDB
    return SolarDB()


# ----- catalog ---------------------------------------------------------------
def test_find_objects_planets(db):
    rows = db.find_objects(object_type="planet")
    assert len(rows) == 8
    names = {r["name"] for r in rows}
    assert {"Mercury", "Venus", "Earth", "Mars",
            "Jupiter", "Saturn", "Uranus", "Neptune"} <= names


def test_mcp_find_objects_forwards_rest_filters(monkeypatch):
    import server

    class RecordingDB:
        def find_objects(self, **kwargs):
            self.kwargs = kwargs
            return []

    recording_db = RecordingDB()
    monkeypatch.setattr(server, "db", lambda: recording_db)

    assert server.find_objects(
        orbit_class="APO",
        max_moid_au=0.05,
        min_diameter_km=1.0,
        max_condition_code=3,
        discovered_after="2000-01-01",
        after="asteroid-100",
        limit=1000,
    ) == []
    assert recording_db.kwargs["orbit_class"] == "APO"
    assert recording_db.kwargs["max_moid_au"] == 0.05
    assert recording_db.kwargs["min_diameter_km"] == 1.0
    assert recording_db.kwargs["max_condition_code"] == 3
    assert recording_db.kwargs["discovered_after"] == "2000-01-01"
    assert recording_db.kwargs["after"] == "asteroid-100"
    assert recording_db.kwargs["limit"] == 1000


def test_mcp_catalog_limits_match_rest(monkeypatch):
    import server

    class RecordingDB:
        def find_objects(self, **kwargs):
            self.find_limit = kwargs["limit"]
            return []

        def list_periodic_comets(self, limit):
            self.comet_limit = limit
            return []

        def list_tnos(self, limit):
            self.tno_limit = limit
            return []

    recording_db = RecordingDB()
    monkeypatch.setattr(server, "db", lambda: recording_db)

    server.find_objects(limit=1001)
    server.list_periodic_comets(limit=2001)
    server.list_tnos(limit=0)

    assert recording_db.find_limit == 1000
    assert recording_db.comet_limit == 2000
    assert recording_db.tno_limit == 1


def test_get_object_halley_comet(db):
    obj = db.get_object("1P/Halley")
    assert obj is not None
    assert obj["object_type"] == "comet"
    assert obj["orbital"]["orbital_period_days"] > 27000  # ~76 y


def test_list_moons_jupiter(db):
    moons = db.list_moons("Jupiter")
    assert len(moons) >= 70
    assert {"Io", "Europa", "Ganymede", "Callisto"} <= {m["name"] for m in moons}


def test_list_dwarf_planets(db):
    dps = db.list_dwarf_planets()
    assert len(dps) == 5
    assert {"Ceres", "Pluto", "Eris", "Makemake", "Haumea"} == {d["name"] for d in dps}


def test_list_dwarf_planet_candidates(db):
    dps = db.list_dwarf_planets(include_candidates=True)
    assert len(dps) >= 10


def test_list_neos_some(db):
    rows = db.list_neos()
    assert len(rows) > 100


def test_list_periodic_comets(db):
    rows = db.list_periodic_comets()
    assert len(rows) > 50
    assert any("Halley" in (r["name"] or "") for r in rows)


def test_list_tnos(db):
    rows = db.list_tnos()
    assert len(rows) >= 50


def test_get_rings_saturn(db):
    rings = db.get_rings("Saturn")
    assert len(rings) >= 7
    assert any(r["name"] == "B Ring" for r in rings)


def test_search_pluto(db):
    rows = db.search("Pluto")
    assert any(r["object_type"] == "dwarf_planet" for r in rows)


def test_get_meteor_shower_perseids(db):
    shower = db.get_meteor_shower("Perseids")
    assert shower is not None
    assert shower["code"] == "PER"


# ----- position / ephemeris --------------------------------------------------
def test_compute_position_earth(db):
    from solar_db import compute_heliocentric_position
    from solar_db.positions import date_to_jd
    elem = db.get_orbital_elements("Earth")
    jd = date_to_jd("2025-06-01")
    pos = compute_heliocentric_position(elem, jd)
    # Earth's distance from Sun should be ~1 AU within a few %
    assert 0.97 < pos["distance_from_sun_au"] < 1.03


def test_next_perihelion_halley(db):
    from solar_db import next_perihelion_jd
    elem = db.get_orbital_elements("1P/Halley")
    jd = next_perihelion_jd(elem, after_jd=2451545.0)  # J2000
    # next perihelion after 2000 was 2061-07-28, JD ~ 2473810
    assert 2470000 < jd < 2480000


# ----- reference -------------------------------------------------------------
def test_list_object_types(db):
    types = db.list_object_types()
    type_set = {t["object_type"] for t in types}
    assert {"planet", "moon", "dwarf_planet", "comet", "asteroid"} <= type_set


def test_get_sources(db):
    srcs = db.get_sources()
    assert len(srcs) > 0
    assert any("JPL" in s["source_name"] for s in srcs)


def test_get_schema(db):
    ddl = db.get_schema()
    assert "CREATE TABLE" in ddl
    assert "objects" in ddl


def test_stats(db):
    s = db.stats()
    assert s["total_objects"] > 2000
    assert "by_object_type" in s


# ----- MCP server registration (loads the actual server module) --------------
def test_mcp_server_loads():
    import importlib
    server = importlib.import_module("server")
    # Server module should expose the FastMCP instance + the tool names
    assert hasattr(server, "mcp")
    for name in ("find_objects", "get_object", "list_moons", "list_neos",
                 "list_periodic_comets", "list_tnos", "get_rings",
                 "compute_position", "next_perihelion",
                 "get_schema", "get_stats", "get_sources",
                 "list_object_types", "search",
                 "list_meteor_showers", "get_meteor_shower",
                 "get_sky_position", "get_download_info",
                 "get_close_approaches", "find_close_approaches"):
        assert hasattr(server, name), f"missing tool: {name}"


def test_astronomy_branding():
    """The server should self-identify as an astronomy tool, not astrology."""
    import importlib
    server = importlib.import_module("server")
    src = Path(server.__file__).read_text().lower()
    # MUST say astronomy
    assert "astronomy" in src
    # MUST NOT use astrology vocabulary as the primary subject. We allow the
    # disclaimer text to mention them explicitly to say "we don't do those" —
    # but only inside the docstring header and the FastMCP instructions block.
    primary_use = src.split('@mcp.tool', 1)[1]  # everything from first tool down
    forbidden = ("horoscope", "natal", "zodiac", "ascendant", "midheaven")
    for word in forbidden:
        assert word not in primary_use, (
            f"Astrology term {word!r} appears in a tool definition")


def test_get_sky_position_jupiter(db):
    import server

    server._db = db
    r = server.get_sky_position("Jupiter", "2026-09-15T00:00:00Z")
    assert r["constellation"]["abbr"] == "Cnc"
    assert r["hemisphere"] == "northern"
    assert r["observer"] is None


def test_mcp_download_info(db, monkeypatch, tmp_path):
    import json
    import server

    manifest = tmp_path / "latest.json"
    manifest.write_text(json.dumps({"url": "https://example.test/catalogue.zst", "sha256": "abc"}))
    monkeypatch.setenv("SOLAR_MANIFEST_PATH", str(manifest))
    server._db = db

    assert server.get_download_info()["sha256"] == "abc"


def test_mcp_close_approaches(db):
    import server

    server._db = db
    rows = server.find_close_approaches(
        "1900-01-01", "2200-01-01", max_dist_au=5, limit=1,
    )
    assert len(rows) == 1
    detail = server.get_close_approaches(rows[0]["object_id"], limit=1)
    assert detail["results"]
