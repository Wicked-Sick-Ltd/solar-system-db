"""Repeatable local read-query profile; synthetic enlargement uses a disposable copy.

No HTTP, live load generation, OS cache eviction, or writes to the input catalogue.
Run with PYTHONPATH=. and a fixture built by scripts/build_full.py --offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import subprocess
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

from solar_db import SolarDB

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
        return checksum.hexdigest()


def synthetic_copy(source: Path, target: Path, objects: int, hosts: int):
    with (
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(target) as conn,
    ):
        original.backup(conn)
        conn.execute("PRAGMA foreign_keys=ON")
        for i in range(objects):
            identity = f"perf-object-{i:06d}"
            kind = "comet" if i % 100 == 0 else "tno" if i % 100 == 1 else "asteroid"
            conn.execute(
                "INSERT INTO objects (id,name,designation,object_type,discovery_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    identity,
                    "" if i % 11 == 0 else f"Synthetic sample {i:06d}",
                    f"SYN{i:06d}",
                    kind,
                    f"{1980 + i % 45}-01-01",
                    "2000-01-01",
                    "2000-01-01",
                ),
            )
            conn.execute(
                "INSERT INTO orbital_elements (object_id,semi_major_axis_au,eccentricity,orbit_class_code,moid_au,condition_code,updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    identity,
                    None if i % 17 == 0 else 0.5 + (i % 997) / 10,
                    (i % 95) / 100,
                    "MBA",
                    (i % 100) / 1000,
                    i % 10,
                    "2000-01-01",
                ),
            )
            conn.execute(
                "INSERT INTO physical_properties (object_id,radius_km,updated_at) VALUES (?,?,?)",
                (identity, None if i % 13 == 0 else (i % 200) / 10, "2000-01-01"),
            )
            conn.execute(
                "INSERT INTO visual_properties (object_id,absolute_magnitude_h,updated_at) VALUES (?,?,?)",
                (identity, i % 25, "2000-01-01"),
            )
            for label, divisor in [("NEO", 19), ("PHA", 97)]:
                if i % divisor == 0:
                    conn.execute(
                        "INSERT INTO classifications VALUES (?,?)", (identity, label)
                    )
        for i in range(hosts):
            identity = f"perf-host-{i:06d}"
            distance = None if i % 10 == 0 else 1 + i % 2000
            conn.execute(
                """INSERT INTO exoplanet_hosts (id,name,ra_deg,dec_deg,distance_pc,x_pc,y_pc,z_pc,coordinate_source_planet,coordinate_metadata,source,retrieved_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity,
                    f"Synthetic host {i:06d}",
                    i % 360,
                    0,
                    distance,
                    distance,
                    0 if distance else None,
                    0 if distance else None,
                    "synthetic",
                    "{}",
                    "profiling-only",
                    "2000-01-01",
                ),
            )
            for planet in ("b", "c"):
                conn.execute(
                    """INSERT INTO exoplanets (id,name,host_id,discovery_method,source,source_url,retrieved_at,source_data) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        f"{identity}-{planet}",
                        f"Synthetic host {i:06d} {planet}",
                        identity,
                        "Transit" if i % 3 else "Radial Velocity",
                        "profiling-only",
                        "https://example.invalid",
                        "2000-01-01",
                        "{}",
                    ),
                )
        conn.execute(
            "INSERT INTO objects_fts (id,name,designation,alt) SELECT id,name,coalesce(designation,''),'' FROM objects WHERE id LIKE 'perf-object-%'"
        )
        conn.execute("ANALYZE")
        conn.commit()


class ProfileDB(SolarDB):
    capture = False

    @contextmanager
    def _conn(self):
        with super()._conn() as conn:
            if self.capture:
                self.connections += 1
                conn.set_trace_callback(self.statements.append)
                conn.set_progress_handler(self.progress, 1000)
            yield conn

    @contextmanager
    def _snapshot_conn(self):
        with super()._snapshot_conn() as conn:
            if self.capture:
                self.connections += 1
                conn.set_trace_callback(self.statements.append)
                conn.set_progress_handler(self.progress, 1000)
            yield conn

    def progress(self):
        self.steps += 1000
        return 0


def measure(db, function, repeats):
    if hasattr(db, "_table_cache"):
        del db._table_cache
    started = time.perf_counter()
    result = function()
    first = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    db.capture, db.statements, db.steps, db.connections = True, [], 0, 0
    tracemalloc.start()
    function()
    _, memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    db.capture = False
    statements = [
        s
        for s in db.statements
        if s.lstrip().upper().startswith("SELECT") and not s.lstrip().startswith("--")
    ]
    plans = []
    with db._conn() as conn:
        for statement in statements:
            plans.append(
                {
                    "sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
                    "plan": [
                        r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + statement)
                    ],
                }
            )
    warm_connections, warm_steps = db.connections, db.steps
    if hasattr(db, "_table_cache"):
        del db._table_cache
    db.capture, db.statements, db.steps, db.connections = True, [], 0, 0
    function()
    db.capture = False
    cold_selects = sum(s.lstrip().upper().startswith("SELECT") for s in db.statements)
    payload = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "first_call_ms": round(first, 3),
        "warm_p50_ms": round(statistics.median(samples), 3),
        "warm_p95_ms": round(sorted(samples)[math.ceil(0.95 * len(samples)) - 1], 3),
        "warm_samples_ms": [round(s, 3) for s in samples],
        "connections": warm_connections,
        "cold_connections": db.connections,
        "cold_select_statements": cold_selects,
        "select_statements": len(statements),
        "vm_steps_lower_bound": warm_steps,
        "python_peak_bytes": memory,
        "json_bytes": len(payload),
        "result_sha256": hashlib.sha256(payload).hexdigest(),
        "result_rows": len(
            result if isinstance(result, list) else result.get("results", [])
        ),
        "plans": plans,
    }


def run(path, repeats, objects):
    db = ProfileDB(path)
    midpoint = f"perf-object-{objects // 2:06d}" if objects else "asteroid-100"
    cases = {
        "search_exact": lambda: db.search("Saturn"),
        "search_absent": lambda: db.search("absent-perf-unique-token"),
        "objects_planets": lambda: db.find_objects(object_type="planet"),
        "objects_offset_first": lambda: db.find_objects(limit=50),
        "objects_offset_1000": lambda: db.find_objects(limit=50, offset=1000),
        "objects_keyset_first": lambda: db.find_objects(after="", limit=50),
        "objects_keyset_middle": lambda: db.find_objects(after=midpoint, limit=50),
        "objects_filtered_keyset": lambda: db.find_objects(
            after=midpoint, neo=True, min_radius_km=5, limit=50
        ),
        "starter_bright": lambda: db.list_starter_targets(
            family="bright_star", limit=24
        ),
        "exoplanets_first": lambda: db.list_exoplanets(limit=50),
        "exoplanets_filtered_total": lambda: db.list_exoplanets(
            discovery_method="Transit", max_distance_pc=100, limit=50
        ),
        "galaxy_map": lambda: db.galaxy_map(limit=10000),
        "galaxy_map_distance": lambda: db.galaxy_map(max_distance_pc=100, limit=10000),
    }
    with db._conn() as conn:
        counts = {
            name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ["objects", "exoplanets", "exoplanet_hosts", "starter_targets"]
        }
    return {
        "catalogue_sha256": digest(path),
        "catalogue_bytes": path.stat().st_size,
        "counts": counts,
        "cases": {
            name: measure(db, function, repeats) for name, function in cases.items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--synthetic-objects", type=int, default=0)
    parser.add_argument("--synthetic-hosts", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=11)
    args = parser.parse_args()
    if (
        not 0 <= args.synthetic_objects <= 200000
        or not 0 <= args.synthetic_hosts <= 10000
        or not 3 <= args.repeats <= 51
    ):
        parser.error("Bounds: 0–200000 objects, 0–10000 hosts, 3–51 repeats")
    if args.output.resolve() == args.database.resolve() or (
        args.output.exists() and args.output.samefile(args.database)
    ):
        parser.error("The report must not overwrite the input catalogue")
    source_hash = digest(args.database)
    report = {
        "method": "First call clears the per-instance schema inventory; repeated calls retain it. Every call opens read-only SQLite connections; OS cache is not evicted. Tracing/memory measured separately from timings.",
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "code_sha256": {
            name: digest(ROOT / name)
            for name in [
                "solar_db/data_access.py",
                "solar_db/exoplanets.py",
                "solar_db/starter_catalogues.py",
            ]
        },
        "input_sha256": source_hash,
        "synthetic_objects": args.synthetic_objects,
        "synthetic_hosts": args.synthetic_hosts,
        "repeats": args.repeats,
    }
    with tempfile.TemporaryDirectory(prefix="ssdb-query-profile-") as temporary:
        path = args.database
        if args.synthetic_objects or args.synthetic_hosts:
            path = Path(temporary) / "synthetic.sqlite"
            synthetic_copy(
                args.database, path, args.synthetic_objects, args.synthetic_hosts
            )
        report.update(run(path, args.repeats, args.synthetic_objects))
    if digest(args.database) != source_hash:
        raise RuntimeError("Input catalogue changed during the profile")
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {args.output}; input unchanged; {report['counts']}")
    for name, result in report["cases"].items():
        print(
            f"{name:28s} p50 {result['warm_p50_ms']:9.3f} ms  steps >= {result['vm_steps_lower_bound']}"
        )


if __name__ == "__main__":
    main()
