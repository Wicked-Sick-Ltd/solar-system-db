"""Two-body Kepler propagation for the stored orbital elements.

This is intentionally simple — two-body, no planetary perturbations, no
relativistic corrections. Accuracy is plenty for general astronomy use cases
(plotting positions, planetarium-style visualisations, sci-fi worldbuilding,
science-education demos). For arc-second precision or close approaches use
the JPL Horizons API directly — see the docstring on `compute_heliocentric_position`.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

# Solar GM (Gaussian gravitational constant squared, in AU^3/day^2)
# Standard value k^2 with k = 0.01720209895 rad/day.
GM_SUN = 0.01720209895 ** 2  # AU^3 / day^2

EPS = 1e-10


def date_to_jd(d: date | datetime | str) -> float:
    """Convert a date / datetime / ISO string to a Julian Date."""
    if isinstance(d, str):
        try:
            dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif isinstance(d, datetime):
        dt = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    elif isinstance(d, date):
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    else:
        raise TypeError(f"Unsupported date input: {d!r}")
    # Convert to JD (UTC ≈ TT for v1; off by ~70 s, negligible at this fidelity)
    y, m, day = dt.year, dt.month, dt.day + (dt.hour + dt.minute / 60.0 +
                                              dt.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    A = math.floor(y / 100)
    B = 2 - A + math.floor(A / 4)
    jd = (math.floor(365.25 * (y + 4716))
          + math.floor(30.6001 * (m + 1))
          + day + B - 1524.5)
    return jd


def solar_longitude_deg(d: date) -> float:
    """Low-precision ecliptic longitude of the Sun (apparent geocentric), in
    degrees, for a given calendar date.

    Formula: L = (280.460 + 0.9856474 * n) % 360, where n is the number of
    days since J2000.0 (2000-01-01 12:00 TT); the date is treated as 00:00
    UTC. Accuracy is ~1 degree — plenty for the +/-15 degree "active on"
    window used to match meteor showers to a calendar date, not for precise
    ephemeris work (use `compute_heliocentric_position` for that).
    """
    n = date_to_jd(d) - 2451545.0
    return (280.460 + 0.9856474 * n) % 360


def solve_kepler(M: float, e: float, tol: float = EPS, max_iter: int = 50) -> float:
    """Solve Kepler's equation M = E - e sin E for E. Returns E in radians."""
    M = (M + math.pi) % (2 * math.pi) - math.pi   # wrap to [-π, π]
    E = M if e < 0.8 else math.pi
    for _ in range(max_iter):
        f = E - e * math.sin(E) - M
        fp = 1 - e * math.cos(E)
        dE = f / fp
        E -= dE
        if abs(dE) < tol:
            break
    return E


def compute_heliocentric_position(elements: dict[str, Any],
                                  jd: float) -> dict[str, float]:
    """Propagate stored Keplerian elements to a Julian Date.

    Returns heliocentric ecliptic coordinates (x, y, z) in AU, plus radius (AU)
    and true anomaly (deg). Two-body only — accurate to a fraction of a percent
    for the planets, somewhat worse for highly-perturbed minor bodies. Use JPL
    Horizons (https://ssd.jpl.nasa.gov/horizons/) for high-precision needs.

    `elements` must include: semi_major_axis_au, eccentricity, inclination_deg,
    longitude_ascending_node_deg, argument_periapsis_deg, mean_anomaly_deg,
    epoch_jd, orbital_period_days.
    """
    a = elements.get("semi_major_axis_au")
    e = elements.get("eccentricity")
    i_deg = elements.get("inclination_deg")
    Omega_deg = elements.get("longitude_ascending_node_deg")
    omega_deg = elements.get("argument_periapsis_deg")
    M0_deg = elements.get("mean_anomaly_deg")
    epoch_jd = elements.get("epoch_jd")
    per_days = elements.get("orbital_period_days")

    missing = [k for k, v in {
        "semi_major_axis_au": a, "eccentricity": e, "inclination_deg": i_deg,
        "longitude_ascending_node_deg": Omega_deg,
        "argument_periapsis_deg": omega_deg, "mean_anomaly_deg": M0_deg,
        "epoch_jd": epoch_jd,
    }.items() if v is None]
    if missing:
        raise ValueError(
            f"Cannot compute position — missing orbital elements: {missing}. "
            f"Try a body with complete elements (planets, named asteroids, "
            f"comets, dwarf planets)."
        )

    i = math.radians(i_deg)
    Omega = math.radians(Omega_deg)
    omega = math.radians(omega_deg)

    # Mean motion (rad/day). Use stored period if reasonable, else derive from a.
    if per_days and per_days > 0:
        n = 2 * math.pi / per_days
    else:
        n = math.sqrt(GM_SUN / (a ** 3))

    dt = jd - epoch_jd
    M = math.radians(M0_deg) + n * dt

    E = solve_kepler(M, e)
    # True anomaly
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2),
                        math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))

    # Position in orbital plane (perifocal)
    x_p = r * math.cos(nu)
    y_p = r * math.sin(nu)

    # Rotate to ecliptic (J2000) frame
    cos_O, sin_O = math.cos(Omega), math.sin(Omega)
    cos_i, sin_i = math.cos(i), math.sin(i)
    cos_w, sin_w = math.cos(omega), math.sin(omega)

    x = (cos_O * cos_w - sin_O * sin_w * cos_i) * x_p \
        + (-cos_O * sin_w - sin_O * cos_w * cos_i) * y_p
    y = (sin_O * cos_w + cos_O * sin_w * cos_i) * x_p \
        + (-sin_O * sin_w + cos_O * cos_w * cos_i) * y_p
    z = (sin_w * sin_i) * x_p + (cos_w * sin_i) * y_p

    return {
        "x_au": x, "y_au": y, "z_au": z,
        "distance_from_sun_au": r,
        "true_anomaly_deg": math.degrees(nu) % 360,
        "eccentric_anomaly_deg": math.degrees(E) % 360,
        "mean_anomaly_deg": math.degrees(M) % 360,
        "jd": jd,
        "frame": "ecliptic J2000, heliocentric, two-body propagation",
        "accuracy_note": (
            "Two-body Kepler propagation — accurate to ~0.1% for major planets "
            "over decades, less so for highly-perturbed bodies. Use JPL Horizons "
            "for high precision."
        ),
    }


def next_perihelion_jd(elements: dict[str, Any], after_jd: float | None = None) -> float:
    """JD of the next perihelion passage after `after_jd` (default: today UTC).

    For a Kepler orbit, perihelion happens whenever the mean anomaly hits 0.
    Two-body — long-period or strongly perturbed bodies will drift; use JPL
    Horizons for mission-planning precision.
    """
    n_per_days = elements.get("orbital_period_days")
    epoch_jd = elements.get("epoch_jd")
    M0_deg = elements.get("mean_anomaly_deg")
    if not (n_per_days and epoch_jd is not None and M0_deg is not None):
        raise ValueError("Need orbital_period_days, epoch_jd and mean_anomaly_deg")

    if after_jd is None:
        after_jd = date_to_jd(datetime.now(timezone.utc))
    # JD at which M = 0:    jd_peri = epoch_jd - M0_deg / 360 * period
    jd_peri = epoch_jd - (M0_deg / 360.0) * n_per_days
    # Advance by whole periods until we're past `after`
    while jd_peri <= after_jd:
        jd_peri += n_per_days
    return jd_peri
