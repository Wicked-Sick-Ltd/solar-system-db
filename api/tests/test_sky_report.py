from solar_db import SolarDB
from solar_db.positions import date_to_jd
from solar_db.sky import sky_report

JD = date_to_jd("2026-09-15T00:00:00Z")
# JPL Horizons, geocentric astrometric J2000, 2026-09-15 00:00 UTC: (ra_deg, dec_deg, delta_au, elongation_deg)
HORIZONS = {
    "planet-mars": (113.63084, 22.42036, 1.7691, 60.07),
    "planet-jupiter": (138.82417, 16.51379, 6.0868, 35.63),
    "dwarf-pluto": (306.27211, -23.68704, 34.9317, 130.96),
}


def _db():
    db = SolarDB()
    return db, db.get_orbital_elements("planet-earth")


def test_matches_horizons_within_a_degree():
    db, earth = _db()
    for oid, (ra, dec, delta, elong) in HORIZONS.items():
        r = sky_report(db.get_orbital_elements(oid), earth, JD)
        assert abs(((r["ra_deg"] - ra + 180) % 360) - 180) < 1.0, (oid, r["ra_deg"])
        assert abs(r["dec_deg"] - dec) < 1.0, (oid, r["dec_deg"])
        assert abs(r["distance_from_earth_au"] - delta) / delta < 0.02, oid
        assert abs(r["elongation_deg"] - elong) < 1.5, oid


def test_sun():
    _, earth = _db()
    r = sky_report(None, earth, JD, is_sun=True)
    assert abs(r["ra_deg"] - 172.48327) < 1.0 and abs(r["dec_deg"] - 3.24575) < 1.0
    assert r["elongation_deg"] == 0.0


def test_observer_block_present_only_with_coordinates():
    db, earth = _db()
    mars = db.get_orbital_elements("planet-mars")
    assert sky_report(mars, earth, JD)["observer"] is None
    o = sky_report(mars, earth, JD, lat=51.5, lon=-0.12)["observer"]
    assert set(o) >= {"altitude_deg", "azimuth_deg", "is_up", "is_dark", "rise_utc", "transit_utc", "set_utc"}
    assert o["lat"] == 51.5 and o["lon"] == -0.12
