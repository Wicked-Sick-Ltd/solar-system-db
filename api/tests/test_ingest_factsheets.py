import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_factsheets import load_seed, mapped_rows, write_factsheets  # noqa: E402
from scrape_factsheets import parse_atmosphere, parse_value  # noqa: E402


def test_parse_value_variants():
    assert parse_value("1,898.13") == 1898.13 and parse_value("9.9250*") == 9.925
    assert parse_value("Unknown*") == "Unknown" and parse_value("") is None


def test_atmosphere_pressure_units_and_both_composition_orders():
    a = parse_atmosphere("Surface pressure: 6.36 mb (mean) Average temperature: 210 K (-63 C) "
                         "Scale height: 11.1 km Mean molecular weight: 43.34 "
                         "Atmospheric composition (by volume): Major: Carbon Dioxide (CO2) - 95.1%; Nitrogen (N2) - 2.59%")
    assert abs(a["surface_pressure_bar"] - 0.00636) < 1e-9 and a["temperature_k"] == 210 and a["scale_height_km"] == 11.1
    assert a["composition"][0] == {"species": "Carbon Dioxide (CO2)", "fraction": 95.1, "unit": "%", "uncertainty": None}
    b = parse_atmosphere("Surface pressure: 1014 mb Atmospheric composition (by volume): Major: 78.084% Nitrogen (N2), 20.946% Oxygen (O2)")
    assert {c["species"]: c["fraction"] for c in b["composition"]} == {"Nitrogen (N2)": 78.084, "Oxygen (O2)": 20.946}


def test_mapping_handles_table_and_pre_label_variants():
    rows = mapped_rows({"Mass (10 24 kg)": 5.9722, "Volume (10 10 km 3)": 108.321, "J 2 (x 10 -6 )": 1082.63,
                        "GM (x 10 6 km 3 /s 2 )": 0.3986, "Equatorial radius (1 bar level) (km)": 71492.0,
                        "Surface gravity (mean) (m/s 2 )": 9.82, "Bond albedo": 0.294})
    assert rows["physical_properties"]["mass_kg"] == 5.9722e24 and rows["physical_properties"]["volume_km3"] == 108.321e10
    assert rows["physical_properties"]["j2"] == 1082.63 and abs(rows["physical_properties"]["gm_km3_s2"] - 398600) < 1
    assert rows["physical_properties"]["equatorial_radius_km"] == 71492 and rows["physical_properties"]["surface_gravity_m_s2"] == 9.82
    assert rows["visual_properties"]["bond_albedo"] == 0.294


def test_seed_json_covers_all_ten_bodies_with_atmospheres():
    seed = load_seed()
    bodies = [k for k in seed if not k.startswith("_")]
    assert len(bodies) == 10
    assert seed["planet-jupiter"]["values"]["J 2 (x 10 -6 )"] == 14736
    assert seed["planet-mars"]["atmosphere"]["surface_pressure_bar"] < 0.01
    assert any(c["species"].startswith("Nitrogen") and c["fraction"] > 78 for c in seed["planet-earth"]["atmosphere"]["composition"])


def test_write_into_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.executescript("""
        INSERT INTO objects (id, name, object_type) VALUES ('sun','Sun','star');
        INSERT INTO objects (id, name, object_type, parent_id) VALUES ('planet-jupiter','Jupiter','planet','sun'),
            ('planet-earth','Earth','planet','sun'),('planet-mars','Mars','planet','sun');
        INSERT INTO physical_properties (object_id, radius_km) VALUES ('planet-jupiter', 69911);
    """)
    stats = write_factsheets(conn, load_seed())
    assert stats["factsheet_bodies"] == 3 and stats["atmospheres"] == 3
    j = conn.execute("SELECT * FROM physical_properties WHERE object_id='planet-jupiter'").fetchone()
    assert j["j2"] == 14736 and abs(j["gm_km3_s2"] - 126686531.9) < 1000 and j["moment_of_inertia"] == 0.254
    assert j["synodic_period_days"] == 398.88 and j["black_body_temperature_k"] == 109.9 and j["mass_kg"] == 1898.13e24
    e = conn.execute("SELECT * FROM atmospheres WHERE object_id='planet-earth'").fetchone()
    comp = json.loads(e["composition_json"])
    assert abs(e["surface_pressure_bar"] - 1.014) < 1e-6 and e["scale_height_km"] == 8.5 and comp[0]["species"] == "Nitrogen (N2)"
    assert conn.execute("SELECT magnitude_v10 FROM visual_properties WHERE object_id='planet-jupiter'").fetchone()[0] == -9.4
