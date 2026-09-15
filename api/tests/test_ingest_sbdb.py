import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_sbdb import ORBIT_CLASS_CODES, load_fixture, map_all, map_row, sbdb_number, write_mapped  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def _row(path, spkid):
    fields, data = load_fixture(path)
    for r in data:
        if r[0] == spkid:
            return fields, r
    raise AssertionError(f"spkid {spkid} not in fixture")


def test_number_from_spkid():
    assert sbdb_number(20000001) == 1
    assert sbdb_number(2000001) == 1
    assert sbdb_number(20134340) == 134340
    assert sbdb_number(30000123) is None


def test_ceres_maps_every_field():
    fields, r = _row(FIX / "sbdb_asteroids.json", 20000001)
    m = map_row(fields, r)
    assert m["id"] == "ast-20000001-ceres"
    o = m["orbital"]
    assert o["orbit_class_code"] == "MBA" and o["orbit_class_name"] == "Main-belt asteroid"
    assert abs(o["moid_au"] - 1.58107) < 1e-6 and o["condition_code"] == 0 and o["n_obs_used"] > 1000
    assert o["producer"] and o["pe_used"] == "DE441" and o["sigma_a"] is not None
    assert m["visual"]["spectral_type"] == "C" and m["visual"]["spectral_type_tholen"] == "G"
    assert m["visual"]["spectral_type"] not in ORBIT_CLASS_CODES
    assert abs(m["physical"]["gm_km3_s2"] - 62.6284) < 1e-4 and abs(m["physical"]["radius_km"] - 469.7) < 0.01
    assert m["physical"]["pole_ra_dec"] == "291.421/66.758" and m["physical"]["extent_km"].startswith("964.4")
    kinds = {d: k for d, k in m["designations"]}
    assert kinds.get("1") == "number" and kinds.get("A801 AA") == "provisional" and kinds.get("Ceres") == "name"
    assert "Named" in m["classifications"] and "MBA" in m["classifications"]


def test_curated_row_routes_to_curated_id():
    fields, r = _row(FIX / "sbdb_asteroids.json", 20134340)
    m = map_row(fields, r, curated={134340: "dwarf-pluto"})
    assert m["id"] == "dwarf-pluto" and m["is_curated"] is True
    assert m["orbital"]["semi_major_axis_au"] > 39


def test_curated_rows_keep_seed_values_and_fill_gaps():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun', 'Sun', 'star')")
    conn.execute("INSERT INTO objects (id, name, object_type, parent_id) VALUES ('dwarf-ceres', 'Ceres', 'dwarf_planet', 'sun')")
    conn.execute("INSERT INTO physical_properties (object_id, radius_km) VALUES ('dwarf-ceres', 469.73)")
    fields, r = _row(FIX / "sbdb_asteroids.json", 20000001)
    write_mapped(conn, [map_row(fields, r, curated={1: "dwarf-ceres"})])
    row = conn.execute("SELECT radius_km, gm_km3_s2, pole_ra_dec FROM physical_properties WHERE object_id='dwarf-ceres'").fetchone()
    assert row["radius_km"] == 469.73            # seed value kept (SBDB says 469.7)
    assert abs(row["gm_km3_s2"] - 62.6284) < 1e-4 and row["pole_ra_dec"]   # gaps filled


def test_comet_maps_from_perihelion_with_magnitude_params():
    fields, data = load_fixture(FIX / "sbdb_comets.json")
    m = map_row(fields, data[0])
    assert m["id"].startswith("comet-") and m["object"]["object_type"] == "comet"
    assert m["orbital"]["orbit_class_code"] in ORBIT_CLASS_CODES
    assert m["orbital"]["epoch"].endswith("(perihelion)") or m["orbital"]["mean_anomaly_deg"] is not None
    assert m["visual"]["comet_m1"] is not None or m["visual"]["absolute_magnitude_h"] is None


def test_write_fixture_into_schema_v2():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun', 'Sun', 'star')")
    conn.execute("INSERT INTO objects (id, name, object_type, parent_id) VALUES ('dwarf-pluto', 'Pluto', 'dwarf_planet', 'sun')")
    counts = {}
    for f in ("sbdb_asteroids.json", "sbdb_comets.json"):
        fields, data = load_fixture(FIX / f)
        counts.update(write_mapped(conn, map_all(fields, data, curated={134340: "dwarf-pluto"})))
    total = conn.execute("SELECT COUNT(*) FROM objects WHERE object_type IN ('asteroid','tno','centaur','comet')").fetchone()[0]
    assert total == 1526 + 400  # Pluto routed to the curated row
    bad = conn.execute("SELECT COUNT(*) FROM visual_properties WHERE spectral_type IN (%s)"
                       % ",".join("'%s'" % c for c in ORBIT_CLASS_CODES)).fetchone()[0]
    assert bad == 0
    assert conn.execute("SELECT orbit_class_code FROM orbital_elements WHERE object_id='dwarf-pluto'").fetchone()[0] == "TNO"
    assert conn.execute("SELECT COUNT(*) FROM designations").fetchone()[0] > 3000
    assert conn.execute("SELECT COUNT(*) FROM orbital_elements WHERE orbit_class_code IS NULL").fetchone()[0] == 0
