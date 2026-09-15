# Sky Position (backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/v1/sky/{id}` (and an MCP tool) returning an object's RA/Dec, constellation, hemisphere, Earth distance and elongation, plus optional observer-specific altitude/azimuth and rise/transit/set.

**Architecture:** A pure-maths module `solar_db/sky.py` builds on the existing two-body propagator: Earth's and the target's heliocentric ecliptic vectors are differenced, rotated to equatorial J2000, and optionally projected to an observer via local sidereal time. Constellations come from the Roman (1987) boundary walk over the vendored IAU table. The FastAPI route and the MCP tool are thin wrappers.

**Tech Stack:** Python 3.10+, stdlib `math`/`datetime`, FastAPI + slowapi (existing), pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-sky-position-design.md`

## Global Constraints

- Two-body only; no perturbations, nutation, aberration or light-time (spec "Non-goals").
- J2000 mean obliquity `23.4392911°`; horizon altitude for rise/set `-0.5667°`; "dark" means Sun altitude `< -6°`.
- Hemisphere: `equatorial` when `|Dec| <= 15°`, else `northern` / `southern`.
- `lat` ∈ [−90, 90], `lon` ∈ [−180, 180], both or neither → else HTTP 422. Round both to 0.01° in the response.
- Moons resolve to their parent; `sun` is special-cased (geocentric vector = −Earth).
- Rate limit `60/minute` (same as `/positions`). 404 when no propagatable elements.
- No new runtime dependencies.

---

### Task 1: Vendor the IAU boundary table and package it

**Files:**
- Create: `solar_db/data/constellation_boundaries.dat` (357 rows, CDS VI/42 `data.dat`, public domain)
- Create: `solar_db/data/__init__.py` (empty)
- Modify: `pyproject.toml` (package data + `tests` in testpaths)
- Modify: `.github/workflows/test.yml:35` (add `tests/`)
- Test: `tests/test_sky_constellations.py`

**Interfaces:**
- Produces: the data file at `importlib.resources.files("solar_db") / "data" / "constellation_boundaries.dat"`; each line `RA_lo_h RA_hi_h Dec_lo_deg ABBR` in B1875 coordinates.

- [ ] Step 1: Copy the fetched `data.dat` (357 lines, first line `  0.0000 24.0000  88.0000 UMi`, last `  0.0000 24.0000 -90.0000 Oct`) to `solar_db/data/constellation_boundaries.dat`; add a 4-line comment header starting `#` (source URL, catalogue VI/42, Roman 1987, epoch B1875).
- [ ] Step 2: In `pyproject.toml` add
```toml
[tool.setuptools.package-data]
solar_db = ["data/*.dat"]
```
and change `testpaths = ["mcp-server/tests", "api/tests", "tests"]`. In the workflow run line add `tests/`.
- [ ] Step 3: Write the failing test `tests/test_sky_constellations.py`:
```python
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
```
- [ ] Step 4: `pytest tests/test_sky_constellations.py -q` → fails with `ModuleNotFoundError: solar_db.sky`.
- [ ] Step 5: Commit `chore(data): vendor IAU constellation boundaries (CDS VI/42)`.

### Task 2: `solar_db/sky.py` — geocentric equatorial, elongation, hemisphere, constellation

**Files:**
- Create: `solar_db/sky.py`
- Modify: `solar_db/__init__.py` (export nothing new; callers import `solar_db.sky`)
- Test: `tests/test_sky_math.py`, `tests/test_sky_constellations.py` (from Task 1)

**Interfaces (produces):**
```python
OBLIQUITY_J2000_DEG = 23.4392911
def ecliptic_to_equatorial(x, y, z) -> tuple[float, float, float]
def geocentric_equatorial(target_xyz: tuple[float,float,float], earth_xyz: tuple[float,float,float]) -> dict
    # keys: ra_deg, ra_hours, ra_hms, dec_deg, dec_dms, distance_from_earth_au
def elongation_deg(target_xyz, earth_xyz) -> float
def hemisphere(dec_deg) -> tuple[str, str]   # ("northern"|"southern"|"equatorial", sentence)
def constellation(ra_deg, dec_deg) -> dict   # {"abbr": "Ori", "name": "Orion"}
def format_hms(ra_deg) -> str; def format_dms(dec_deg) -> str
```

