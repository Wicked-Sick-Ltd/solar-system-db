import datetime
import math

from solar_db.positions import solar_longitude_deg
from solar_db.sky import (ecliptic_to_equatorial, elongation_deg, format_dms, format_hms,
                          geocentric_equatorial, hemisphere)


def test_equatorial_rotation_of_ecliptic_pole():
    # The ecliptic north pole (0,0,1) sits at Dec = 90 - obliquity.
    x, y, z = ecliptic_to_equatorial(0.0, 0.0, 1.0)
    assert abs(math.degrees(math.asin(z)) - (90 - 23.4392911)) < 1e-6


def test_geocentric_sun_is_minus_earth():
    earth = (0.9, 0.4, 0.0)
    sun = geocentric_equatorial((0.0, 0.0, 0.0), earth)
    assert abs(sun["distance_from_earth_au"] - math.hypot(0.9, 0.4)) < 1e-9
    assert 180 < sun["ra_deg"] < 270  # anti-Earth direction, third quadrant


def test_elongation_is_zero_when_target_behind_sun():
    assert abs(elongation_deg((-2.0, 0.0, 0.0), (1.0, 0.0, 0.0))) < 1e-9
    assert abs(elongation_deg((2.0, 0.0, 0.0), (1.0, 0.0, 0.0)) - 180) < 1e-9


def test_hemisphere_bands():
    assert hemisphere(40)[0] == "northern"
    assert hemisphere(-40)[0] == "southern"
    assert hemisphere(10)[0] == "equatorial"


def test_formatting():
    assert format_hms(187.42) == "12h 29m 41s"
    assert format_dms(-2.31) == "-02° 18′ 36″"


def test_solar_longitude_deg_december():
    assert abs(solar_longitude_deg(datetime.date(2026, 12, 14)) - 262) <= 2


def test_solar_longitude_deg_june():
    assert abs(solar_longitude_deg(datetime.date(2026, 6, 14)) - 83) <= 2
