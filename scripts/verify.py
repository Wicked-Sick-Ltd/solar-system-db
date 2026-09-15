"""Sanity checks against data/solar_system.sqlite.

Exits non-zero on any failure so CI can fail fast.
"""
from __future__ import annotations

import sys

from common import DB_PATH, connect


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"OK    {msg}")


def main() -> int:
    if not DB_PATH.exists():
        print(f"FATAL: {DB_PATH} does not exist")
        return 2

    conn = connect()
    failures: list[str] = []

    # ---- structural integrity --------------------------------------------
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        for r in fk_errors:
            failures.append(f"orphan FK: {tuple(r)}")
            fail(f"orphan FK: {tuple(r)}")
    else:
        ok("no foreign-key violations")

    # ---- row counts -------------------------------------------------------
    counts = {r["object_type"]: r["n"] for r in
              conn.execute("SELECT * FROM v_object_counts")}
    total = sum(counts.values())
    print(f"      total objects: {total}")
    for t, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"        {t:28s} {n}")

    # Expectations
    expectations: list[tuple[str, int, str]] = [
        ("planet",            8,    "exactly 8 planets"),
        ("star",              1,    "the Sun"),
    ]
    for object_type, expected, label in expectations:
        actual = counts.get(object_type, 0)
        if actual != expected:
            failures.append(f"{label}: expected {expected}, got {actual}")
            fail(f"{label}: expected {expected}, got {actual}")
        else:
            ok(f"{label}: {actual}")

    # Moon counts per parent
    earth_moons = conn.execute(
        "SELECT COUNT(*) FROM objects WHERE object_type='moon' AND parent_id='planet-earth'"
    ).fetchone()[0]
    if earth_moons != 1:
        failures.append(f"Earth should have 1 moon, has {earth_moons}")
        fail(f"Earth should have 1 moon, has {earth_moons}")
    else:
        ok("Earth has 1 moon")

    jupiter_moons = conn.execute(
        "SELECT COUNT(*) FROM objects WHERE object_type='moon' AND parent_id='planet-jupiter'"
    ).fetchone()[0]
    if jupiter_moons < 70:
        failures.append(f"Jupiter should have >70 moons in catalogue, has {jupiter_moons}")
        fail(f"Jupiter should have >70 moons, has {jupiter_moons}")
    else:
        ok(f"Jupiter has {jupiter_moons} moons")

    saturn_moons = conn.execute(
        "SELECT COUNT(*) FROM objects WHERE object_type='moon' AND parent_id='planet-saturn'"
    ).fetchone()[0]
    if saturn_moons < 50:
        failures.append(f"Saturn should have >50 moons in catalogue, has {saturn_moons}")
        fail(f"Saturn should have >50 moons, has {saturn_moons}")
    else:
        ok(f"Saturn has {saturn_moons} moons")

    # Dwarf planets
    dp = conn.execute(
        "SELECT COUNT(*) FROM objects WHERE object_type='dwarf_planet'"
    ).fetchone()[0]
    if dp != 5:
        failures.append(f"expected 5 IAU dwarf planets, got {dp}")
        fail(f"expected 5 IAU dwarf planets, got {dp}")
    else:
        ok("5 IAU dwarf planets")

    # Every IAU dwarf planet needs orbital elements, else /positions 404s and
    # the orrery silently drops it (Pluto was missing until 2026-09).
    missing = [r[0] for r in conn.execute(
        """
        SELECT o.name FROM objects o
        LEFT JOIN orbital_elements oe ON oe.object_id = o.id
        WHERE o.object_type='dwarf_planet' AND oe.semi_major_axis_au IS NULL
        ORDER BY o.name
        """
    ).fetchall()]
    if missing:
        failures.append(f"dwarf planets without orbital elements: {', '.join(missing)}")
        fail(f"dwarf planets without orbital elements: {', '.join(missing)}")
    else:
        ok("all IAU dwarf planets have orbital elements")

    # Halley (the comet, not asteroid 2688)
    halley = conn.execute(
        """
        SELECT o.name, oe.orbital_period_days
        FROM objects o JOIN orbital_elements oe ON oe.object_id = o.id
        WHERE o.object_type='comet'
          AND (o.designation LIKE '1P/Halley%' OR o.id='comet-1p-halley')
        """
    ).fetchone()
    if halley is None:
        failures.append("Halley's comet not found")
        fail("Halley's comet not found")
    else:
        period_y = (halley["orbital_period_days"] or 0) / 365.25
        if not (60 <= period_y <= 90):
            failures.append(f"Halley period unexpectedly {period_y:.1f} y")
            fail(f"Halley period {period_y:.1f} y outside 60–90")
        else:
            ok(f"Halley orbital period {period_y:.1f} y")

    # Rings
    saturn_rings = conn.execute(
        "SELECT COUNT(*) FROM rings WHERE parent_id='planet-saturn'"
    ).fetchone()[0]
    if saturn_rings < 7:
        failures.append(f"Saturn rings: expected >=7, got {saturn_rings}")
        fail(f"Saturn rings: expected >=7, got {saturn_rings}")
    else:
        ok(f"Saturn rings: {saturn_rings}")

    # Property tables aren't empty
    for tbl in ("orbital_elements", "physical_properties", "visual_properties"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        if n == 0:
            failures.append(f"{tbl} is empty")
            fail(f"{tbl} is empty")
        else:
            ok(f"{tbl}: {n} rows")

    # Earth orbital plausible
    earth = conn.execute(
        "SELECT semi_major_axis_au FROM orbital_elements WHERE object_id='planet-earth'"
    ).fetchone()
    if earth and 0.99 <= (earth["semi_major_axis_au"] or 0) <= 1.01:
        ok(f"Earth a = {earth['semi_major_axis_au']:.5f} AU")
    else:
        failures.append("Earth semi-major axis out of range")
        fail("Earth semi-major axis out of range")

    print()
    if failures:
        print(f"VERIFY FAILED — {len(failures)} issue(s)")
        return 1
    print("VERIFY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