- [ ] Step 1: Failing tests `tests/test_sky_math.py`:
```python
import math
from solar_db.sky import (ecliptic_to_equatorial, geocentric_equatorial, elongation_deg,
                          hemisphere, format_hms, format_dms)

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
```
- [ ] Step 2: `pytest tests/test_sky_math.py -q` → ImportError.
- [ ] Step 3: Implement `solar_db/sky.py` part 1:
```python
"""Sky-position maths: where an object appears from Earth.

Builds on positions.compute_heliocentric_position (heliocentric ecliptic
J2000). Two-body throughout, so expect ~1° for the planets: enough to name
the constellation and say whether something is above the horizon.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone, timedelta
from importlib import resources

OBLIQUITY_J2000_DEG = 23.4392911
HORIZON_ALT_DEG = -0.5667      # geometric horizon incl. refraction
DARK_SUN_ALT_DEG = -6.0        # civil twilight
EQUATORIAL_BAND_DEG = 15.0

def ecliptic_to_equatorial(x, y, z):
    e = math.radians(OBLIQUITY_J2000_DEG)
    return x, y * math.cos(e) - z * math.sin(e), y * math.sin(e) + z * math.cos(e)

def format_hms(ra_deg):
    h = (ra_deg % 360) / 15
    hh = int(h); m = (h - hh) * 60; mm = int(m); ss = int(round((m - mm) * 60))
    if ss == 60: ss = 0; mm += 1
    if mm == 60: mm = 0; hh = (hh + 1) % 24
    return f"{hh:02d}h {mm:02d}m {ss:02d}s"

def format_dms(dec_deg):
    sign = "-" if dec_deg < 0 else "+"
    d = abs(dec_deg); dd = int(d); m = (d - dd) * 60; mm = int(m); ss = int(round((m - mm) * 60))
    if ss == 60: ss = 0; mm += 1
    if mm == 60: mm = 0; dd += 1
    return f"{sign}{dd:02d}° {mm:02d}′ {ss:02d}″"

def geocentric_equatorial(target_xyz, earth_xyz):
    gx, gy, gz = (t - e for t, e in zip(target_xyz, earth_xyz))
    x, y, z = ecliptic_to_equatorial(gx, gy, gz)
    r = math.sqrt(x*x + y*y + z*z)
    ra = math.degrees(math.atan2(y, x)) % 360
    dec = math.degrees(math.asin(z / r)) if r else 0.0
    return {"ra_deg": ra, "ra_hours": ra / 15, "ra_hms": format_hms(ra),
            "dec_deg": dec, "dec_dms": format_dms(dec), "distance_from_earth_au": r}

def elongation_deg(target_xyz, earth_xyz):
    # angle at Earth between the Sun (-earth) and the target (target-earth)
    s = [-c for c in earth_xyz]; t = [a - b for a, b in zip(target_xyz, earth_xyz)]
    dot = sum(a*b for a, b in zip(s, t)); ns = math.sqrt(sum(c*c for c in s)); nt = math.sqrt(sum(c*c for c in t))
    if ns == 0 or nt == 0: return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (ns * nt)))))

def hemisphere(dec_deg):
    if abs(dec_deg) <= EQUATORIAL_BAND_DEG:
        return "equatorial", "Visible from both hemispheres."
    if dec_deg > 0:
        return "northern", "Favours the northern hemisphere; low or below the horizon from far south."
    return "southern", "Favours the southern hemisphere; low or below the horizon from far north."
```
plus the constellation section:
```python
_CONSTELLATION_NAMES = {"And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aqr": "Aquarius", "Aql": "Aquila", "Ara": "Ara", "Ari": "Aries", "Aur": "Auriga", "Boo": "Boötes", "Cae": "Caelum", "Cam": "Camelopardalis", "Cnc": "Cancer", "CVn": "Canes Venatici", "CMa": "Canis Major", "CMi": "Canis Minor", "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia", "Cen": "Centaurus", "Cep": "Cepheus", "Cet": "Cetus", "Cha": "Chamaeleon", "Cir": "Circinus", "Col": "Columba", "Com": "Coma Berenices", "CrA": "Corona Australis", "CrB": "Corona Borealis", "Crv": "Corvus", "Crt": "Crater", "Cru": "Crux", "Cyg": "Cygnus", "Del": "Delphinus", "Dor": "Dorado", "Dra": "Draco", "Equ": "Equuleus", "Eri": "Eridanus", "For": "Fornax", "Gem": "Gemini", "Gru": "Grus", "Her": "Hercules", "Hor": "Horologium", "Hya": "Hydra", "Hyi": "Hydrus", "Ind": "Indus", "Lac": "Lacerta", "Leo": "Leo", "LMi": "Leo Minor", "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus", "Lyn": "Lynx", "Lyr": "Lyra", "Men": "Mensa", "Mic": "Microscopium", "Mon": "Monoceros", "Mus": "Musca", "Nor": "Norma", "Oct": "Octans", "Oph": "Ophiuchus", "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus", "Per": "Perseus", "Phe": "Phoenix", "Pic": "Pictor", "Psc": "Pisces", "PsA": "Piscis Austrinus", "Pup": "Puppis", "Pyx": "Pyxis", "Ret": "Reticulum", "Sge": "Sagitta", "Sgr": "Sagittarius", "Sco": "Scorpius", "Scl": "Sculptor", "Sct": "Scutum", "Ser": "Serpens", "Sex": "Sextans", "Tau": "Taurus", "Tel": "Telescopium", "Tri": "Triangulum", "TrA": "Triangulum Australe", "Tuc": "Tucana", "UMa": "Ursa Major", "UMi": "Ursa Minor", "Vel": "Vela", "Vir": "Virgo", "Vol": "Volans", "Vul": "Vulpecula"}
_BOUNDARIES: list[tuple[float, float, float, str]] | None = None

def _boundaries():
    global _BOUNDARIES
    if _BOUNDARIES is None:
        rows = []
        text = (resources.files("solar_db") / "data" / "constellation_boundaries.dat").read_text()
        for line in text.splitlines():
            if not line.strip() or line.startswith("#"): continue
            lo, hi, dec, abbr = line.split()
            rows.append((float(lo), float(hi), float(dec), abbr))
        _BOUNDARIES = rows
    return _BOUNDARIES

def _precess_j2000_to_b1875(ra_deg, dec_deg):
    # Meeus, Astronomical Algorithms ch. 21, rigorous formulae, T from J2000 to B1875.0
    T = (2405889.25855 - 2451545.0) / 36525.0
    zeta = math.radians((2306.2181*T + 0.30188*T*T + 0.017998*T**3) / 3600)
    zed = math.radians((2306.2181*T + 1.09468*T*T + 0.018203*T**3) / 3600)
    theta = math.radians((2004.3109*T - 0.42665*T*T - 0.041833*T**3) / 3600)
    a = math.radians(ra_deg); d = math.radians(dec_deg)
    A = math.cos(d) * math.sin(a + zeta)
    B = math.cos(theta) * math.cos(d) * math.cos(a + zeta) - math.sin(theta) * math.sin(d)
    C = math.sin(theta) * math.cos(d) * math.cos(a + zeta) + math.cos(theta) * math.sin(d)
    return math.degrees(math.atan2(A, B) + zed) % 360, math.degrees(math.asin(max(-1.0, min(1.0, C))))

def constellation(ra_deg, dec_deg):
    ra1875, dec1875 = _precess_j2000_to_b1875(ra_deg, dec_deg)
    ra_h = ra1875 / 15
    for lo, hi, dec_lo, abbr in _boundaries():
        if dec1875 >= dec_lo and lo <= ra_h < hi:
            return {"abbr": abbr, "name": _CONSTELLATION_NAMES[abbr]}
    return {"abbr": "Oct", "name": "Octans"}  # only reachable at the exact south pole edge
```
- [ ] Step 4: `pytest tests/test_sky_math.py tests/test_sky_constellations.py -q` → all pass.
- [ ] Step 5: Commit `feat(sky): geocentric RA/Dec, elongation, hemisphere and constellation lookup`.

