"""Sanity checks against data/solar_system.sqlite.

Exits non-zero on any failure so CI can fail fast.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from common import DB_PATH, connect


def fail(msg: str) -> None:
    print(f"FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"OK    {msg}")


def main() -> int:
    if not DB_PATH.exists():
        # No catalogue on this checkout (it is downloaded or built, never committed):
        # verify the offline build instead so CI still exercises the whole pipeline.
        import subprocess
        import sys as _sys
        import tempfile
        with tempfile.TemporaryDirectory(prefix="solar-verify-") as scratch_dir:
            scratch = Path(scratch_dir) / "solar_system.sqlite"
            # flush=True: CI captures build_full.py's child-process output separately
            # from this print, so without it this line can appear after the build log.
            print(f"{DB_PATH} does not exist — building the offline fixture catalogue at {scratch} to verify",
                  flush=True)
            env = {**os.environ, "SSDB_BUILD_PATH": str(scratch), "SSDB_NO_PUBLISH": "1"}
            subprocess.run([_sys.executable, str(Path(__file__).resolve().parent / "build_full.py"),
                            "--fresh", "--offline", "--no-vacuum"], check=True, env=env)
            os.environ["SSDB_BUILD_PATH"] = str(scratch)
            rc = subprocess.run([_sys.executable, __file__], env=os.environ).returncode
        return rc

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
        WHERE o.object_type='dwarf_planet' AND (
            oe.semi_major_axis_au IS NULL
            OR oe.eccentricity IS NULL
            OR oe.inclination_deg IS NULL
            OR oe.longitude_ascending_node_deg IS NULL
            OR oe.argument_periapsis_deg IS NULL
            OR oe.mean_anomaly_deg IS NULL
            OR oe.epoch_jd IS NULL
        )
        ORDER BY o.name
        """
    ).fetchall()]
    if missing:
        failures.append(f"dwarf planets without orbital elements: {', '.join(missing)}")
        fail(f"dwarf planets without orbital elements: {', '.join(missing)}")
    else:
        ok("all IAU dwarf planets have orbital elements")

    # ---- schema v2 invariants (skipped on v1 files) ------------------------
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 2:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ingest_sbdb import ORBIT_CLASS_CODES
        placeholders = ",".join("?" for _ in ORBIT_CLASS_CODES)
        bad = conn.execute(f"SELECT COUNT(*) FROM visual_properties WHERE spectral_type IN ({placeholders})",
                           tuple(ORBIT_CLASS_CODES)).fetchone()[0]
        if bad:
            failures.append(f"{bad} rows carry an orbit class in spectral_type")
            fail(f"{bad} rows carry an orbit class in spectral_type")
        else:
            ok("spectral_type holds taxonomies only")
        no_class = conn.execute(
            "SELECT COUNT(*) FROM objects o JOIN orbital_elements oe ON oe.object_id=o.id "
            "WHERE o.object_type IN ('asteroid','comet','tno','centaur') "
            "AND oe.semi_major_axis_au IS NOT NULL AND oe.orbit_class_code IS NULL").fetchone()[0]
        if no_class:
            failures.append(f"{no_class} small bodies without orbit_class_code")
            fail(f"{no_class} small bodies without orbit_class_code")
        else:
            ok("every small body has an orbit class")
        n_des = conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0]
        n_fts = conn.execute("SELECT COUNT(*) FROM objects_fts").fetchone()[0]
        n_obj = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        if n_des == 0 or n_fts != n_obj:
            failures.append(f"designations={n_des}, fts={n_fts} vs objects={n_obj}")
            fail(f"designations={n_des}, fts={n_fts} vs objects={n_obj}")
        else:
            ok(f"designations {n_des}; FTS covers all {n_obj} objects")

    # ---- schema v3 invariants (meteor showers, IAU MDC) --------------------
    if conn.execute("PRAGMA user_version").fetchone()[0] >= 3:
        n_showers = conn.execute("SELECT COUNT(*) FROM meteor_showers").fetchone()[0]

        build_row = conn.execute(
            "SELECT mode, notes FROM build_meta ORDER BY id DESC LIMIT 1"
        ).fetchone()
        notes: dict = {}
        if build_row and build_row["notes"]:
            try:
                parsed = json.loads(build_row["notes"])
                if isinstance(parsed, dict):
                    notes = parsed
            except ValueError:
                notes = {}  # pre-#16 builds wrote free text here, not JSON
        showers_skipped = notes.get("showers") == "skipped"

        if showers_skipped and n_showers == 0:
            # A deliberate `--skip-showers` build: nothing to check, and an
            # empty table here is expected, not a failure.
            ok("meteor_showers: build_meta.notes says showers were skipped — shower checks skipped")
        else:
            # 41 rows offline, ~1,400 online — read the mode this file was
            # actually built with instead of a single floor covering both.
            floor = 30 if (build_row and build_row["mode"] == "full-offline") else 100
            if n_showers < floor:
                failures.append(f"meteor_showers: expected >={floor}, got {n_showers}")
                fail(f"meteor_showers: expected >={floor}, got {n_showers}")
            else:
                ok(f"meteor_showers: {n_showers} rows")

            dupes = conn.execute(
                "SELECT iau_no, ad_no, COUNT(*) AS n FROM meteor_showers GROUP BY iau_no, ad_no HAVING n > 1"
            ).fetchall()
            if dupes:
                failures.append(f"meteor_showers duplicate (iau_no, ad_no): {[tuple(r) for r in dupes]}")
                fail(f"meteor_showers duplicate (iau_no, ad_no): {[tuple(r) for r in dupes]}")
            else:
                ok("meteor_showers: no duplicate (iau_no, ad_no)")

            established = conn.execute(
                "SELECT COUNT(*) FROM meteor_showers WHERE status_label LIKE '%stablished%'"
            ).fetchone()[0]
            if established == 0:
                failures.append("meteor_showers: no status_label contains 'stablished'")
                fail("meteor_showers: no status_label contains 'stablished'")
            else:
                ok(f"meteor_showers: {established} rows with an 'established' status_label")

            working = conn.execute(
                "SELECT COUNT(*) FROM meteor_showers WHERE status_label LIKE '%working%'"
            ).fetchone()[0]
            if working == 0:
                failures.append("meteor_showers: no status_label contains 'working' (the MDC working-list legend)")
                fail("meteor_showers: no status_label contains 'working' (the MDC working-list legend)")
            else:
                ok(f"meteor_showers: {working} rows with a 'working' status_label")

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
