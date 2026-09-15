import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_sats import display_name, match_key, parse_elements, parse_physical, write_satellites  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def test_name_normalisation():
    assert display_name("S2010_J_1") == "S/2010 J 1"
    assert display_name("Magaclite") == "Megaclite" and display_name("Moon") == "Luna"
    assert match_key("S/2003 J 10") == match_key("S2003_J_10") == match_key("moon-s/2003-j-10"[5:])


def test_parse_elements_table():
    els = parse_elements((FIX / "jpl_sats_elem.html").read_text())
    assert len(els) >= 450
    moon = next(e for e in els if e["code"] == 301)
    assert moon["name"] == "Luna" and abs(moon["a_km"] - 384400) < 1 and abs(moon["period_days"] - 27.322) < 1e-3
    titan = next(e for e in els if e["name"] == "Titan")
    assert titan["planet"] == "Saturn" and titan["frame"] in ("ecliptic", "Laplace", "equatorial")


def test_parse_physical_table():
    ph = parse_physical((FIX / "jpl_sats_phys_par.html").read_text())
    assert len(ph) >= 40
    moon = next(p for p in ph if p["code"] == 301)
    assert abs(moon["gm_km3_s2"] - 4902.8) < 0.01 and abs(moon["radius_km"] - 1737.4) < 0.01 and moon["density_g_cm3"] == 3.344


def test_write_creates_missing_moons_and_keeps_seed_values():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.executescript("""
        INSERT INTO objects (id, name, object_type) VALUES ('sun','Sun','star');
        INSERT INTO objects (id, name, object_type, parent_id) VALUES ('planet-earth','Earth','planet','sun'),
            ('planet-jupiter','Jupiter','planet','sun'),('planet-saturn','Saturn','planet','sun'),
            ('planet-mars','Mars','planet','sun'),('planet-uranus','Uranus','planet','sun'),
            ('planet-neptune','Neptune','planet','sun'),('dwarf-pluto','Pluto','dwarf_planet','sun');
        INSERT INTO objects (id, name, object_type, parent_id) VALUES ('moon-luna','Luna','moon','planet-earth'),
            ('moon-titan','Titan','moon','planet-saturn'),('moon-s/2003-j-10','S/2003 J 10','moon','planet-jupiter');
        INSERT INTO physical_properties (object_id, radius_km) VALUES ('moon-luna', 1737.4),('moon-titan', 2574.7);
    """)
    els = parse_elements((FIX / "jpl_sats_elem.html").read_text())
    ph = parse_physical((FIX / "jpl_sats_phys_par.html").read_text())
    stats = write_satellites(conn, els, ph)
    assert stats["moons_updated"] == 3 and stats["moons_created"] >= 440
    unique = {(e["planet"], match_key(e["name"])) for e in els}   # Puck is listed under two ephemerides
    assert conn.execute("SELECT COUNT(*) FROM objects WHERE object_type='moon'").fetchone()[0] == len(unique)
    titan = conn.execute("SELECT p.radius_km, p.gm_km3_s2, p.mass_kg, oe.centre, oe.semi_major_axis_au FROM physical_properties p JOIN orbital_elements oe USING (object_id) WHERE object_id='moon-titan'").fetchone()
    assert titan["radius_km"] == 2574.7 and titan["gm_km3_s2"] > 8900 and titan["mass_kg"] > 1e23 and titan["centre"] == "Saturn"
    assert 0.007 < titan["semi_major_axis_au"] < 0.009
    assert conn.execute("SELECT COUNT(*) FROM objects WHERE id='moon-s-2003-j-10'").fetchone()[0] == 0  # matched existing slash id
    new = conn.execute("SELECT id, name, designation, parent_id FROM objects WHERE id='moon-s-2010-j-1'").fetchone()
    assert new and new["name"] == "S/2010 J 1" and new["designation"] == "S/2010 J 1" and new["parent_id"] == "planet-jupiter"
    assert conn.execute("SELECT COUNT(*) FROM designations WHERE designation='NAIF 301'").fetchone()[0] == 1