### Task 3: Observer maths — sidereal time, alt/az, rise/transit/set, Sun altitude

**Files:**
- Modify: `solar_db/sky.py`
- Test: `tests/test_sky_observer.py`

**Interfaces (produces):**
```python
def gmst_deg(jd: float) -> float
def topocentric(ra_deg, dec_deg, lat, lon, jd) -> dict   # altitude_deg, azimuth_deg, hour_angle_deg
def rise_transit_set(ra_deg, dec_deg, lat, lon, jd) -> dict
    # rise_utc, transit_utc, set_utc (ISO strings or None), circumpolar: bool, never_rises: bool
def observer_view(target_eq: dict, sun_eq: dict, lat, lon, jd) -> dict  # the API's "observer" block
```

- [ ] Step 1: Failing tests:
```python
import math
from solar_db.sky import gmst_deg, topocentric, rise_transit_set

J2000 = 2451545.0

def test_gmst_at_j2000():
    # Meeus example 12.a: 1987-04-10 0h UT -> GMST 13h10m46.3668s = 197.693195 deg
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

def test_rise_before_transit_before_set():
    r = rise_transit_set(120.0, 10.0, 51.5, -0.12, 2461299.5)
    assert r["rise_utc"] < r["transit_utc"] < r["set_utc"] or r["rise_utc"] > r["set_utc"]  # may wrap midnight
    assert r["circumpolar"] is False and r["never_rises"] is False
```
- [ ] Step 2: `pytest tests/test_sky_observer.py -q` → ImportError.
- [ ] Step 3: Implement:
```python
def gmst_deg(jd):
    T = (jd - 2451545.0) / 36525.0
    return (280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933*T*T - T**3/38710000.0) % 360

def topocentric(ra_deg, dec_deg, lat, lon, jd):
    H = math.radians((gmst_deg(jd) + lon - ra_deg) % 360)
    phi = math.radians(lat); d = math.radians(dec_deg)
    alt = math.asin(math.sin(phi)*math.sin(d) + math.cos(phi)*math.cos(d)*math.cos(H))
    az = math.atan2(-math.sin(H)*math.cos(d), math.sin(d)*math.cos(phi) - math.cos(d)*math.sin(phi)*math.cos(H))
    return {"altitude_deg": math.degrees(alt), "azimuth_deg": math.degrees(az) % 360,
            "hour_angle_deg": math.degrees(H)}

def _jd_to_iso(jd):
    dt = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def rise_transit_set(ra_deg, dec_deg, lat, lon, jd):
    phi = math.radians(lat); d = math.radians(dec_deg)
    cosH0 = (math.sin(math.radians(HORIZON_ALT_DEG)) - math.sin(phi)*math.sin(d)) / (math.cos(phi)*math.cos(d))
    jd0 = math.floor(jd - 0.5) + 0.5           # 0h UT of the civil day
    theta0 = gmst_deg(jd0)
    m_transit = ((ra_deg - lon - theta0) % 360) / 360.98564736629
    out = {"rise_utc": None, "transit_utc": _jd_to_iso(jd0 + m_transit), "set_utc": None,
           "circumpolar": False, "never_rises": False}
    if cosH0 < -1: out["circumpolar"] = True; return out
    if cosH0 > 1: out["never_rises"] = True; out["transit_utc"] = None; return out
    H0 = math.degrees(math.acos(cosH0)) / 360.98564736629 * 360  # in sidereal-day fraction
    out["rise_utc"] = _jd_to_iso(jd0 + (m_transit - H0/360) % 1)
    out["set_utc"] = _jd_to_iso(jd0 + (m_transit + H0/360) % 1)
    return out

def observer_view(target_eq, sun_eq, lat, lon, jd):
    t = topocentric(target_eq["ra_deg"], target_eq["dec_deg"], lat, lon, jd)
    s = topocentric(sun_eq["ra_deg"], sun_eq["dec_deg"], lat, lon, jd)
    rts = rise_transit_set(target_eq["ra_deg"], target_eq["dec_deg"], lat, lon, jd)
    return {"lat": round(lat, 2), "lon": round(lon, 2),
            "altitude_deg": t["altitude_deg"], "azimuth_deg": t["azimuth_deg"],
            "is_up": t["altitude_deg"] > HORIZON_ALT_DEG,
            "sun_altitude_deg": s["altitude_deg"], "is_dark": s["altitude_deg"] < DARK_SUN_ALT_DEG,
            **rts}
```
- [ ] Step 4: `pytest tests -q` → pass.
- [ ] Step 5: Commit `feat(sky): observer altitude/azimuth and rise/transit/set`.

