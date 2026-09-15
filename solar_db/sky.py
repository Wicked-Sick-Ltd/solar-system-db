"""Sky-position maths: where an object appears from Earth.

Builds on positions.compute_heliocentric_position (heliocentric ecliptic
J2000). Two-body throughout, with no nutation, aberration or light-time, so
expect roughly a degree for the planets: enough to name the constellation
and say whether something is above the horizon. For precision use JPL
Horizons.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any

from .positions import compute_heliocentric_position

OBLIQUITY_J2000_DEG = 23.4392911
HORIZON_ALT_DEG = -0.5667      # geometric horizon including mean refraction
DARK_SUN_ALT_DEG = -6.0        # civil twilight
EQUATORIAL_BAND_DEG = 15.0
SIDEREAL_RATE = 360.98564736629  # degrees of GMST per UT day

FRAME = "equatorial J2000, geocentric; alt/az topocentric; two-body propagation"
ACCURACY_NOTE = (
    "Two-body approximation, good to about a degree for the planets — "
    "enough to find the constellation and know whether it is up."
)

Vec = tuple[float, float, float]


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------
def ecliptic_to_equatorial(x: float, y: float, z: float) -> Vec:
    e = math.radians(OBLIQUITY_J2000_DEG)
    return x, y * math.cos(e) - z * math.sin(e), y * math.sin(e) + z * math.cos(e)


def format_hms(ra_deg: float) -> str:
    h = (ra_deg % 360) / 15
    hh = int(h)
    m = (h - hh) * 60
    mm = int(m)
    ss = int(round((m - mm) * 60))
    if ss == 60:
        ss, mm = 0, mm + 1
    if mm == 60:
        mm, hh = 0, (hh + 1) % 24
    return f"{hh:02d}h {mm:02d}m {ss:02d}s"


def format_dms(dec_deg: float) -> str:
    sign = "-" if dec_deg < 0 else "+"
    d = abs(dec_deg)
    dd = int(d)
    m = (d - dd) * 60
    mm = int(m)
    ss = int(round((m - mm) * 60))
    if ss == 60:
        ss, mm = 0, mm + 1
    if mm == 60:
        mm, dd = 0, dd + 1
    return f"{sign}{dd:02d}° {mm:02d}′ {ss:02d}″"


def geocentric_equatorial(target_xyz: Vec, earth_xyz: Vec) -> dict[str, Any]:
    """Geocentric RA/Dec (J2000) and Earth distance from two heliocentric ecliptic vectors."""
    gx, gy, gz = (t - e for t, e in zip(target_xyz, earth_xyz))
    x, y, z = ecliptic_to_equatorial(gx, gy, gz)
    r = math.sqrt(x * x + y * y + z * z)
    ra = math.degrees(math.atan2(y, x)) % 360
    dec = math.degrees(math.asin(z / r)) if r else 0.0
    return {
        "ra_deg": ra, "ra_hours": ra / 15, "ra_hms": format_hms(ra),
        "dec_deg": dec, "dec_dms": format_dms(dec),
        "distance_from_earth_au": r,
    }


def elongation_deg(target_xyz: Vec, earth_xyz: Vec) -> float:
    """Angle at Earth between the Sun and the target (0 = behind/in front of the Sun, 180 = opposition)."""
    s = [-c for c in earth_xyz]
    t = [a - b for a, b in zip(target_xyz, earth_xyz)]
    ns = math.sqrt(sum(c * c for c in s))
    nt = math.sqrt(sum(c * c for c in t))
    if ns == 0 or nt == 0:
        return 0.0
    dot = sum(a * b for a, b in zip(s, t))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (ns * nt)))))


def hemisphere(dec_deg: float) -> tuple[str, str]:
    if abs(dec_deg) <= EQUATORIAL_BAND_DEG:
        return "equatorial", "Visible from both hemispheres."
    if dec_deg > 0:
        return "northern", "Favours the northern hemisphere; low or below the horizon from far south."
    return "southern", "Favours the southern hemisphere; low or below the horizon from far north."


# ---------------------------------------------------------------------------
# Constellations (Roman 1987 boundary walk over the vendored IAU table)
# ---------------------------------------------------------------------------
_CONSTELLATION_NAMES = {
    "And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aqr": "Aquarius", "Aql": "Aquila",
    "Ara": "Ara", "Ari": "Aries", "Aur": "Auriga", "Boo": "Boötes", "Cae": "Caelum",
    "Cam": "Camelopardalis", "Cnc": "Cancer", "CVn": "Canes Venatici", "CMa": "Canis Major",
    "CMi": "Canis Minor", "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia",
    "Cen": "Centaurus", "Cep": "Cepheus", "Cet": "Cetus", "Cha": "Chamaeleon", "Cir": "Circinus",
    "Col": "Columba", "Com": "Coma Berenices", "CrA": "Corona Australis", "CrB": "Corona Borealis",
    "Crv": "Corvus", "Crt": "Crater", "Cru": "Crux", "Cyg": "Cygnus", "Del": "Delphinus",
    "Dor": "Dorado", "Dra": "Draco", "Equ": "Equuleus", "Eri": "Eridanus", "For": "Fornax",
    "Gem": "Gemini", "Gru": "Grus", "Her": "Hercules", "Hor": "Horologium", "Hya": "Hydra",
    "Hyi": "Hydrus", "Ind": "Indus", "Lac": "Lacerta", "Leo": "Leo", "LMi": "Leo Minor",
    "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus", "Lyn": "Lynx", "Lyr": "Lyra", "Men": "Mensa",
    "Mic": "Microscopium", "Mon": "Monoceros", "Mus": "Musca", "Nor": "Norma", "Oct": "Octans",
    "Oph": "Ophiuchus", "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus", "Per": "Perseus",
    "Phe": "Phoenix", "Pic": "Pictor", "Psc": "Pisces", "PsA": "Piscis Austrinus", "Pup": "Puppis",
    "Pyx": "Pyxis", "Ret": "Reticulum", "Sge": "Sagitta", "Sgr": "Sagittarius", "Sco": "Scorpius",
    "Scl": "Sculptor", "Sct": "Scutum", "Ser": "Serpens", "Sex": "Sextans", "Tau": "Taurus",
    "Tel": "Telescopium", "Tri": "Triangulum", "TrA": "Triangulum Australe", "Tuc": "Tucana",
    "UMa": "Ursa Major", "UMi": "Ursa Minor", "Vel": "Vela", "Vir": "Virgo", "Vol": "Volans",
    "Vul": "Vulpecula",
}
_BOUNDARIES: list[tuple[float, float, float, str]] | None = None


def _boundaries() -> list[tuple[float, float, float, str]]:
    global _BOUNDARIES
    if _BOUNDARIES is None:
        rows = []
        text = (resources.files("solar_db") / "data" / "constellation_boundaries.dat").read_text()
        for line in text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            lo, hi, dec, abbr = line.split()
            rows.append((float(lo), float(hi), float(dec), abbr))
        _BOUNDARIES = rows
    return _BOUNDARIES


def _precess_j2000_to_b1875(ra_deg: float, dec_deg: float) -> tuple[float, float]:
    # Meeus, Astronomical Algorithms ch. 21 (rigorous), from J2000.0 to B1875.0.
    T = (2405889.25855 - 2451545.0) / 36525.0
    zeta = math.radians((2306.2181 * T + 0.30188 * T * T + 0.017998 * T ** 3) / 3600)
    zed = math.radians((2306.2181 * T + 1.09468 * T * T + 0.018203 * T ** 3) / 3600)
    theta = math.radians((2004.3109 * T - 0.42665 * T * T - 0.041833 * T ** 3) / 3600)
    a = math.radians(ra_deg)
    d = math.radians(dec_deg)
    A = math.cos(d) * math.sin(a + zeta)
    B = math.cos(theta) * math.cos(d) * math.cos(a + zeta) - math.sin(theta) * math.sin(d)
    C = math.sin(theta) * math.cos(d) * math.cos(a + zeta) + math.cos(theta) * math.sin(d)
    return (math.degrees(math.atan2(A, B) + zed) % 360,
            math.degrees(math.asin(max(-1.0, min(1.0, C)))))


def constellation(ra_deg: float, dec_deg: float) -> dict[str, str]:
    """IAU constellation containing a J2000 position."""
    ra1875, dec1875 = _precess_j2000_to_b1875(ra_deg, dec_deg)
    ra_h = ra1875 / 15
    for lo, hi, dec_lo, abbr in _boundaries():
        if dec1875 >= dec_lo and lo <= ra_h < hi:
            return {"abbr": abbr, "name": _CONSTELLATION_NAMES[abbr]}
    return {"abbr": "Oct", "name": "Octans"}  # only at the exact south-pole edge


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------
def gmst_deg(jd: float) -> float:
    """Greenwich mean sidereal time in degrees (Meeus 12.4), UT1 ≈ UTC."""
    d = jd - 2451545.0
    T = d / 36525.0
    return (280.46061837 + SIDEREAL_RATE * d + 0.000387933 * T * T - T ** 3 / 38710000.0) % 360


def topocentric(ra_deg: float, dec_deg: float, lat: float, lon: float, jd: float) -> dict[str, float]:
    """Altitude/azimuth (azimuth from north through east) for an observer at lat/lon (east-positive)."""
    H = math.radians((gmst_deg(jd) + lon - ra_deg) % 360)
    phi = math.radians(lat)
    d = math.radians(dec_deg)
    alt = math.asin(math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(H))
    az = math.atan2(-math.sin(H) * math.cos(d),
                    math.sin(d) * math.cos(phi) - math.cos(d) * math.sin(phi) * math.cos(H))
    return {"altitude_deg": math.degrees(alt), "azimuth_deg": math.degrees(az) % 360,
            "hour_angle_deg": math.degrees(H)}


def _jd_to_iso(jd: float) -> str:
    dt = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rise_transit_set(ra_deg: float, dec_deg: float, lat: float, lon: float, jd: float) -> dict[str, Any]:
    """Rise, transit and set (UTC ISO) on the civil UT day containing jd; single-pass Meeus ch. 15."""
    phi = math.radians(lat)
    d = math.radians(dec_deg)
    denom = math.cos(phi) * math.cos(d)
    cos_h0 = ((math.sin(math.radians(HORIZON_ALT_DEG)) - math.sin(phi) * math.sin(d)) / denom
              if denom else 2.0)
    jd0 = math.floor(jd - 0.5) + 0.5           # 0h UT of the civil day
    theta0 = gmst_deg(jd0)
    m_transit = ((ra_deg - lon - theta0) % 360) / SIDEREAL_RATE
    out: dict[str, Any] = {"rise_utc": None, "transit_utc": _jd_to_iso(jd0 + m_transit), "set_utc": None,
                           "circumpolar": False, "never_rises": False}
    if cos_h0 < -1:
        out["circumpolar"] = True
        return out
    if cos_h0 > 1:
        out["never_rises"] = True
        out["transit_utc"] = None
        return out
    h0 = math.degrees(math.acos(cos_h0)) / SIDEREAL_RATE   # fraction of a day
    out["rise_utc"] = _jd_to_iso(jd0 + (m_transit - h0) % 1)
    out["set_utc"] = _jd_to_iso(jd0 + (m_transit + h0) % 1)
    return out


def observer_view(target_eq: dict[str, Any], sun_eq: dict[str, Any],
                  lat: float, lon: float, jd: float) -> dict[str, Any]:
    t = topocentric(target_eq["ra_deg"], target_eq["dec_deg"], lat, lon, jd)
    s = topocentric(sun_eq["ra_deg"], sun_eq["dec_deg"], lat, lon, jd)
    rts = rise_transit_set(target_eq["ra_deg"], target_eq["dec_deg"], lat, lon, jd)
    return {
        "lat": round(lat, 2), "lon": round(lon, 2),
        "altitude_deg": t["altitude_deg"], "azimuth_deg": t["azimuth_deg"],
        "is_up": t["altitude_deg"] > HORIZON_ALT_DEG,
        "sun_altitude_deg": s["altitude_deg"], "is_dark": s["altitude_deg"] < DARK_SUN_ALT_DEG,
        **rts,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def sky_report(target_elements: dict[str, Any] | None, earth_elements: dict[str, Any], jd: float, *,
               lat: float | None = None, lon: float | None = None, is_sun: bool = False) -> dict[str, Any]:
    """Everything the /sky endpoint returns except the object's identity fields."""
    e = compute_heliocentric_position(earth_elements, jd)
    earth_xyz: Vec = (e["x_au"], e["y_au"], e["z_au"])
    if is_sun:
        target_xyz: Vec = (0.0, 0.0, 0.0)
        dist_sun = 0.0
    else:
        if target_elements is None:
            raise ValueError("target_elements required unless is_sun=True")
        t = compute_heliocentric_position(target_elements, jd)
        target_xyz = (t["x_au"], t["y_au"], t["z_au"])
        dist_sun = t["distance_from_sun_au"]

    eq = geocentric_equatorial(target_xyz, earth_xyz)
    hemi, sentence = hemisphere(eq["dec_deg"])
    report: dict[str, Any] = {
        **eq,
        "distance_from_sun_au": dist_sun,
        "elongation_deg": 0.0 if is_sun else elongation_deg(target_xyz, earth_xyz),
        "constellation": constellation(eq["ra_deg"], eq["dec_deg"]),
        "hemisphere": hemi,
        "visible_from": sentence,
        "jd": jd,
        "observer": None,
        "frame": FRAME,
        "accuracy_note": ACCURACY_NOTE,
    }
    if lat is not None and lon is not None:
        sun_eq = eq if is_sun else geocentric_equatorial((0.0, 0.0, 0.0), earth_xyz)
        report["observer"] = observer_view(eq, sun_eq, lat, lon, jd)
    return report
