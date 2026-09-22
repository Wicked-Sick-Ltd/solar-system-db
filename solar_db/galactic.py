"""Reproducible catalogue coordinates, not propagated stellar ephemerides."""
from __future__ import annotations

import math

FRAME = {
    "input": "ICRS; archive catalogue coordinates, no proper-motion propagation",
    "heliocentric": "Galactic Cartesian: x toward Galactic centre, y toward l=90 deg, z north",
    "unit": "pc",
    "galactocentric": {
        "galcen_distance_pc": 8122.0, "z_sun_pc": 20.8, "roll_deg": 0.0,
        "galcen_ra_deg": 266.4051, "galcen_dec_deg": -28.936175,
    },
}


def coordinates(ra: float | None, dec: float | None, distance: float | None) -> dict:
    """Return no 3D position when any measurement is missing or invalid."""
    if any(v is None or not math.isfinite(v) for v in (ra, dec, distance)):
        return {}
    if not (0 <= ra < 360 and -90 <= dec <= 90 and distance > 0):
        return {}
    from astropy import units as u
    from astropy.coordinates import Galactocentric, SkyCoord

    point = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=distance * u.pc, frame="icrs")
    local = point.galactic.cartesian
    settings = FRAME["galactocentric"]
    frame = Galactocentric(
        galcen_distance=settings["galcen_distance_pc"] * u.pc,
        z_sun=settings["z_sun_pc"] * u.pc, roll=settings["roll_deg"] * u.deg,
        galcen_coord=SkyCoord(ra=settings["galcen_ra_deg"] * u.deg,
                             dec=settings["galcen_dec_deg"] * u.deg, frame="icrs"),
    )
    gal = point.transform_to(frame).cartesian
    return {"x_pc": float(local.x.value), "y_pc": float(local.y.value), "z_pc": float(local.z.value),
            "galactocentric_x_pc": float(gal.x.value), "galactocentric_y_pc": float(gal.y.value),
            "galactocentric_z_pc": float(gal.z.value)}
