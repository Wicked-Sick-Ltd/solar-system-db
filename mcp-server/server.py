"""solar-system-db MCP server.

An MCP server for **astronomy** — querying a curated catalogue of known
solar-system objects (planets, moons, dwarf planets, asteroids, comets, TNOs,
centaurs, rings). Useful for astronomy teachers, hobbyist stargazers, science
writers, planetarium operators, sci-fi worldbuilders, and model-builders.

This is NOT an astrology tool. There are no horoscopes, houses, transits,
natal charts, or aspects. Every tool here is grounded in observational data
from NASA/JPL and the IAU Minor Planet Center.

Run:
    python server.py                       # stdio (default; for Claude Desktop, Cursor)
    python server.py --transport http      # streamable-http on $PORT (default 8002)
    python server.py --transport sse       # legacy SSE transport
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make the parent (solar_db package) importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from solar_db import SolarDB, compute_heliocentric_position, next_perihelion_jd
from solar_db.positions import date_to_jd
from solar_db.sky_lookup import SkyLookupError, resolve_and_report

mcp = FastMCP(
    name="solar-system-db",
    instructions=(
        "Queryable catalogue of known solar-system objects for astronomy and "
        "science-education use. Bodies: planets, moons, dwarf planets, "
        "asteroids, comets, TNOs, centaurs, rings. Data sourced from NASA/JPL "
        "and the IAU Minor Planet Center. Use the `search` and `find_objects` "
        "tools for discovery; `get_object` for full detail; `compute_position` "
        "for two-body orbital propagation. This is an astronomy tool — not "
        "astrology."
    ),
)

# Single shared DB connection-factory; instances reuse it.
_db: SolarDB | None = None


def db() -> SolarDB:
    global _db
    if _db is None:
        _db = SolarDB()
    return _db


# Catalog tools
def _bounded_limit(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))


@mcp.tool()
def find_objects(
    object_type: str | None = None,
    parent: str | None = None,
    min_radius_km: float | None = None,
    max_radius_km: float | None = None,
    max_eccentricity: float | None = None,
    min_semi_major_axis_au: float | None = None,
    max_semi_major_axis_au: float | None = None,
    neo: bool | None = None,
    pha: bool | None = None,
    named_only: bool | None = None,
    orbit_class: str | None = None,
    max_moid_au: float | None = None,
    min_diameter_km: float | None = None,
    max_condition_code: int | None = None,
    discovered_after: str | None = None,
    after: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Filter solar-system objects by type, parent body, size, orbit, and classification.

    Args:
      object_type: planet / moon / dwarf_planet / dwarf_planet_candidate /
                   asteroid / comet / tno / centaur. Omit to search all.
      parent: parent body name or id (e.g. "Jupiter" returns Jupiter's moons).
      min_radius_km, max_radius_km: physical radius bounds in kilometres.
      max_eccentricity: orbital eccentricity upper bound.
      min/max_semi_major_axis_au: orbital semi-major axis bounds in AU.
      neo: True returns only Near-Earth Objects.
      pha: True returns only Potentially Hazardous Asteroids.
      named_only: True excludes provisional-designation-only objects.
      orbit_class: SBDB orbit class code, e.g. MBA, APO, TNO, or JFc.
      max_moid_au: maximum Earth minimum-orbit-intersection distance in AU.
      min_diameter_km: estimated diameter lower bound in kilometres.
      max_condition_code: maximum orbit uncertainty code, from 0 (best) to 9.
      discovered_after: earliest discovery date, as an ISO date.
      after: last object id from the previous page for keyset pagination.
      limit: 1–1000, default 50.
    Returns: list of object summaries with key orbital + physical fields.
    """
    return db().find_objects(
        object_type=object_type, parent=parent,
        min_radius_km=min_radius_km, max_radius_km=max_radius_km,
        max_eccentricity=max_eccentricity,
        min_semi_major_axis_au=min_semi_major_axis_au,
        max_semi_major_axis_au=max_semi_major_axis_au,
        neo=neo, pha=pha, named_only=named_only,
        orbit_class=orbit_class, max_moid_au=max_moid_au,
        min_diameter_km=min_diameter_km,
        max_condition_code=max_condition_code,
        discovered_after=discovered_after, after=after,
        limit=_bounded_limit(limit, 1000),
    )


