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
                 "list_object_types", "search"):
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
    from solar_db.sky_lookup import resolve_and_report
    r = resolve_and_report(db, "Jupiter", "2026-09-15T00:00:00Z")
    assert r["constellation"]["abbr"] == "Cnc"
    assert r["hemisphere"] == "northern"
    assert r["observer"] is None
