from solar_db.sky import constellation

CASES = [  # J2000 RA deg, Dec deg -> IAU abbr
    (37.95, 89.26, "UMi"),    # Polaris
    (88.79, 7.41, "Ori"),     # Betelgeuse
    (101.29, -16.72, "CMa"),  # Sirius
    (247.35, -26.43, "Sco"),  # Antares
    (95.99, -52.70, "Car"),   # Canopus
    (279.23, 38.78, "Lyr"),   # Vega
    (0.0, 0.0, "Psc"),
    (0.0, -90.0, "Oct"),
]


def test_known_points():
    for ra, dec, abbr in CASES:
        got = constellation(ra, dec)
        assert got["abbr"] == abbr, (ra, dec, got)


def test_names_are_full():
    assert constellation(88.79, 7.41)["name"] == "Orion"
