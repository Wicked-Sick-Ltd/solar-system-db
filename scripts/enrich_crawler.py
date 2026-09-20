"""The Forth Road Bridge: a perpetual, polite crawl of the SBDB lookup API.

Two rings, one queue (spec §5.3). Tier 1 (what the site shows) is re-fetched
every 7 days; tier 2 (everything else) is a rolling pass that restarts when it
finishes. Results — the raw JSON payloads — live in their own SQLite store so
they survive nightly rebuilds and are merged in by build_full (stage 7).

    python scripts/enrich_crawler.py --store /data/enrichment.sqlite --db /data/solar_system.sqlite
    python scripts/enrich_crawler.py ... --rps 1.0 --max 500 --once   # bounded run

Never parallelised. Back-off doubles on 429/503 up to 10 minutes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import SBDB_LOOKUP_URL, USER_AGENT  # noqa: E402

TIER1_REFRESH_DAYS = 7
LOOKUP_PARAMS = {"full-prec": "true", "phys-par": "true", "ca-data": "true", "vi-data": "true",
                 "discovery": "true", "alt-des": "true", "radar-obs": "true", "sat": "true"}

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS lookups (
    object_id   TEXT PRIMARY KEY,
    sstr        TEXT NOT NULL,          -- what we asked SBDB for (number or designation)
    tier        INTEGER NOT NULL DEFAULT 2,
    fetched_at  TEXT,                   -- ISO UTC of the last SUCCESSFUL fetch (drives staleness/coverage)
    attempted_at TEXT,                  -- ISO UTC of the last attempt of any outcome (drives queue order)
    status      TEXT,                   -- ok | not_found | error   (a later failure never clears 'ok')
    http_status INTEGER,
    last_error  TEXT,
    payload     TEXT                    -- raw JSON on success (kept across later failures)
);
CREATE INDEX IF NOT EXISTS idx_lookups_tier_fetched ON lookups(tier, fetched_at);
"""


def open_store(path: str | Path) -> sqlite3.Connection:
    """WAL + a long busy timeout: the crawler commits every second while the
    nightly merge reads the same file — neither must block or die on a lock."""
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.executescript(STORE_SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(lookups)")}
    migrations = {
        "attempted_at": "ALTER TABLE lookups ADD COLUMN attempted_at TEXT",
        "last_error": "ALTER TABLE lookups ADD COLUMN last_error TEXT",
    }
    for column, query in migrations.items():      # stores created before these columns existed
        if column not in have:
            conn.execute(query)
    conn.commit()
    return conn


def sync_queue(store: sqlite3.Connection, catalogue: sqlite3.Connection) -> int:
    """Mirror the catalogue's small bodies + tiers into the store (idempotent)."""
    rows = catalogue.execute(
        """
        SELECT o.id, COALESCE(es.tier, 2) AS tier,
               COALESCE((SELECT designation FROM designations d WHERE d.object_id = o.id AND d.kind = 'number' LIMIT 1),
                        (SELECT designation FROM designations d WHERE d.object_id = o.id AND d.kind = 'provisional' LIMIT 1),
                        o.designation, o.name) AS sstr
        FROM objects o LEFT JOIN enrichment_state es ON es.object_id = o.id
        WHERE o.object_type IN ('asteroid','comet','tno','centaur','dwarf_planet','dwarf_planet_candidate')
        """
    ).fetchall()
    store.executemany(
        "INSERT INTO lookups (object_id, sstr, tier) VALUES (?, ?, ?) "
        "ON CONFLICT(object_id) DO UPDATE SET sstr = excluded.sstr, tier = excluded.tier",
        [(r["id"], r["sstr"], r["tier"]) for r in rows if r["sstr"]],
    )
    store.commit()
    return len(rows)


def next_batch(store: sqlite3.Connection, n: int, now: float | None = None) -> list[sqlite3.Row]:
    """Tier-1 never-fetched → tier-1 stale → tier-2 never-fetched → tier-2 oldest,
    ordered by last attempt so failing bodies cycle rather than block the queue."""
    now = now or time.time()
    stale1 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - TIER1_REFRESH_DAYS * 86400))
    return store.execute(
        """
        SELECT object_id, sstr, tier FROM lookups
        WHERE tier <> 1 OR fetched_at IS NULL OR fetched_at < ?
        ORDER BY
          CASE WHEN tier = 1 AND fetched_at IS NULL THEN 0
               WHEN tier = 1 THEN 1
               WHEN tier = 2 AND fetched_at IS NULL THEN 2
               ELSE 3 END,
          attempted_at
        LIMIT ?
        """,
        (stale1, n),
    ).fetchall()


