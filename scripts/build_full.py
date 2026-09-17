"""Build the complete catalogue from scratch.

    python scripts/build_full.py --fresh --online     # nightly, on the build host
    python scripts/build_full.py --fresh --offline    # from tests/fixtures, no network

Stages (spec §5.2): seed → SBDB bulk (all fields, all bodies) → designations/FTS
→ MPC discoveries → JPL close approaches → crawler tiering → ANALYZE/VACUUM →
verify → publish. Enrichment merge (§5.3) and satellite tables (PR 3) plug in
as further stages.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_cad  # noqa: E402
import ingest_enrichment  # noqa: E402
import ingest_factsheets  # noqa: E402
import ingest_mpc  # noqa: E402
import ingest_sats  # noqa: E402
import ingest_sbdb  # noqa: E402
import ingest_seed  # noqa: E402
from common import DB_PATH, ROOT, connect, publish  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _run_stage(label: str, fn) -> object:
    """Print label, run fn(), print its elapsed wall time, return its result.

    time.monotonic() (not time.time()) so the timing is immune to any
    wall-clock adjustment during a build that can run for well over an hour.
    """
    print(label)
    t = time.monotonic()
    result = fn()
    print(f"  … ({time.monotonic() - t:.1f}s)")
    return result


def _fresh_db() -> None:
    if DB_PATH.exists():
        try:
            DB_PATH.unlink()
        except (OSError, PermissionError):
            DB_PATH.write_bytes(b"")
    for ext in ("-journal", "-wal", "-shm"):
        side = DB_PATH.with_suffix(DB_PATH.suffix + ext)
        if side.exists():
            side.unlink()


def stage_seed(conn) -> dict[str, int]:
    counts = ingest_seed.populate_major_bodies(conn)
    counts["tno_notable"] = ingest_seed.seed_notable_tnos(conn)
    conn.commit()
    return counts


def stage_factsheets(conn) -> dict[str, int]:
    return ingest_factsheets.write_factsheets(conn, ingest_factsheets.load_seed())


def stage_satellites(conn, *, offline: bool) -> dict[str, int]:
    if offline:
        elem = (FIXTURES / "jpl_sats_elem.html").read_text()
        phys = (FIXTURES / "jpl_sats_phys_par.html").read_text()
    else:
        elem, phys = ingest_sats.fetch_pages()
    return ingest_sats.write_satellites(conn, ingest_sats.parse_elements(elem), ingest_sats.parse_physical(phys))


def stage_sbdb(conn, *, offline: bool, page: int) -> dict[str, int]:
    curated = ingest_seed.curated_numbers()
    total: dict[str, int] = {}
    if offline:
        pages = [ingest_sbdb.load_fixture(FIXTURES / "sbdb_asteroids.json"),
                 ingest_sbdb.load_fixture(FIXTURES / "sbdb_comets.json")]
    else:
        pages = (p for kind in ("a", "c") for p in ingest_sbdb.iter_bulk(kind, page=page))
    for fields, rows in pages:
        counts = ingest_sbdb.write_mapped(conn, ingest_sbdb.map_all(fields, rows, curated=curated))
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
        print(f"    +{sum(counts.values())} rows ({dict(counts)})")
    return total


def stage_mpc(conn, *, offline: bool) -> dict[str, int]:
    if offline:
        records = ingest_mpc.parse_lines((FIXTURES / "mpc_numbered.txt").read_text().splitlines())
    else:
        records = ingest_mpc.fetch_numbered()
    return ingest_mpc.write_discoveries(conn, records)


def stage_cad(conn, *, offline: bool, years: int) -> dict[str, int]:
    total = {"close_approaches": 0, "unmatched": 0}
    if offline:
        fields, rows = ingest_cad.load_fixture(FIXTURES / "cad.json")
        pages = [(fields, rows)]
    else:
        y = time.gmtime().tm_year
        pages = ingest_cad.iter_window(f"{y - years}-01-01", f"{y + years}-12-31")
    for fields, rows in pages:
        r = ingest_cad.write_close_approaches(conn, fields, rows)
        total["close_approaches"] += r["close_approaches"]
        total["unmatched"] += r["unmatched"]
    return total


def stage_enrichment(conn, store: str | None) -> dict[str, int]:
    if not store or not Path(store).exists():
        return {"applied": 0, "skipped": 0, "note": "no enrichment store"}  # type: ignore[dict-item]
    return ingest_enrichment.merge_store(conn, store)


def stage_fts(conn) -> int:
    conn.execute("DELETE FROM objects_fts")
    conn.execute(
        """
        INSERT INTO objects_fts (id, name, designation, alt)
        SELECT o.id, o.name, COALESCE(o.designation, ''),
               COALESCE((SELECT group_concat(d.designation, ' ') FROM designations d WHERE d.object_id = o.id), '')
        FROM objects o
        """
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM objects_fts").fetchone()[0]


def stage_tiers(conn) -> dict[str, int]:
    """Tier 1 = what the site shows today; tier 2 = everything else (spec §5.3)."""
    conn.execute("INSERT OR IGNORE INTO enrichment_state (object_id, tier) SELECT id, 2 FROM objects")
    conn.execute(
        """
        UPDATE enrichment_state SET tier = 1 WHERE object_id IN (
            SELECT id FROM objects WHERE object_type IN ('star','planet','moon','dwarf_planet','dwarf_planet_candidate','comet')
            UNION SELECT object_id FROM classifications WHERE label IN ('NEO','PHA','Named')
            UNION SELECT o.id FROM objects o JOIN visual_properties v ON v.object_id = o.id
                  WHERE o.object_type IN ('tno','centaur') AND v.absolute_magnitude_h < 7
        )
        """
    )
    conn.commit()
    rows = conn.execute("SELECT tier, COUNT(*) FROM enrichment_state GROUP BY tier").fetchall()
    return {f"tier{t}": n for t, n in rows}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--online", action="store_true", help="Pull everything from JPL / MPC")
    mode.add_argument("--offline", action="store_true", help="Build from tests/fixtures (no network)")
    p.add_argument("--fresh", action="store_true", help="Start from an empty database (recommended)")
    p.add_argument("--page", type=int, default=50000, help="SBDB rows per request")
    p.add_argument("--cad-years", type=int, default=200, help="Close-approach window, ± years from now")
    p.add_argument("--skip-cad", action="store_true")
    p.add_argument("--skip-mpc", action="store_true")
    p.add_argument("--no-vacuum", action="store_true")
    p.add_argument("--enrichment-store", default=None, help="crawler store (enrichment.sqlite) to merge in")
    args = p.parse_args(argv)
    offline = args.offline

    print(f"Building {DB_PATH} ({'offline fixtures' if offline else 'online'}) …")
    if args.fresh:
        _fresh_db()
    conn = connect(create=True)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("INSERT INTO build_meta (started_at, mode) VALUES (?, ?)",
                 (started, "full-offline" if offline else "full"))
    build_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    totals: dict[str, object] = {}

    t0 = time.monotonic()
    totals["seed"] = _run_stage("Stage 1: curated seed", lambda: stage_seed(conn))
    totals["factsheets"] = _run_stage("Stage 1b: NASA fact sheets (seed/factsheets.json)",
                                       lambda: stage_factsheets(conn))
    totals["satellites"] = _run_stage("Stage 1c: JPL planetary satellites",
                                       lambda: stage_satellites(conn, offline=offline))
    totals["sbdb"] = _run_stage("Stage 2: SBDB bulk (all fields)",
                                 lambda: stage_sbdb(conn, offline=offline, page=args.page))
    if not args.skip_mpc:
        totals["mpc"] = _run_stage("Stage 4: MPC discoveries", lambda: stage_mpc(conn, offline=offline))
    if not args.skip_cad:
        totals["cad"] = _run_stage("Stage 5: JPL close approaches",
                                    lambda: stage_cad(conn, offline=offline, years=args.cad_years))
    totals["tiers"] = _run_stage("Stage 6: crawler tiers", lambda: stage_tiers(conn))
    totals["enrichment"] = _run_stage("Stage 7: merge crawler enrichment",
                                       lambda: stage_enrichment(conn, args.enrichment_store))
    totals["fts_rows"] = _run_stage("Stage 8: full-text index (after every designation source, incl. lookups)",
                                     lambda: stage_fts(conn))

    row_count = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
    conn.execute("UPDATE build_meta SET finished_at = ?, row_count = ?, notes = ? WHERE id = ?",
                 (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row_count, json.dumps(totals), build_id))
    conn.commit()

    def _analyze_vacuum() -> None:
        conn.execute("ANALYZE")
        if not args.no_vacuum:
            conn.execute("VACUUM")
    _run_stage("Stage 9: ANALYZE + VACUUM", _analyze_vacuum)
    conn.close()

    published = publish()
    elapsed = time.monotonic() - t0
    minutes, seconds = divmod(int(elapsed), 60)
    print("=" * 60)
    print(f"Done in {elapsed:.0f}s. {row_count} objects.")
    for k, v in totals.items():
        print(f"  {k:12s} {v}")
    print(f"DB: {published} ({published.stat().st_size / 1048576:.1f} MiB)")
    print(f"Total: {minutes}m{seconds}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