@mcp.tool()
def get_object(name_or_designation: str) -> dict | None:
    """Return the full record for one object — orbital + physical + visual
    properties, classifications, and provenance.

    Resolves by exact id, exact name, exact designation, or partial match
    (e.g. "Halley", "1P/Halley", "Ceres", "(1) Ceres", "Pluto").
    """
    obj = db().get_object(name_or_designation)
    if obj is None:
        return {"error": f"No object found matching {name_or_designation!r}. "
                         f"Try the search tool to discover candidates."}
    return obj


@mcp.tool()
def list_moons(planet_name: str) -> list[dict]:
    """List all moons of a given planet (or dwarf planet), ordered by orbital
    distance. Example: list_moons("Saturn") returns ~146 moons."""
    return db().list_moons(planet_name)


@mcp.tool()
def list_dwarf_planets(include_candidates: bool = False) -> list[dict]:
    """List the IAU-recognised dwarf planets (Ceres, Pluto, Eris, Makemake,
    Haumea). Set include_candidates=True to also include leading candidates
    (Orcus, Quaoar, Sedna, Gonggong, Salacia)."""
    return db().list_dwarf_planets(include_candidates=include_candidates)


@mcp.tool()
def list_neos(min_diameter_km: float | None = None,
              max_diameter_km: float | None = None) -> list[dict]:
    """List Near-Earth Objects, ordered by absolute magnitude H (brightest first).
    `min_diameter_km` / `max_diameter_km` filter on the estimated diameter."""
    return db().list_neos(min_diameter_km=min_diameter_km,
                           max_diameter_km=max_diameter_km)


@mcp.tool()
def list_periodic_comets(limit: int = 500) -> list[dict]:
    """List numbered / periodic comets (orbital period < 200 years), ordered by
    period. Includes 1P/Halley, 67P/Churyumov-Gerasimenko, 109P/Swift-Tuttle,
    etc. `limit` is 1–2000, default 500."""
    return db().list_periodic_comets(limit=_bounded_limit(limit, 2000))


@mcp.tool()
def list_tnos(limit: int = 500) -> list[dict]:
    """List trans-Neptunian objects and centaurs, ordered by semi-major axis.
    Includes Pluto's KBO cousins, Sedna, Eris, Quaoar, Chiron, Chariklo, etc.
    `limit` is 1–2000, default 500."""
    return db().list_tnos(limit=_bounded_limit(limit, 2000))


@mcp.tool()
def get_rings(planet_name: str) -> list[dict]:
    """List the rings of a given planet or dwarf planet, ordered by inner
    radius. Example: get_rings("Saturn") returns all known Saturn ring components
    (D, C, B, Cassini Division, A, F, G, E, Phoebe ring)."""
    return db().get_rings(planet_name)


@mcp.tool()
def search(query: str, limit: int = 20) -> list[dict]:
    """Free-text search across object names, designations, and discoverers.
    Use this when you don't know the exact name — e.g. search("Halley")
    returns both comet 1P/Halley and asteroid (2688) Halley."""
    return db().search(query, limit=limit)


# Position / ephemeris tools
@mcp.tool()
def get_close_approaches(name_or_designation: str, date_min: str | None = None, date_max: str | None = None,
                         body: str | None = None, limit: int = 100) -> dict:
    """Close approaches of one body to the planets or the Moon (JPL CAD + SBDB).
    Dates are ISO; `body` filters to e.g. "Earth". Nearest-in-time first."""
    rows = db().close_approaches_for(name_or_designation, date_min=date_min, date_max=date_max, body=body, limit=limit)
    if rows is None:
        return {"error": f"No object found matching {name_or_designation!r}."}
    return {"object": name_or_designation, "results": rows}


