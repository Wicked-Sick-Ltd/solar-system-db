import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from enrich_crawler import Crawler, coverage, next_batch, open_store, record, sync_queue  # noqa: E402
from ingest_enrichment import apply_payload, merge_store  # noqa: E402
from ingest_sbdb import load_fixture, map_all, write_mapped  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def _catalogue():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun','Sun','star')")
    conn.execute("INSERT INTO objects (id, name, object_type, parent_id) VALUES ('dwarf-pluto','Pluto','dwarf_planet','sun')")
    fields, data = load_fixture(FIX / "sbdb_asteroids.json")
    write_mapped(conn, map_all(fields, data, curated={134340: "dwarf-pluto"}))
    conn.execute("INSERT INTO enrichment_state (object_id, tier) SELECT id, 2 FROM objects")
    conn.execute("UPDATE enrichment_state SET tier = 1 WHERE object_id IN ('ast-20099942-apophis', 'dwarf-pluto')")
    return conn


def test_queue_orders_tier1_first_then_stale(tmp_path):
    cat = _catalogue()
    store = open_store(tmp_path / "e.sqlite")
    n = sync_queue(store, cat)
    assert n > 1500
    first = next_batch(store, 2)
    assert {r["tier"] for r in first} == {1}
    assert {r["sstr"] for r in first} >= {"99942", "134340"}
    record(store, "ast-20099942-apophis", "ok", 200, {"object": {}})
    record(store, "dwarf-pluto", "ok", 200, {"object": {}})
    nxt = next_batch(store, 3)
    assert all(r["tier"] == 2 for r in nxt)             # tier 1 fresh → tier 2 never-fetched
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
    store.execute("UPDATE lookups SET fetched_at = ? WHERE object_id = 'dwarf-pluto'", (old,))
    assert next_batch(store, 1)[0]["object_id"] == "dwarf-pluto"   # tier 1 stale beats tier 2
    cov = coverage(store)
    assert cov["tier1_total"] == 2 and cov["tier1_fresh_within_7d"] == 1


class _FakeSession:
    """Serves fixtures by sstr; throttles once to exercise back-off."""

    def __init__(self):
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        sstr = params["sstr"]

        class R:
            def __init__(self, status, body):
                self.status_code, self._body = status, body

            def json(self):
                return self._body

        if self.calls == 1:
            return R(503, {})
        path = {"99942": "sbdb_lookup_99942.json", "1": "sbdb_lookup_2000001.json", "134340": "sbdb_lookup_2134340.json"}.get(sstr)
        if not path:
            return R(200, {"message": "not found"})
        return R(200, json.loads((FIX / path).read_text()))


def test_crawler_fetches_records_and_backs_off(tmp_path):
    cat = _catalogue()
    store = open_store(tmp_path / "e.sqlite")
    sync_queue(store, cat)
    session = _FakeSession()
    c = Crawler(store, rps=0, session=session, max_backoff=0.01)
    stats = c.run(max_items=3, batch=10)
    assert stats["throttled"] == 1 and stats["ok"] >= 2
    row = store.execute("SELECT status, payload FROM lookups WHERE object_id='ast-20099942-apophis'").fetchone()
    assert row["status"] == "ok" and "ca_data" in row["payload"]


def test_apply_payload_maps_every_section():
    cat = _catalogue()
    n = apply_payload(cat, "ast-20099942-apophis", json.loads((FIX / "sbdb_lookup_99942.json").read_text()))
    assert n["close_approaches"] == 98 and n["radar"] == 50
    d = cat.execute("SELECT * FROM discoveries WHERE object_id='ast-20099942-apophis'").fetchone()
    assert d["discovered_on"] == "2004-06-19" and d["discoverer"] == "Tholen, D. J." and "Apep" in d["citation"]
    assert cat.execute("SELECT COUNT(*) FROM designations WHERE object_id='ast-20099942-apophis' AND designation='2004 MN4'").fetchone()[0] == 1
    im = cat.execute("SELECT flagged FROM impact_monitoring WHERE object_id='ast-20099942-apophis'").fetchone()
    assert im["flagged"] == 0
    ca = cat.execute("SELECT body, cd_iso, dist_au FROM close_approaches WHERE object_id='ast-20099942-apophis' AND cd_iso LIKE '2029-04-13%'").fetchone()
    assert ca["body"] == "Earth" and ca["dist_au"] < 0.0003
    src = cat.execute("SELECT COUNT(*) FROM sources WHERE object_id='ast-20099942-apophis' AND field_name='absolute_magnitude_h'").fetchone()[0]
    assert src == 1
    assert cat.execute("SELECT nongrav_a2 FROM visual_properties WHERE object_id='ast-20099942-apophis'").fetchone()[0] is not None


def test_merge_store_applies_only_known_ok_rows(tmp_path):
    cat = _catalogue()
    store = open_store(tmp_path / "e.sqlite")
    sync_queue(store, cat)
    record(store, "dwarf-pluto", "ok", 200, json.loads((FIX / "sbdb_lookup_2134340.json").read_text()))
    record(store, "ast-20099942-apophis", "error", 500, None)
    store.execute("INSERT INTO lookups (object_id, sstr, tier, status, payload) VALUES ('ast-gone', 'x', 2, 'ok', '{}')")
    store.commit()
    totals = merge_store(cat, tmp_path / "e.sqlite")
    assert totals["applied"] == 1 and totals["skipped"] == 1
    assert cat.execute("SELECT COUNT(*) FROM designations WHERE object_id='dwarf-pluto' AND designation LIKE 'satellite: %'").fetchone()[0] == 5
    es = cat.execute("SELECT lookup_status, lookup_at FROM enrichment_state WHERE object_id='dwarf-pluto'").fetchone()
    assert es["lookup_status"] == "ok" and es["lookup_at"]


def test_failed_refetch_keeps_earlier_payload_and_ok_status(tmp_path):
    cat = _catalogue()
    store = open_store(tmp_path / "e.sqlite")
    sync_queue(store, cat)
    record(store, "dwarf-pluto", "ok", 200, {"object": {"des": "134340"}})
    record(store, "dwarf-pluto", "error", 500, None, error="boom")
    row = store.execute("SELECT status, payload, last_error, fetched_at, attempted_at FROM lookups WHERE object_id='dwarf-pluto'").fetchone()
    assert row["status"] == "ok" and "134340" in row["payload"] and row["last_error"] == "boom"
    assert row["fetched_at"] and row["attempted_at"] >= row["fetched_at"]


def test_lookup_close_approaches_dedupe_against_cad_rows():
    cat = _catalogue()
    cat.execute("INSERT INTO close_approaches (object_id, body, cd_jd, cd_iso, dist_au, v_rel_km_s, source) "
                "VALUES ('ast-20099942-apophis', 'Earth', 2462239.406944, '2029-04-13T21:46:00Z', 0.000254, 7.42, 'JPL CAD')")
    apply_payload(cat, "ast-20099942-apophis", json.loads((FIX / "sbdb_lookup_99942.json").read_text()))
    rows = cat.execute("SELECT cd_jd, source FROM close_approaches WHERE object_id='ast-20099942-apophis' AND cd_iso='2029-04-13T21:46:00Z'").fetchall()
    assert len(rows) == 1 and rows[0]["source"] == "JPL CAD" and abs(rows[0]["cd_jd"] - 2462239.406944) < 1e-6