def record(store: sqlite3.Connection, object_id: str, status: str, http_status: int | None, payload: dict | None,
           error: str | None = None) -> None:
    """A success sets fetched_at/status/payload. A failure records the attempt
    and error but never demotes an earlier 'ok' or discards its payload."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if status == "ok":
        store.execute(
            "UPDATE lookups SET fetched_at = ?, attempted_at = ?, status = 'ok', http_status = ?, last_error = NULL, payload = ? "
            "WHERE object_id = ?", (now, now, http_status, json.dumps(payload), object_id))
    elif status == "not_found":
        store.execute(
            "UPDATE lookups SET attempted_at = ?, status = CASE WHEN status = 'ok' THEN 'ok' ELSE 'not_found' END, "
            "http_status = ?, last_error = ? WHERE object_id = ?", (now, http_status, error or "not found", object_id))
    else:
        store.execute(
            "UPDATE lookups SET attempted_at = ?, status = COALESCE(status, 'error'), http_status = ?, last_error = ? "
            "WHERE object_id = ?", (now, http_status, error or status, object_id))
    store.commit()


class Crawler:
    def __init__(self, store: sqlite3.Connection, *, rps: float = 1.0, session: requests.Session | None = None,
                 max_backoff: float = 600.0) -> None:
        self.store = store
        self.interval = 1.0 / rps if rps > 0 else 0.0
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.backoff = 0.0
        self.max_backoff = max_backoff

    def fetch_one(self, sstr: str) -> tuple[str, int | None, dict | None]:
        try:
            r = self.session.get(SBDB_LOOKUP_URL, params={"sstr": sstr, **LOOKUP_PARAMS}, timeout=60)
        except requests.RequestException:
            return "error", None, None
        if r.status_code in (429, 503):
            return "throttled", r.status_code, None
        if r.status_code == 404 or (r.status_code == 200 and "object" not in r.json()):
            return "not_found", r.status_code, None
        if r.status_code != 200:
            return "error", r.status_code, None
        return "ok", 200, r.json()

    def run(self, *, max_items: int | None = None, once: bool = False, batch: int = 200,
            resync: Callable[[], None] | None = None, resync_every: float = 900.0) -> dict[str, int]:
        """`resync` (e.g. re-sync the queue from a freshly rebuilt catalogue) is
        called between batches, at most every `resync_every` seconds."""
        done = {"ok": 0, "not_found": 0, "error": 0, "throttled": 0}
        processed = 0
        last_sync = time.time()
        while True:
            if resync and time.time() - last_sync >= resync_every:
                resync()
                last_sync = time.time()
            rows = next_batch(self.store, batch)
            if not rows:
                if resync and not once:
                    time.sleep(min(resync_every, 60))
                    continue
                return done
            for row in rows:
                if max_items is not None and processed >= max_items:
                    return done
                t0 = time.time()
                while True:                       # throttled → back off and retry this body
                    status, http, payload = self.fetch_one(row["sstr"])
                    if status != "throttled":
                        break
                    done["throttled"] += 1
                    self.backoff = min(self.max_backoff, (self.backoff or 5.0) * 2)
                    time.sleep(self.backoff)
                self.backoff = 0.0
                record(self.store, row["object_id"], status, http, payload)
                done[status] += 1
                processed += 1
                elapsed = time.time() - t0
                if self.interval > elapsed:
                    time.sleep(self.interval - elapsed)
            if once:
                return done


def coverage(store: sqlite3.Connection) -> dict[str, float | int]:
    stale1 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - TIER1_REFRESH_DAYS * 86400))
    t1 = store.execute("SELECT COUNT(*) FROM lookups WHERE tier = 1").fetchone()[0]
    t1_fresh = store.execute("SELECT COUNT(*) FROM lookups WHERE tier = 1 AND status = 'ok' AND fetched_at >= ?", (stale1,)).fetchone()[0]
    t2 = store.execute("SELECT COUNT(*) FROM lookups WHERE tier = 2").fetchone()[0]
    t2_ok = store.execute("SELECT COUNT(*) FROM lookups WHERE tier = 2 AND status = 'ok'").fetchone()[0]
    return {"tier1_total": t1, "tier1_fresh_within_7d": t1_fresh, "tier1_fresh_fraction": (t1_fresh / t1) if t1 else 0.0,
            "tier2_total": t2, "tier2_enriched": t2_ok, "tier2_enriched_fraction": (t2_ok / t2) if t2 else 0.0}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--store", required=True, help="enrichment.sqlite (created if missing)")
    p.add_argument("--db", required=True, help="the catalogue to take the queue and tiers from")
    p.add_argument("--rps", type=float, default=1.0, help="requests per second to JPL (agree higher with them first)")
    p.add_argument("--max", type=int, default=None, help="stop after N lookups")
    p.add_argument("--once", action="store_true", help="one batch then exit")
    p.add_argument("--batch", type=int, default=200)
    args = p.parse_args(argv)

    store = open_store(args.store)
    state = {"mtime": 0.0}

    def resync() -> None:
        """Re-read the queue whenever the nightly build has replaced the catalogue."""
        try:
            mtime = Path(args.db).stat().st_mtime
        except OSError:
            return
        if mtime == state["mtime"]:
            return
        catalogue = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        catalogue.row_factory = sqlite3.Row
        n = sync_queue(store, catalogue)
        catalogue.close()
        state["mtime"] = mtime
        print(f"queue synced: {n} bodies; coverage {coverage(store)}")

    resync()
    stats = Crawler(store, rps=args.rps).run(max_items=args.max, once=args.once, batch=args.batch,
                                              resync=resync, resync_every=300.0)
    print(f"done: {stats}; coverage {coverage(store)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