@mcp.tool()
def find_close_approaches(date_min: str, date_max: str, body: str = "Earth",
                          max_dist_au: float = 0.05, limit: int = 200) -> list[dict]:
    """Every catalogued close approach to `body` between two ISO dates, nearest
    first. 1 lunar distance ≈ 0.00257 AU. Example: what passes Earth this year?"""
    return db().close_approaches_between(date_min, date_max, body=body, max_dist_au=max_dist_au, limit=limit)


@mcp.tool()
def get_discovery(name_or_designation: str) -> dict:
    """Discovery circumstances: date, discoverer(s), site, and the naming
    citation where one exists (MPC numbered list + SBDB)."""
    d = db().get_discovery(name_or_designation)
    return d if d is not None else {"error": f"No object found matching {name_or_designation!r}."}


@mcp.tool()
def get_designations(name_or_designation: str) -> list[dict] | dict:
    """Every identifier a body has carried: permanent number, name, provisional
    and alternate designations, legacy ids."""
    d = db().get_designations(name_or_designation)
    return d if d is not None else {"error": f"No object found matching {name_or_designation!r}."}


@mcp.tool()
def list_meteor_showers(established_only: bool = False, active_on: str | None = None,
                        limit: int = 200) -> list[dict]:
    """List IAU Meteor Data Center showers — one entry per parameter set (a
    shower may have several, from different observation campaigns).
    `established_only` keeps only showers whose MDC status code is 1 (single
    established shower, group) or 6 (member of the established group).
    `active_on` (ISO date, YYYY-MM-DD) keeps only showers whose activity peak
    (solar longitude) falls within 15 degrees of the Sun's position on that
    date."""
    return db().list_meteor_showers(established_only=established_only, active_on=active_on, limit=limit)


@mcp.tool()
def get_meteor_shower(code_or_name: str) -> dict:
    """One IAU Meteor Data Center shower by 3-letter code (e.g. "GEM") or name
    (e.g. "Geminids"), case-insensitively — all its parameter sets, plus its
    parent comet/asteroid when the MDC has linked one."""
    s = db().get_meteor_shower(code_or_name)
    return s if s is not None else {"error": f"No meteor shower found matching {code_or_name!r}."}


@mcp.tool()
def get_atmosphere(planet: str) -> dict:
    """Atmosphere of a planet, Pluto or Titan-class body from the NASA fact
    sheets: surface pressure, temperature, density, scale height, winds and
    composition by volume."""
    a = db().get_atmosphere(planet)
    return a if a is not None else {"error": f"No object found matching {planet!r}."}


@mcp.tool()
def get_download_info() -> dict:
    """How to download the entire catalogue as one SQLite file: URL, size,
    SHA-256, build date, row counts, enrichment coverage and licence."""
    m = db().download_manifest()
    return m if m is not None else {"error": "No published artefact yet."}


@mcp.tool()
def compute_position(name_or_designation: str, date: str) -> dict:
    """Compute the heliocentric ecliptic position (J2000) of an object at a
    given date by Keplerian two-body propagation.

    Args:
      name_or_designation: any object that has stored orbital elements.
      date: ISO 8601 date or datetime (e.g. "2027-04-15" or "2027-04-15T12:00:00Z").

    Returns x/y/z in AU plus radius, true anomaly, and an accuracy note.
    For arcsecond precision use the JPL Horizons API directly — this is
    intended for general astronomy, planetarium-style visualisations, and
    educational use.
    """
    elements = db().get_orbital_elements(name_or_designation)
    if not elements:
        return {"error": f"No object found matching {name_or_designation!r}."}
    try:
        jd = date_to_jd(date)
        pos = compute_heliocentric_position(elements, jd)
    except ValueError as e:
        return {"error": str(e)}
    return {
        "name": elements.get("name"),
        "designation": elements.get("designation"),
        "input_date": date,
        **pos,
    }