### Task 4: `sky_report()` orchestration + Horizons regression tests

**Files:**
- Modify: `solar_db/sky.py`, `solar_db/__init__.py`
- Test: `tests/test_sky_report.py`

**Interfaces (produces):**
```python
def sky_report(target_elements: dict, earth_elements: dict, jd: float, *, lat=None, lon=None, is_sun=False) -> dict
```
Returns the full response body from the spec (minus `name`/`designation`, which the route adds).

- [ ] Step 1: Failing test (Horizons, geocentric astrometric J2000, 2026-09-15 00:00 UTC):
```python
from solar_db import SolarDB
from solar_db.sky import sky_report
from solar_db.positions import date_to_jd

JD = date_to_jd("2026-09-15T00:00:00Z")
HORIZONS = {  # id: (ra_deg, dec_deg, delta_au, elongation_deg)
    "planet-mars": (113.63084, 22.42036, 1.7691, 60.07),
    "planet-jupiter": (138.82417, 16.51379, 6.0868, 35.63),
    "dwarf-pluto": (306.27211, -23.68704, 34.9317, 130.96),
}

def test_matches_horizons_within_a_degree():
    db = SolarDB(); earth = db.get_orbital_elements("planet-earth")
    for oid, (ra, dec, delta, elong) in HORIZONS.items():
        r = sky_report(db.get_orbital_elements(oid), earth, JD)
        assert abs(((r["ra_deg"] - ra + 180) % 360) - 180) < 1.0, oid
        assert abs(r["dec_deg"] - dec) < 1.0, oid
        assert abs(r["distance_from_earth_au"] - delta) / delta < 0.02, oid
        assert abs(r["elongation_deg"] - elong) < 1.5, oid

def test_sun():
    db = SolarDB(); earth = db.get_orbital_elements("planet-earth")
    r = sky_report(None, earth, JD, is_sun=True)
    assert abs(r["ra_deg"] - 172.48327) < 1.0 and abs(r["dec_deg"] - 3.24575) < 1.0
    assert r["elongation_deg"] == 0.0

def test_observer_block_present_only_with_coordinates():
    db = SolarDB(); earth = db.get_orbital_elements("planet-earth"); mars = db.get_orbital_elements("planet-mars")
    assert sky_report(mars, earth, JD)["observer"] is None
    o = sky_report(mars, earth, JD, lat=51.5, lon=-0.12)["observer"]
    assert set(o) >= {"altitude_deg", "azimuth_deg", "is_up", "is_dark", "rise_utc", "transit_utc", "set_utc"}
```
- [ ] Step 2: run → ImportError.
- [ ] Step 3: Implement:
```python
from .positions import compute_heliocentric_position

FRAME = "equatorial J2000, geocentric; alt/az topocentric; two-body propagation"
ACCURACY_NOTE = ("Two-body approximation, good to about a degree for the planets — "
                 "enough to find the constellation and know whether it is up.")

def sky_report(target_elements, earth_elements, jd, *, lat=None, lon=None, is_sun=False):
    e = compute_heliocentric_position(earth_elements, jd)
    earth_xyz = (e["x_au"], e["y_au"], e["z_au"])
    if is_sun:
        target_xyz, dist_sun = (0.0, 0.0, 0.0), 0.0
    else:
        t = compute_heliocentric_position(target_elements, jd)
        target_xyz, dist_sun = (t["x_au"], t["y_au"], t["z_au"]), t["distance_from_sun_au"]
    eq = geocentric_equatorial(target_xyz, earth_xyz)
    hemi, sentence = hemisphere(eq["dec_deg"])
    report = {**eq, "distance_from_sun_au": dist_sun,
              "elongation_deg": 0.0 if is_sun else elongation_deg(target_xyz, earth_xyz),
              "constellation": constellation(eq["ra_deg"], eq["dec_deg"]),
              "hemisphere": hemi, "visible_from": sentence, "jd": jd, "observer": None,
              "frame": FRAME, "accuracy_note": ACCURACY_NOTE}
    if lat is not None and lon is not None:
        sun_eq = eq if is_sun else geocentric_equatorial((0.0, 0.0, 0.0), earth_xyz)
        report["observer"] = observer_view(eq, sun_eq, lat, lon, jd)
    return report
```
Export `sky_report` from `solar_db/__init__.py`.
- [ ] Step 4: `pytest tests -q` → pass (if Pluto misses by >1°, widen only Pluto's tolerance to 2° and note why in the test: SBDB epoch 2016).
- [ ] Step 5: Commit `feat(sky): sky_report orchestration with Horizons regression tests`.

### Task 5: API route `GET /api/v1/sky/{id}`

**Files:**
- Modify: `api/main.py` (after `compute_position`)
- Test: `api/tests/test_api_smoke.py` (append)

- [ ] Step 1: Failing tests:
```python
def test_sky_mars(client):
    r = client.get("/api/v1/sky/Mars", params={"date": "2026-09-15T00:00:00Z"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "Mars" and d["constellation"]["abbr"] == "Gem" and d["observer"] is None

def test_sky_observer(client):
    r = client.get("/api/v1/sky/Mars", params={"date": "2026-09-15T21:00:00Z", "lat": 51.5, "lon": -0.12})
    assert r.status_code == 200 and r.json()["observer"]["lat"] == 51.5

def test_sky_lat_without_lon_is_422(client):
    assert client.get("/api/v1/sky/Mars", params={"lat": 51.5}).status_code == 422

def test_sky_moon_uses_parent(client):
    r = client.get("/api/v1/sky/Titan", params={"date": "2026-09-15"})
    assert r.status_code == 200 and r.json()["resolved_from"] == "planet-saturn"

def test_sky_sun(client):
    assert client.get("/api/v1/sky/sun", params={"date": "2026-09-15"}).json()["elongation_deg"] == 0.0

def test_sky_404_without_elements(client):
    assert client.get("/api/v1/sky/nothing-here-xyz").status_code == 404
```
- [ ] Step 2: run → 404s.
- [ ] Step 3: Implement route:
```python
from solar_db.sky import sky_report

@app.get("/api/v1/sky/{name_or_designation:path}", tags=["positions"],
         summary="Where an object appears in the sky (RA/Dec, constellation, optional observer view)")
@limiter.limit("60/minute")
def sky_position(request: Request, name_or_designation: str,
                 date: Optional[str] = Query(None, description="ISO date/datetime UTC; default now"),
                 lat: Optional[float] = Query(None, ge=-90, le=90),
                 lon: Optional[float] = Query(None, ge=-180, le=180)):
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="Provide both lat and lon, or neither.")
    earth = db.get_orbital_elements("planet-earth")
    obj = db.get_object(name_or_designation)
    if not obj:
        raise HTTPException(status_code=404, detail=f"No object found matching {name_or_designation!r}")
    resolved_from = None
    is_sun = obj["id"] == "sun"
    elements = None
    if not is_sun:
        elements = db.get_orbital_elements(obj["id"])
        if (not elements or elements.get("semi_major_axis_au") is None) and obj.get("object_type") == "moon" and obj.get("parent_id"):
            elements = db.get_orbital_elements(obj["parent_id"]); resolved_from = obj["parent_id"]
        if not elements or elements.get("semi_major_axis_au") is None:
            raise HTTPException(status_code=404, detail=f"No propagatable orbital elements for {obj['name']!r}")
    try:
        jd = date_to_jd(date) if date else date_to_jd(datetime.now(timezone.utc))
        report = sky_report(elements, earth, jd, lat=lat, lon=lon, is_sun=is_sun)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"name": obj["name"], "designation": obj.get("designation"),
            "input_datetime": date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "resolved_from": resolved_from, **report}
```
(Moon elements are planetocentric so they must not be propagated heliocentrically: treat any moon as "use parent" regardless of whether it has elements.) Adjust: `if obj.get("object_type") == "moon" and obj.get("parent_id"): elements = db.get_orbital_elements(obj["parent_id"]); resolved_from = obj["parent_id"]`.
- [ ] Step 4: `pytest -q` → pass.
- [ ] Step 5: Commit `feat(api): GET /api/v1/sky/{id}`.

### Task 6: MCP tool + docs

**Files:**
- Modify: `mcp-server/server.py` (after `compute_position`), `README.md` (endpoint table), `mcp-server/tests/test_server.py` if a position test exists (mirror it)

- [ ] Step 1: Add
```python
@mcp.tool()
def get_sky_position(name_or_designation: str, date: str | None = None,
                     lat: float | None = None, lon: float | None = None) -> dict:
    """Where an object appears in Earth's sky: RA/Dec (J2000), constellation,
    hemisphere, distance from Earth and elongation from the Sun. Give lat/lon
    (degrees, east-positive) to add altitude/azimuth, whether it is up after
    dark, and rise/transit/set for that date. Two-body accuracy (~1°)."""
```
calling the same resolution logic (factor it into `solar_db/sky_lookup.py::resolve_and_report(db, name, date, lat, lon)` used by both the API and MCP so they cannot drift; move the route's body there in this task).
- [ ] Step 2: README: add the `/sky` row and one curl example.
- [ ] Step 3: `pytest -q` → pass; commit `feat(mcp): get_sky_position tool; docs`.

## Self-review

- Spec coverage: RA/Dec/hms/dms ✔ T2; elongation ✔ T2; hemisphere ✔ T2; constellation + vendored table ✔ T1/T2; observer alt/az, dark, rise/set ✔ T3; endpoint validation, 404, moon→parent, sun ✔ T5; MCP ✔ T6; tests listed in spec ✔ T1–T5; rounding lat/lon ✔ T3 `observer_view`.
- Placeholders: none.
- Types: `sky_report(target_elements, earth_elements, jd, *, lat, lon, is_sun)` used identically in T4/T5/T6; `constellation()` returns `{"abbr","name"}` in T2 and asserted in T1/T5.
