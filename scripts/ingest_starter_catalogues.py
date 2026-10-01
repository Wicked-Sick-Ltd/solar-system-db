"""Install reviewed, pinned starter snapshots identically in offline/online builds."""

from __future__ import annotations

import json

from solar_db.starter_catalogues import load_starter_catalogues


def write_starter_catalogues(conn):
    records, sources = load_starter_catalogues()  # Validate everything before mutation.
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS starter_targets (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS starter_sources (source TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM starter_targets")
        conn.execute("DELETE FROM starter_sources")
        conn.executemany(
            "INSERT INTO starter_targets VALUES (?,?)",
            [(r["id"], json.dumps(r, allow_nan=False)) for r in records],
        )
        conn.executemany(
            "INSERT INTO starter_sources VALUES (?,?)",
            [(s, json.dumps(p, allow_nan=False)) for s, p in sources.items()],
        )
    return {
        family: sum(family in r["families"] for r in records)
        for family in ("bright_star", "double_star", "deep_sky")
    }
