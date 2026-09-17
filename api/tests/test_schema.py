import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[2] / "schema" / "schema.sql"

V2_COLUMNS = {
    "orbital_elements": ["moid_au", "tisserand_jupiter", "condition_code", "data_arc_days", "orbit_class_code",
                         "orbit_class_name", "perihelion_time_jd", "sigma_a", "producer"],
    "physical_properties": ["gm_km3_s2", "slope_g", "extent_km", "pole_ra_dec", "j2", "synodic_period_days",
                            "mean_temperature_k"],
    "visual_properties": ["spectral_type", "spectral_type_tholen", "colour_u_b", "comet_m1", "nongrav_a1"],
}
V2_TABLES = ["designations", "discoveries", "close_approaches", "atmospheres", "radar_observations",
             "impact_monitoring", "enrichment_state", "objects_fts"]


def _mem():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text())
    return conn


def test_user_version_is_2():
    assert _mem().execute("PRAGMA user_version").fetchone()[0] >= 2


def test_v2_columns_exist():
    conn = _mem()
    for table, cols in V2_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in cols if c not in have]
        assert not missing, (table, missing)


def test_v2_tables_exist():
    conn = _mem()
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert set(V2_TABLES) <= have, set(V2_TABLES) - have


def test_v1_columns_still_present():
    conn = _mem()
    have = {r[1] for r in conn.execute("PRAGMA table_info(visual_properties)")}
    assert {"geometric_albedo", "bond_albedo", "absolute_magnitude_h", "colour_b_v", "spectral_type",
            "dominant_colour_hex"} <= have


def test_schema_v3_has_meteor_showers():
    conn = sqlite3.connect(":memory:")
    conn.executescript((SCHEMA).read_text())
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(meteor_showers)")}
    assert {"iau_no", "ad_no", "code", "name", "status_code", "status_label", "solar_longitude_deg",
            "ra_deg", "dec_deg", "vg_km_s", "parent_body", "parent_object_id", "source"} <= cols
    idx = {r[1] for r in conn.execute("PRAGMA index_list(meteor_showers)")}
    assert {"idx_showers_code", "idx_showers_parent", "idx_showers_status"} <= idx
