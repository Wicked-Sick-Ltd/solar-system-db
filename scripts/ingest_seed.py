"""Curated seed: Sun, planets, dwarf planets (+ candidates), notable TNOs,
named moons, rings — from NASA fact sheets and the hand-maintained tables in
seed_major.py / seed_moons.py. Runs first in every build; bulk sources fill in
around it and never overwrite curated physical/visual values.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import add_source, upsert_object, upsert_row  # noqa: E402
from seed_major import (DWARF_PLANETS, EARTH_MOONS, JUPITER_GALILEAN, MARS_MOONS, NEPTUNE_MAJOR,  # noqa: E402
                        NOTABLE_TNOS, OTHER_DWARF_MOONS, PLANETS, PLUTO_MOONS, RINGS, SATURN_MAJOR, SUN,
                        URANUS_MAJOR)
from seed_moons import ALL_MOONS  # noqa: E402

J2000_JD = 2451545.0  # JD for 2000-01-01.5 TT


def write_major_body(conn, body: dict, object_type: str,
                     parent_id: str | None = None) -> None:
    """Insert one curated body (sun/planet/dwarf/moon) and its properties."""
    upsert_object(
        conn,
        id=body["id"], name=body["name"], object_type=object_type,
        designation=body.get("designation"), parent_id=parent_id or body.get("parent_id"),
        discoverer=body.get("discoverer"), discovery_date=body.get("discovery_date"),
        wikipedia_url=body.get("wikipedia_url"),
    )
    if "orbital" in body:
        oe = dict(body["orbital"])
        oe.setdefault("epoch", "J2000")
        oe.setdefault("epoch_jd", J2000_JD)
        oe.setdefault("frame", "J2000")
        oe.setdefault("centre", "Sun" if parent_id is None and object_type != "moon" else
                      (parent_id or body.get("parent_id") or "Sun"))
        upsert_row(conn, "orbital_elements", body["id"], oe)
        add_source(conn, object_id=body["id"], table_name="orbital_elements",
                   source_name="NASA Planetary Fact Sheet / JPL keplerian elements",
                   source_url="https://nssdc.gsfc.nasa.gov/planetary/factsheet/")
    if "physical" in body:
        upsert_row(conn, "physical_properties", body["id"], body["physical"])
        add_source(conn, object_id=body["id"], table_name="physical_properties",
                   source_name="NASA Planetary Fact Sheet",
                   source_url="https://nssdc.gsfc.nasa.gov/planetary/factsheet/")
    if "visual" in body:
        upsert_row(conn, "visual_properties", body["id"], body["visual"])
        add_source(conn, object_id=body["id"], table_name="visual_properties",
                   source_name="NASA Planetary Fact Sheet",
                   source_url="https://nssdc.gsfc.nasa.gov/planetary/factsheet/")


def safe_float(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Stage 1: major bodies (sun, planets, dwarf planets, curated moons, rings)
# ---------------------------------------------------------------------------
def populate_major_bodies(conn) -> dict[str, int]:
    counts: dict[str, int] = {}

    write_major_body(conn, SUN, "star")
    counts["star"] = 1

    for pl in PLANETS:
        write_major_body(conn, pl, "planet", parent_id="sun")
    counts["planet"] = len(PLANETS)

    # Dwarf planets + candidates (must come BEFORE moons that reference them)
    for dp in DWARF_PLANETS:
        write_major_body(conn, dp, dp["object_type"], parent_id="sun")
    counts["dwarf_planet"] = sum(1 for d in DWARF_PLANETS if d["object_type"] == "dwarf_planet")
    counts["dwarf_planet_candidate"] = sum(1 for d in DWARF_PLANETS if d["object_type"] == "dwarf_planet_candidate")

    # Curated moons (those with rich properties)
    curated_moons = [
        (EARTH_MOONS,      "planet-earth"),
        (MARS_MOONS,       "planet-mars"),
        (JUPITER_GALILEAN, "planet-jupiter"),
        (SATURN_MAJOR,     "planet-saturn"),
        (URANUS_MAJOR,     "planet-uranus"),
        (NEPTUNE_MAJOR,    "planet-neptune"),
        (PLUTO_MOONS,      "dwarf-pluto"),
    ]
    n_moons = 0
    for moons, parent in curated_moons:
        for moon in moons:
            write_major_body(conn, moon, "moon", parent_id=parent)
            n_moons += 1
    # Other dwarf-planet moons (varied parents)
    for moon in OTHER_DWARF_MOONS:
        write_major_body(conn, moon, "moon", parent_id=moon["parent_id"])
        n_moons += 1

    # Named-only moons (from seed_moons.ALL_MOONS) — names + parents, no detail
    for moon in ALL_MOONS:
        # Avoid clobbering richer rows above (UPSERT keeps existing detail)
        upsert_object(
            conn,
            id=moon["id"], name=moon["name"], object_type="moon",
            parent_id=moon["parent_id"], discoverer=moon.get("discoverer"),
            discovery_date=moon.get("discovery_date"),
        )
        # Ensure property rows exist (FK joins won't fail)
        upsert_row(conn, "orbital_elements",    moon["id"], {})
        upsert_row(conn, "physical_properties", moon["id"], {})
        upsert_row(conn, "visual_properties",   moon["id"], {})
        n_moons += 1
    counts["moon"] = n_moons

    n_rings = 0
    for ring in RINGS:
        conn.execute(
            """
            INSERT INTO rings (parent_id, name, inner_radius_km, outer_radius_km,
                               width_km, thickness_km, notes)
            VALUES (:parent_id, :name, :inner_radius_km, :outer_radius_km,
                    :width_km, :thickness_km, :notes)
            """,
            {**{"width_km": None, "thickness_km": None, "notes": None}, **ring},
        )
        n_rings += 1
    counts["rings"] = n_rings

    return counts


def seed_notable_tnos(conn) -> int:
    """Identity rows only; orbital/physical detail arrives from the SBDB bulk pull via `curated_numbers()`."""
    for body in NOTABLE_TNOS:
        upsert_object(conn, id=body["id"], name=body["name"], designation=body.get("designation"),
                      object_type=body["object_type"], parent_id="sun", discoverer=body.get("discoverer"),
                      discovery_date=body.get("discovery_date"), wikipedia_url=body.get("wikipedia_url"))
        for tbl in ("orbital_elements", "physical_properties", "visual_properties"):
            upsert_row(conn, tbl, body["id"], {})
    conn.commit()
    return len(NOTABLE_TNOS)


def curated_numbers() -> dict[int, str]:
    """Permanent minor-planet number → curated object id, for routing SBDB rows."""
    out: dict[int, str] = {}
    for body in [*DWARF_PLANETS, *NOTABLE_TNOS]:
        spkid = body.get("spkid")
        if not spkid:
            continue
        n = int(spkid)
        n = n - 20000000 if n >= 20000000 else n - 2000000
        if n > 0:
            out[n] = body["id"]
    return out
