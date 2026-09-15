"""Full rebuild of data/solar_system.sqlite from upstream sources.

Sources (all public-domain / freely redistributable):
  * NASA Planetary Fact Sheets — hardcoded in seed_major.py
  * IAU-named moons — hardcoded in seed_moons.py
  * JPL SBDB Query API — bulk asteroid + comet pulls
  * JPL SBDB single-lookup API — orbital + physical for specific TNOs/dwarfs
  * IAU Minor Planet Center — periodic-comet list (via SBDB which mirrors)

Usage:
  python scripts/populate_initial.py                  # default scope
  python scripts/populate_initial.py --full           # include all named asteroids (~25k)
  python scripts/populate_initial.py --skip-asteroids # planets + moons + comets only
  python scripts/populate_initial.py --skip-net       # offline rebuild from seed only
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Any, Iterable

from common import (
    DB_PATH, PUBLISH_PATH, SBDB_QUERY_URL, SBDB_LOOKUP_URL,
    add_classification, add_source, asteroid_id, comet_id, connect,
    fetch_json, publish, tno_id, upsert_object, upsert_row,
)
from seed_major import (
    DWARF_PLANETS, EARTH_MOONS, JUPITER_GALILEAN, MARS_MOONS, NEPTUNE_MAJOR,
    NOTABLE_COMETS, NOTABLE_TNOS, OTHER_DWARF_MOONS, PLANETS, PLUTO_MOONS,
    RINGS, SATURN_MAJOR, SUN, URANUS_MAJOR,
)
from seed_moons import ALL_MOONS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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

    # Sun
    write_major_body(conn, SUN, "star")
    counts["star"] = 1

    # Planets
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

    # Rings
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


# ---------------------------------------------------------------------------
# Stage 2: bulk asteroid pull from JPL SBDB
# ---------------------------------------------------------------------------
SBDB_FIELDS = [
    "spkid", "full_name", "name", "pdes", "kind",
    "a", "e", "i", "om", "w", "ma", "per", "epoch",
    "H", "diameter", "albedo", "rot_per", "extent",
    "class", "neo", "pha", "moid",
]


def _sbdb_query(params: dict, label: str, expected_min: int = 100) -> list[dict]:
    """Run an SBDB Query and return rows as dicts keyed on field name."""
    print(f"  → SBDB query: {label}")
    qparams = {
        "fields": ",".join(SBDB_FIELDS),
        "full-prec": "true",
        **params,
    }
    data = fetch_json(SBDB_QUERY_URL, params=qparams, timeout=120)
    fields = data.get("fields", [])
    rows = data.get("data", [])
    print(f"    received {len(rows)} rows (expected_min={expected_min})")
    if len(rows) < expected_min:
        print(f"    WARN: fewer rows than expected for {label}")
    return [dict(zip(fields, row)) for row in rows]


def _write_small_body(conn, row: dict, default_type: str) -> str:
    """Insert one SBDB row as an object + its properties. Returns id."""
    spkid = row.get("spkid")
    pdes = row.get("pdes")
    name = (row.get("name") or "").strip() or None
    full_name = (row.get("full_name") or "").strip() or None
    designation = full_name or pdes

    display = name or pdes or full_name or f"SPKID {spkid}"
    obj_id = asteroid_id(spkid, name)

    kind = (row.get("kind") or "").lower()
    obj_type = default_type
    is_neo = (row.get("neo") in (True, "Y", "y", 1, "1"))
    is_pha = (row.get("pha") in (True, "Y", "y", 1, "1"))

    upsert_object(
        conn,
        id=obj_id,
        name=display,
        designation=designation,
        object_type=obj_type,
    )

    # Orbital
    a = safe_float(row.get("a"))
    e = safe_float(row.get("e"))
    per_days = safe_float(row.get("per"))
    perihelion = (a * (1 - e)) if (a is not None and e is not None) else None
    aphelion = (a * (1 + e)) if (a is not None and e is not None) else None
    oe = {
        "epoch": str(row.get("epoch")) if row.get("epoch") is not None else None,
        "epoch_jd": safe_float(row.get("epoch")),
        "frame": "J2000",
        "centre": "Sun",
        "semi_major_axis_au": a,
        "eccentricity": e,
        "inclination_deg": safe_float(row.get("i")),
        "longitude_ascending_node_deg": safe_float(row.get("om")),
        "argument_periapsis_deg": safe_float(row.get("w")),
        "mean_anomaly_deg": safe_float(row.get("ma")),
        "orbital_period_days": per_days,
        "perihelion_au": perihelion,
        "aphelion_au": aphelion,
    }
    upsert_row(conn, "orbital_elements", obj_id, oe)

    # Physical
    diam = safe_float(row.get("diameter"))
    radius = diam / 2.0 if diam else None
    upsert_row(conn, "physical_properties", obj_id, {
        "radius_km": radius,
        "rotation_period_hours": safe_float(row.get("rot_per")),
    })

    # Visual
    upsert_row(conn, "visual_properties", obj_id, {
        "geometric_albedo": safe_float(row.get("albedo")),
        "absolute_magnitude_h": safe_float(row.get("H")),
        "spectral_type": row.get("class") or None,
    })

    # Classifications
    if is_neo:
        add_classification(conn, obj_id, "NEO")
    if is_pha:
        add_classification(conn, obj_id, "PHA")
    cls = (row.get("class") or "").upper()
    if cls == "MBA":
        add_classification(conn, obj_id, "MBA")
    elif cls in {"TJN", "JFC", "JFc"}:
        add_classification(conn, obj_id, "Trojan" if "TJN" in cls else "JFC")
    elif cls in {"CEN"}:
        add_classification(conn, obj_id, "Centaur")
    elif cls in {"TNO", "KBO"}:
        add_classification(conn, obj_id, "TNO")
    if name:
        add_classification(conn, obj_id, "Named")

    add_source(conn, object_id=obj_id, table_name="orbital_elements",
               source_name="JPL SBDB", source_url="https://ssd-api.jpl.nasa.gov/sbdb.api")
    return obj_id


def populate_asteroids(conn, full: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {"asteroid": 0, "neo": 0, "pha": 0,
                              "trojan": 0, "hilda": 0, "comet": 0}

    seen_ids: set[str] = set()

    # 1) All named asteroids (cdata: name not null)
    # 2) Plus all bright (H <= 14) unnamed — bounded set of significant bodies
    # 3) Plus all NEOs   — pulled via sb-group=neo
    # 4) Plus all PHAs   — via sb-group=pha
    # 5) Plus Trojans / Hildas via class filter
    # Targeted queries chosen to total ~12–14k rows after de-dup.
    # `--full` removes the H caps for the much larger catalogue.
    H_NAMED = "26" if full else "12"   # H<12 → ~2.5k named, H<26 → all named
    H_TROJAN = "26" if full else "13"
    H_TNO = "26" if full else "7"
    queries: list[tuple[dict, str, int]] = [
        # Named asteroids (the "household-name" set)
        ({"sb-kind": "a",
          "sb-cdata": json.dumps({"AND": ["name|DF", f"H|LT|{H_NAMED}"]})},
         f"named asteroids (H<{H_NAMED})", 500),
        # All PHAs (small and important)
        ({"sb-kind": "a", "sb-group": "pha"},
         "PHAs (all)", 1000),
        # Bright NEOs
        ({"sb-kind": "a", "sb-group": "neo",
          "sb-cdata": json.dumps({"AND": ["H|LT|19"]})},
         "NEOs (H<19)", 1000),
        # Jupiter Trojans (bright)
        ({"sb-kind": "a",
          "sb-cdata": json.dumps({"AND": ["class|EQ|TJN", f"H|LT|{H_TROJAN}"]})},
         f"Jupiter Trojans (H<{H_TROJAN})", 500),
        # Hildas — class HIL returns 0 from SBDB, so filter by orbit instead
        ({"sb-kind": "a",
          "sb-cdata": json.dumps({"AND": ["a|GE|3.7", "a|LE|4.2", "H|LT|13"]})},
         "Hildas (a 3.7–4.2 AU, H<13)", 50),
        # Centaurs (small set, take all)
        ({"sb-kind": "a",
          "sb-cdata": json.dumps({"AND": ["class|EQ|CEN"]})},
         "Centaurs", 50),
        # Bright TNOs (the dwarf-planet-sized notable end)
        ({"sb-kind": "a",
          "sb-cdata": json.dumps({"AND": ["class|EQ|TNO", f"H|LT|{H_TNO}"]})},
         f"TNOs (H<{H_TNO})", 100),
    ]

    for params, label, expected in queries:
        try:
            rows = _sbdb_query(params, label, expected_min=expected)
        except Exception as e:
            print(f"    ! SBDB query failed for {label}: {e}")
            continue

        for row in rows:
            cls = (row.get("class") or "").upper()
            default_type = "asteroid"
            if cls == "TNO":
                default_type = "tno"
            elif cls == "CEN":
                default_type = "centaur"
            try:
                obj_id = _write_small_body(conn, row, default_type=default_type)
            except Exception as e:
                print(f"    ! failed to insert SPKID {row.get('spkid')}: {e}")
                continue

            if obj_id in seen_ids:
                continue
            seen_ids.add(obj_id)
            counts["asteroid"] += 1
            if row.get("neo") in (True, "Y", "y", 1, "1"):
                counts["neo"] += 1
            if row.get("pha") in (True, "Y", "y", 1, "1"):
                counts["pha"] += 1
            if cls == "TJN":
                counts["trojan"] += 1
            if cls == "HIL":
                counts["hilda"] += 1
        conn.commit()
        # Be gentle with the API between bulk pulls
        time.sleep(1.0)

    return counts


# ---------------------------------------------------------------------------
# Stage 3: comets (all numbered/periodic)
# ---------------------------------------------------------------------------
def populate_comets(conn) -> int:
    fields = ["spkid", "full_name", "name", "pdes", "kind",
              "q", "e", "i", "om", "w", "tp", "per", "per_y", "M1", "epoch",
              "class", "moid"]
    params = {
        "fields": ",".join(fields),
        "sb-kind": "c",
        "full-prec": "true",
    }
    print("  → SBDB query: all comets")
    try:
        data = fetch_json(SBDB_QUERY_URL, params=params, timeout=120)
    except Exception as e:
        print(f"    ! comet query failed: {e}")
        return 0
    fnames = data.get("fields", [])
    rows = [dict(zip(fnames, r)) for r in data.get("data", [])]
    print(f"    received {len(rows)} comets")
    n = 0
    for row in rows:
        pdes = row.get("pdes") or row.get("full_name")
        if not pdes:
            continue
        name = (row.get("name") or "").strip() or pdes
        full_name = (row.get("full_name") or "").strip() or None
        obj_id = comet_id(full_name or pdes)
        upsert_object(
            conn,
            id=obj_id, name=name,
            designation=full_name or pdes,
            object_type="comet",
        )
        q = safe_float(row.get("q"))
        e = safe_float(row.get("e"))
        a = (q / (1 - e)) if (q is not None and e is not None and e < 1) else None
        per_days = safe_float(row.get("per"))
        if per_days is None:
            per_y = safe_float(row.get("per_y"))
            if per_y is not None:
                per_days = per_y * 365.25
        aphelion = (a * (1 + e)) if (a is not None and e is not None) else None
        # Use time-of-perihelion-passage (tp) as the epoch — at tp, mean anomaly
        # is 0 by definition. This makes Kepler propagation trivial.
        tp_jd = safe_float(row.get("tp"))
        if tp_jd is not None:
            epoch_jd = tp_jd
            epoch_str = f"JD {tp_jd:.5f} (perihelion)"
            mean_anomaly = 0.0
        else:
            epoch_jd = safe_float(row.get("epoch"))
            epoch_str = str(row.get("epoch")) if row.get("epoch") else None
            mean_anomaly = None
        upsert_row(conn, "orbital_elements", obj_id, {
            "epoch": epoch_str, "epoch_jd": epoch_jd,
            "frame": "J2000", "centre": "Sun",
            "semi_major_axis_au": a, "eccentricity": e,
            "inclination_deg": safe_float(row.get("i")),
            "longitude_ascending_node_deg": safe_float(row.get("om")),
            "argument_periapsis_deg": safe_float(row.get("w")),
            "mean_anomaly_deg": mean_anomaly,
            "orbital_period_days": per_days,
            "perihelion_au": q, "aphelion_au": aphelion,
        })
        upsert_row(conn, "visual_properties", obj_id, {
            "absolute_magnitude_h": safe_float(row.get("M1")),
            "spectral_type": row.get("class") or None,
        })
        upsert_row(conn, "physical_properties", obj_id, {})
        add_source(conn, object_id=obj_id, table_name="orbital_elements",
                   source_name="JPL SBDB (comets)",
                   source_url="https://ssd-api.jpl.nasa.gov/sbdb.api")
        n += 1
    conn.commit()
    return n


def sbdb_orbital_elements(data: dict) -> dict[str, Any]:
    """Map an SBDB single-object lookup payload onto our orbital_elements columns."""
    orbit = data.get("orbit") or {}
    elem = {item["name"]: item.get("value") for item in (orbit.get("elements") or [])}
    a = safe_float(elem.get("a"))
    e = safe_float(elem.get("e"))
    return {
        "epoch": str(orbit.get("epoch")) if orbit.get("epoch") else None,
        "epoch_jd": safe_float(orbit.get("epoch")),
        "frame": "J2000", "centre": "Sun",
        "semi_major_axis_au": a, "eccentricity": e,
        "inclination_deg": safe_float(elem.get("i")),
        "longitude_ascending_node_deg": safe_float(elem.get("om")),
        "argument_periapsis_deg": safe_float(elem.get("w")),
        "mean_anomaly_deg": safe_float(elem.get("ma")),
        "orbital_period_days": safe_float(elem.get("per")),
        "perihelion_au": safe_float(elem.get("q")) or ((a * (1 - e)) if a and e is not None else None),
        "aphelion_au": safe_float(elem.get("ad")) or ((a * (1 + e)) if a and e is not None else None),
    }


# ---------------------------------------------------------------------------
# Stage 1b: dwarf planets — orbital elements from SBDB
#
# The curated seed carries physical + visual blocks for the dwarf planets but
# no orbital elements, so without this stage /positions/{id} 404s for Pluto,
# Ceres, Eris … and the orrery silently omits them. Only orbital_elements is
# written here; the curated NASA fact-sheet physical/visual values stay
# authoritative.
# ---------------------------------------------------------------------------
def enrich_dwarf_planets(conn) -> int:
    n = 0
    for body in DWARF_PLANETS:
        spkid = body.get("spkid")
        if not spkid:
            continue
        try:
            data = fetch_json(SBDB_LOOKUP_URL, params={"sstr": str(spkid), "full-prec": "true"}, timeout=30)
        except Exception as e:
            print(f"    ! SBDB lookup failed for {body['name']}: {e}")
            continue
        oe = sbdb_orbital_elements(data)
        if oe.get("semi_major_axis_au") is None:
            print(f"    ! SBDB returned no orbit for {body['name']} (spkid {spkid})")
            continue
        upsert_row(conn, "orbital_elements", body["id"], oe)
        add_source(conn, object_id=body["id"], table_name="orbital_elements",
                   source_name="JPL SBDB (lookup)",
                   source_url=f"https://ssd-api.jpl.nasa.gov/sbdb.api?sstr={spkid}")
        n += 1
        time.sleep(0.3)
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Stage 4: notable TNOs (per-object lookups so we get full data)
# ---------------------------------------------------------------------------
def populate_notable_tnos(conn) -> int:
    n = 0
    for body in NOTABLE_TNOS:
        upsert_object(
            conn,
            id=body["id"], name=body["name"], designation=body.get("designation"),
            object_type=body["object_type"], parent_id="sun",
            discoverer=body.get("discoverer"), discovery_date=body.get("discovery_date"),
            wikipedia_url=body.get("wikipedia_url"),
        )
        spkid = body.get("spkid")
        if not spkid:
            continue
        try:
            data = fetch_json(SBDB_LOOKUP_URL, params={"sstr": str(spkid), "full-prec": "true"}, timeout=30)
        except Exception as e:
            print(f"    ! SBDB lookup failed for {body['name']}: {e}")
            continue
        upsert_row(conn, "orbital_elements", body["id"], sbdb_orbital_elements(data))
        phys = data.get("phys_par") or []
        phys_map = {item["name"]: item.get("value") for item in phys}
        upsert_row(conn, "physical_properties", body["id"], {
            "radius_km": (safe_float(phys_map.get("diameter")) / 2.0
                          if phys_map.get("diameter") else None),
            "rotation_period_hours": safe_float(phys_map.get("rot_per")),
        })
        upsert_row(conn, "visual_properties", body["id"], {
            "absolute_magnitude_h": safe_float(phys_map.get("H")),
            "geometric_albedo": safe_float(phys_map.get("albedo")),
        })
        add_source(conn, object_id=body["id"], table_name="orbital_elements",
                   source_name="JPL SBDB (lookup)",
                   source_url=f"https://ssd-api.jpl.nasa.gov/sbdb.api?sstr={spkid}")
        n += 1
        time.sleep(0.3)
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--full", action="store_true",
                   help="Don't cap unnamed asteroids (pulls more rows)")
    p.add_argument("--skip-asteroids", action="store_true")
    p.add_argument("--skip-net", action="store_true",
                   help="Offline rebuild — major bodies + seed only")
    p.add_argument("--fresh", action="store_true",
                   help="Drop and recreate all tables before populating")
    args = p.parse_args(argv)

    print(f"Building {DB_PATH} …")

    if args.fresh and DB_PATH.exists():
        # Truncate rather than unlink — some FS mounts disallow delete.
        try:
            DB_PATH.unlink()
        except (OSError, PermissionError):
            DB_PATH.write_bytes(b"")
        for ext in ("-journal", "-wal", "-shm"):
            sidecar = DB_PATH.with_suffix(DB_PATH.suffix + ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except (OSError, PermissionError):
                    sidecar.write_bytes(b"")
        print("  cleared previous DB")

    conn = connect(create=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT INTO build_meta (started_at, mode) VALUES (?, ?)",
        (started, "full"),
    )
    build_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    total = {}

    print("Stage 1: major bodies (sun, planets, dwarfs, curated moons, rings)")
    total.update(populate_major_bodies(conn))
    conn.commit()

    if not args.skip_net:
        print("Stage 1b: dwarf-planet orbital elements (SBDB lookups)")
        total["dwarf_planet_orbits"] = enrich_dwarf_planets(conn)

        print("Stage 2: small bodies (SBDB asteroids / NEOs / PHAs / Trojans …)")
        if not args.skip_asteroids:
            total.update(populate_asteroids(conn, full=args.full))

        print("Stage 3: comets")
        total["comet"] = populate_comets(conn)

        print("Stage 4: notable TNOs & centaurs")
        total["tno_notable"] = populate_notable_tnos(conn)
    else:
        print("(skipping network stages)")

    # Final book-keeping
    row_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    conn.execute(
        "UPDATE build_meta SET finished_at = ?, row_count = ?, notes = ? WHERE id = ?",
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row_count,
         json.dumps(total), build_id),
    )
    conn.commit()

    conn.close()
    # If we built in a scratch path (SSDB_BUILD_PATH), publish to data/
    published = publish()
    print()
    print("=" * 60)
    print(f"Done. {row_count} rows in objects.")
    for k, v in total.items():
        print(f"  {k:24s} {v}")
    print(f"Build DB: {DB_PATH}  ({DB_PATH.stat().st_size / 1024:.1f} KiB)")
    print(f"Published: {published}  ({published.stat().st_size / 1024:.1f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