@mcp.tool()
def get_sky_position(name_or_designation: str, date: str | None = None,
                     lat: float | None = None, lon: float | None = None) -> dict:
    """Where an object appears in Earth's sky: RA/Dec (J2000), constellation,
    hemisphere, distance from Earth and elongation from the Sun.

    Args:
      name_or_designation: any planet, dwarf planet, asteroid, comet or moon
        (moons report their parent planet's position), or "sun".
      date: ISO 8601 date or datetime, UTC. Defaults to now.
      lat, lon: observer's latitude (north-positive) and longitude
        (east-positive) in degrees. Give both to add altitude/azimuth, whether
        it is above the horizon after dark, and rise/transit/set for that day.

    Two-body accuracy (about a degree for the planets) — enough to name the
    constellation and say whether it is up; use JPL Horizons for precision.
    """
    try:
        return resolve_and_report(db(), name_or_designation, date, lat, lon)
    except SkyLookupError as e:
        return {"error": e.detail}


@mcp.tool()
def next_perihelion(name_or_designation: str) -> dict:
    """Return the next perihelion passage (closest approach to the Sun) of an
    object as a Julian Date.

    Two-body Kepler estimate — fine for periodic comets and minor planets
    over short look-aheads. Long-period comets and bodies in mean-motion
    resonance may drift; use JPL Horizons for mission-planning precision.
    """
    elements = db().get_orbital_elements(name_or_designation)
    if not elements:
        return {"error": f"No object found matching {name_or_designation!r}."}
    try:
        jd = next_perihelion_jd(elements)
    except ValueError as e:
        return {"error": str(e)}
    return {
        "name": elements.get("name"),
        "designation": elements.get("designation"),
        "next_perihelion_jd": jd,
        "orbital_period_days": elements.get("orbital_period_days"),
    }


# Reference / discovery
@mcp.tool()
def list_object_types() -> list[dict]:
    """List the object_type values present in the catalogue and how many rows
    each has. Useful as a sanity-check on coverage."""
    return db().list_object_types()


@mcp.tool()
def get_sources() -> list[dict]:
    """Return the upstream data sources used to populate the catalogue,
    summarised by source name and last-retrieved timestamp. Sources are NASA
    Planetary Fact Sheets, JPL Solar System Dynamics, JPL Small-Body Database,
    and the IAU Minor Planet Center (via SBDB)."""
    return db().get_sources()


@mcp.tool()
def get_schema() -> str:
    """Return the SQLite schema (DDL) for the catalogue, including views.
    Useful if you want to write your own queries against a local copy of
    data/solar_system.sqlite."""
    return db().get_schema()


@mcp.tool()
def get_stats() -> dict:
    """Catalogue statistics: total objects, counts by object_type, and the
    timestamp of the most recent build."""
    return db().stats()


# Resources
@mcp.resource("solar-system://schema")
def schema_resource() -> str:
    """The SQLite schema, exposed as a resource."""
    return db().get_schema()


@mcp.resource("solar-system://catalog-stats")
def catalog_stats_resource() -> str:
    """Catalogue statistics, exposed as a resource (JSON)."""
    import json
    return json.dumps(db().stats(), indent=2, default=str)


# Entry point
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument(
        "--transport", choices=("stdio", "http", "streamable-http", "sse"),
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="Transport (default: stdio). 'http' is an alias for 'streamable-http'.",
    )
    p.add_argument(
        "--port", type=int, default=int(os.environ.get("MCP_PORT", "8002")),
        help="Port to bind on for HTTP transports.",
    )
    p.add_argument(
        "--host", default=os.environ.get("MCP_HOST", "0.0.0.0"),
        help="Host to bind on for HTTP transports.",
    )
    args = p.parse_args(argv)

    # Eagerly open DB so a missing/corrupt DB fails loudly at startup.
    db()

    transport = "streamable-http" if args.transport == "http" else args.transport
    if transport in ("streamable-http", "sse"):
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"solar-system-db MCP server: {transport} on "
              f"{args.host}:{args.port}", file=sys.stderr)
    mcp.run(transport=transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
