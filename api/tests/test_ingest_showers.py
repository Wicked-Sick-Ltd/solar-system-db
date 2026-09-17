import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import ingest_showers  # noqa: E402
from ingest_showers import fetch_showers, parse_legend, parse_showers, resolve_parent, write_showers  # noqa: E402
from ingest_sbdb import load_fixture as load_sbdb, map_all, write_mapped  # noqa: E402

FIX = (ROOT / "tests" / "fixtures" / "mdc_showers.txt").read_bytes().decode("utf-8", errors="replace")


def _db():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun', 'Sun', 'star')")
    for fx in ("sbdb_asteroids.json", "sbdb_comets.json"):
        fields, data = load_sbdb(ROOT / "tests" / "fixtures" / fx)
        write_mapped(conn, map_all(fields, data))
    return conn


def test_legend_names_established_and_working():
    legend = parse_legend([l for l in FIX.splitlines() if l.startswith(":") or l.startswith("#")])
    labels = " ".join(legend.values()).lower()
    assert "established" in labels and "working" in labels


def test_parse_geminids():
    rows = parse_showers(FIX)
    gem = [r for r in rows if r["code"] == "GEM"]
    assert gem and gem[0]["iau_no"] == 4 and gem[0]["name"].lower().startswith("geminids")
    assert 250 < gem[0]["solar_longitude_deg"] < 265 and 30 < gem[0]["vg_km_s"] < 40
    assert "Phaethon" in gem[0]["parent_body"]


def test_multiple_parameter_sets_keep_distinct_ad_no():
    rows = parse_showers(FIX)
    keys = [(r["iau_no"], r["ad_no"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert any(sum(1 for k in keys if k[0] == iau) > 1 for iau, _ in keys)


def test_empty_fields_become_none():
    rows = parse_showers(FIX)
    assert any(r["parent_body"] is None for r in rows)
    assert all(isinstance(r["status_code"], int) for r in rows)


def test_fetch_showers_retries_on_429_via_fetch_bytes(monkeypatch):
    data_row = FIX.splitlines()[-1].encode("utf-8")
    assert data_row.startswith(b'"')  # sanity: a real data row, not a header line
    payload = b":header\n" + data_row
    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, status_code, content=b""):
            self.status_code = status_code
            self.content = content

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(429)
        return FakeResponse(200, payload)

    monkeypatch.setattr(common.session, "get", fake_get)
    monkeypatch.setattr(common.time, "sleep", lambda *a, **k: None)

    text = fetch_showers()

    assert calls["n"] == 2
    assert text == payload.decode("utf-8", errors="replace")


def test_resolve_parent_by_comet_designation_and_asteroid_number():
    conn = _db()
    assert resolve_parent(conn, "1P/Halley") is not None
    assert resolve_parent(conn, "3200 Phaethon") == resolve_parent(conn, "3200")
    assert resolve_parent(conn, None) is None and resolve_parent(conn, "unknown body") is None


def test_write_showers_counts_and_links():
    conn = _db()
    counts = write_showers(conn, parse_showers(FIX))
    assert counts["showers"] >= 30 and counts["parents_resolved"] >= 2
    row = conn.execute("SELECT parent_object_id FROM meteor_showers WHERE code='ETA' LIMIT 1").fetchone()
    assert row and row[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE table_name='meteor_showers'").fetchone()[0] == 1


def test_resolve_parent_does_not_misread_a_provisional_designations_year_as_a_number():
    conn = _db()
    conn.execute(
        "INSERT INTO objects (id, name, object_type, parent_id) VALUES ('ast-2001-einstein', 'Einstein', 'asteroid', 'sun')"
    )
    conn.execute(
        "INSERT INTO designations (object_id, designation, kind, source) VALUES ('ast-2001-einstein', '2001', 'number', 'test')"
    )
    conn.commit()
    assert resolve_parent(conn, "2001 MEW1?") is None
    assert resolve_parent(conn, "2001") == "ast-2001-einstein"
    assert resolve_parent(conn, "2001 Einstein") == "ast-2001-einstein"
    assert resolve_parent(conn, "3200 Phaethon") is not None


def test_resolve_parent_does_not_misread_an_outburst_years_number_as_an_asteroid():
    """C2 regression: "2015 outburst" is a free-text remark on the Geminids-
    style fixture row (shower GLY, iau_no 794), not the numbered asteroid
    2015. A synthetic asteroid numbered 2015 makes the bug concrete: without
    the fix, resolve_parent would return it."""
    conn = _db()
    conn.execute(
        "INSERT INTO objects (id, name, object_type, parent_id) VALUES ('ast-2015-synthetic', 'Synthetic2015', 'asteroid', 'sun')"
    )
    conn.execute(
        "INSERT INTO designations (object_id, designation, kind, source) VALUES ('ast-2015-synthetic', '2015', 'number', 'test')"
    )
    conn.commit()
    assert resolve_parent(conn, "2015 outburst") is None
    assert resolve_parent(conn, "2015") == "ast-2015-synthetic"
    assert resolve_parent(conn, "2015 Synthetic2015") == "ast-2015-synthetic"


def test_write_showers_counts_unchanged_by_the_provisional_designation_guard():
    conn = _db()
    counts = write_showers(conn, parse_showers(FIX))
    assert counts["parents_resolved"] == 22 and counts["parents_unresolved"] == 3


def test_parse_showers_counts_rows_skipped_for_being_short_of_cols():
    """I7: a row with fewer fields than COLS is silently dropped by
    parse_showers — LAST_PARSE_STATS must expose that drop count so it can
    surface in the build summary instead of vanishing without a trace."""
    header = FIX.splitlines()[0]  # any ':'-prefixed header line
    short_row = '"1"|"1"|"000"|"XXX"|"short row"\n'  # far fewer than len(COLS) fields
    good_row = FIX.splitlines()[-1] + "\n"
    text = header + "\n" + short_row + good_row

    rows = parse_showers(text)

    assert len(rows) == 1
    assert ingest_showers.LAST_PARSE_STATS == {"rows": 1, "skipped": 1}


def test_write_showers_counts_include_skipped_rows():
    conn = _db()
    counts = write_showers(conn, parse_showers(FIX))
    assert counts["skipped_rows"] == 0
