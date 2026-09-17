import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_cad import cd_to_iso, load_fixture as load_cad, write_close_approaches  # noqa: E402
from ingest_mpc import parse_line, parse_lines, write_discoveries  # noqa: E402
from ingest_sbdb import load_fixture as load_sbdb, map_all, write_mapped  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun', 'Sun', 'star')")
    fields, data = load_sbdb(FIX / "sbdb_asteroids.json")
    write_mapped(conn, map_all(fields, data))
    return conn


def test_parse_ceres_line():
    line = "     (1) Ceres                           1801 01 01  Palermo                  Piazzi, G."
    rec = parse_line(line)
    assert rec["number"] == 1 and rec["name"] == "Ceres" and rec["discovered_on"] == "1801-01-01"
    assert rec["site"] == "Palermo" and rec["discoverer"] == "Piazzi, G." and rec["provisional"] is None
    assert rec["date_is_conventional"] is False


def test_parse_handles_multiple_discoverers_and_no_name():
    line = "  (4179) Toutatis                        1989 01 04  Caussols                 Pollas, C."
    assert parse_line(line)["discoverer"] == "Pollas, C."
    unnamed = "(346889) Rhiphonos           2009 QV38   2009 08 28  Zelenchukskaya Stn 84384 Kryachko, T. V."
    rec = parse_line(unnamed)
    assert rec["provisional"] == "2009 QV38" and rec["site"] == "Zelenchukskaya Stn" and rec["discoverer"] == "Kryachko, T. V."
    assert rec["name_ref"] == 84384
    starred = "(306173)                     2010 NK83   2010 07 01* WISE                     WISE"
    rec = parse_line(starred)
    assert rec["name"] is None and rec["date_is_conventional"] is True and rec["discoverer"] == "WISE"


def test_parse_strips_trailing_name_ref_number_from_site():
    # tests/fixtures/mpc_numbered.txt line 587: (5145) Pholus — site "Kitt Peak"
    # is followed by "20523", the Minor Planet Circular naming-citation
    # reference number, not part of the site name.
    pholus = "  (5145) Pholus              1992 AD     1992 01 09  Kitt Peak          20523 Spacewatch"
    rec = parse_line(pholus)
    assert rec["site"] == "Kitt Peak"
    assert rec["name_ref"] == 20523
    assert rec["discoverer"] == "Spacewatch"

    # line 596: (5590) — same observatory, no Name Ref. present, so nothing
    # to strip and name_ref stays None.
    unnumbered = "  (5590)                     1990 VA     1990 11 09  Kitt Peak                Spacewatch"
    rec = parse_line(unnumbered)
    assert rec["site"] == "Kitt Peak"
    assert rec["name_ref"] is None


def test_discoveries_written_for_fixture_bodies():
    conn = _db()
    stats = write_discoveries(conn, parse_lines((FIX / "mpc_numbered.txt").read_text().splitlines()))
    assert stats["discoveries"] == 1527
    row = conn.execute("SELECT d.discoverer, d.site, o.discoverer AS legacy FROM discoveries d JOIN objects o ON o.id=d.object_id WHERE d.object_id='ast-20000001-ceres'").fetchone()
    assert row["discoverer"] == "Piazzi, G." and row["site"] == "Palermo" and row["legacy"] == "Piazzi, G."


def test_cd_to_iso():
    assert cd_to_iso("2026-Sep-01 02:53") == "2026-09-01T02:53:00Z"


def test_close_approaches_resolve_by_designation():
    conn = _db()
    fields, rows = load_cad(FIX / "cad.json")
    stats = write_close_approaches(conn, fields, rows)
    assert stats["close_approaches"] > 0
    row = conn.execute("SELECT body, cd_iso, dist_au FROM close_approaches ORDER BY cd_jd LIMIT 1").fetchone()
    assert row["body"] and row["cd_iso"].endswith("Z") and row["dist_au"] < 0.05
