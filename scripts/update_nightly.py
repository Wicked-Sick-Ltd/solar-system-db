"""Incremental nightly refresh.

Strategy:
  * Major bodies don't change — re-applied from seed (idempotent upsert).
  * Asteroids/comets/TNOs — we re-query SBDB only for objects whose `epoch`
    (Julian Date of the stored elements) is older than 90 days, in chunks.
  * If the DB is empty (first run), we fall through to populate_initial.
  * The MPC named-asteroid list only grows on the order of dozens/month, so
    we also re-run the named-asteroid SBDB query once a week (Sunday) to pick
    up newly-named bodies.

Designed to be safe to run on a fresh checkout — if the DB is missing or
empty, it bootstraps with a full populate.
"""
from __future__ import annotations

import argparse
import os
import json
import sys
import time
from datetime import datetime, timezone

from common import (
    DB_PATH, SBDB_QUERY_URL, add_source, connect, fetch_json,
)
import populate_initial as full
from seed_major import NOTABLE_COMETS, NOTABLE_TNOS


def db_has_data(conn) -> bool:
    try:
        n = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        return n > 100
    except Exception:
        return False


def refresh_named_weekly(conn) -> int:
    """Once a week (Sunday UTC), refresh the named-asteroid list — catches new
    namings from the MPC."""
    if datetime.now(timezone.utc).weekday() != 6:  # Sunday
        return 0
    print("Sunday → refreshing named-asteroid list")
    params = {
        "sb-kind": "a",
        "sb-cdata": json.dumps({"AND": ["name|DF"]}),
        "fields": ",".join(full.SBDB_FIELDS),
        "full-prec": "true",
    }
    try:
        data = fetch_json(SBDB_QUERY_URL, params=params, timeout=120)
    except Exception as e:
        print(f"  ! refresh failed: {e}")
        return 0
    fields = data.get("fields", [])
    rows = [dict(zip(fields, r)) for r in data.get("data", [])]
    n_new = 0
    cur = conn.execute("SELECT id FROM objects").fetchall()
    seen = {row[0] for row in cur}
    for row in rows:
        # Re-insert covers updated elements; check for new spkids
        from common import asteroid_id
        obj_id = asteroid_id(row.get("spkid"), (row.get("name") or "").strip() or None)
        before = obj_id in seen
        full._write_small_body(conn, row, default_type="asteroid")
        if not before:
            n_new += 1
    conn.commit()
    print(f"  → {n_new} newly-named asteroids")
    return n_new


def refresh_stale_elements(conn, *, days_stale: int = 90,
                           batch_size: int = 200) -> int:
    """Re-pull SBDB orbital elements for objects whose `epoch` is older than
    `days_stale`. Uses the per-object lookup endpoint."""
    print(f"Refreshing stale orbital elements (>{days_stale} days)…")
    from common import SBDB_LOOKUP_URL, fetch_json
    cutoff = time.time() - days_stale * 86400
    stale = conn.execute(
        """
        SELECT o.id, o.designation
        FROM objects o
        JOIN orbital_elements oe ON oe.object_id = o.id
        WHERE o.object_type IN (
            'asteroid','comet','tno','centaur',
            'dwarf_planet','dwarf_planet_candidate'
        )
          AND (oe.updated_at IS NULL
               OR strftime('%s', oe.updated_at) < ?)
        ORDER BY oe.updated_at
        LIMIT ?
        """,
        (str(int(cutoff)), batch_size),
    ).fetchall()
    n = 0
    for row in stale:
        obj_id, designation = row["id"], row["designation"]
        if not designation:
            continue
        try:
            data = fetch_json(SBDB_LOOKUP_URL,
                              params={"sstr": designation, "full-prec": "true"},
                              timeout=30)
        except Exception as e:
            print(f"  ! lookup failed for {designation}: {e}")
            continue
        orbit = data.get("orbit") or {}
        elem = {item["name"]: item.get("value") for item in (orbit.get("elements") or [])}
        if not elem:
            continue
        from common import upsert_row
        def _f(x):
            try:
                return float(x) if x not in (None, "") else None
            except Exception:
                return None
        a, e = _f(elem.get("a")), _f(elem.get("e"))
        upsert_row(conn, "orbital_elements", obj_id, {
            "epoch": str(orbit.get("epoch")) if orbit.get("epoch") else None,
            "epoch_jd": _f(orbit.get("epoch")),
            "frame": "J2000", "centre": "Sun",
            "semi_major_axis_au": a, "eccentricity": e,
            "inclination_deg": _f(elem.get("i")),
            "longitude_ascending_node_deg": _f(elem.get("om")),
            "argument_periapsis_deg": _f(elem.get("w")),
            "mean_anomaly_deg": _f(elem.get("ma")),
            "orbital_period_days": _f(elem.get("per")),
            "perihelion_au": _f(elem.get("q")) or ((a * (1 - e)) if a and e is not None else None),
            "aphelion_au": _f(elem.get("ad")) or ((a * (1 + e)) if a and e is not None else None),
        })
        add_source(conn, object_id=obj_id, table_name="orbital_elements",
                   source_name="JPL SBDB (nightly refresh)",
                   source_url=f"https://ssd-api.jpl.nasa.gov/sbdb.api?sstr={designation}")
        n += 1
        time.sleep(0.25)
    conn.commit()
    print(f"  → refreshed {n} objects")
    return n


def main(argv: list[str] | None = None) -> int:
    # Retired 2026-09-15: nightly builds now run on the build host via
    # scripts/build_full.py and publish to object storage (see
    # docs/superpowers/specs/2026-09-15-full-catalogue-design.md). This shim
    # keeps the legacy GitHub Action harmless (no DB change → no PR) until
    # the workflow itself is removed.
    if os.environ.get("SSDB_LEGACY_NIGHTLY") != "1":
        print("update_nightly.py is retired — builds run via scripts/build_full.py on the build host.")
        return 0
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--bootstrap", action="store_true",
                   help="Run a full populate if the DB is empty (default).")
    p.add_argument("--days-stale", type=int, default=90)
    p.add_argument("--batch-size", type=int, default=200)
    args = p.parse_args(argv)

    conn = connect(create=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "INSERT INTO build_meta (started_at, mode) VALUES (?, ?)",
        (started, "nightly"),
    )
    build_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    if not db_has_data(conn):
        print("DB is empty — bootstrapping with a full populate")
        return full.main(["--fresh"])

    # Re-apply curated seed (idempotent)
    print("Reapplying major-body seed (idempotent)…")
    full.populate_major_bodies(conn)
    conn.commit()

    # Refresh stale element rows
    refresh_stale_elements(conn, days_stale=args.days_stale, batch_size=args.batch_size)

    # Sunday: pull new named asteroids
    refresh_named_weekly(conn)

    row_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    conn.execute(
        "UPDATE build_meta SET finished_at = ?, row_count = ? WHERE id = ?",
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row_count, build_id),
    )
    conn.commit()
    print(f"Nightly refresh complete. {row_count} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
