from solar_db.sky import gmst_deg, rise_transit_set, topocentric


def test_gmst_meeus_example_12a():
    # 1987-04-10 0h UT -> GMST 13h10m46.3668s = 197.693195 deg
    assert abs(gmst_deg(2446895.5) - 197.693195) < 1e-4


def test_object_on_meridian_south_of_zenith_has_azimuth_180():
    jd = 2446895.5
    lst = gmst_deg(jd)  # lon 0 -> LST = GMST
    v = topocentric(lst, 10.0, 51.5, 0.0, jd)
    assert abs(v["azimuth_deg"] - 180) < 1e-6
    assert abs(v["altitude_deg"] - (90 - 51.5 + 10.0)) < 1e-6


def test_circumpolar_and_never_rises():
    assert rise_transit_set(0, 89, 51.5, 0, 2461299.5)["circumpolar"] is True
    assert rise_transit_set(0, -60, 51.5, 0, 2461299.5)["never_rises"] is True


def test_rise_before_transit_before_set_or_wraps_midnight():
    r = rise_transit_set(120.0, 10.0, 51.5, -0.12, 2461299.5)
    assert r["circumpolar"] is False and r["never_rises"] is False
    assert r["rise_utc"] and r["transit_utc"] and r["set_utc"]
    assert (r["rise_utc"] < r["transit_utc"] < r["set_utc"]) or (r["rise_utc"] > r["set_utc"])
