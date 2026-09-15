"""JPL planetary satellite tables — every known natural satellite.

  https://ssd.jpl.nasa.gov/sats/elem/      mean orbital elements, all ~460 satellites
  https://ssd.jpl.nasa.gov/sats/phys_par/  GM / mean radius / density for the ~46 well-measured ones

The elements table is the authority for *which* moons exist; the curated seed
(seed_major / seed_moons) stays the authority for the values it carries, and
these tables fill everything else.
"""
from __future__ import annotations

import html
import re
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import add_source, fetch_text, upsert_object, upsert_row, upsert_row_fill  # noqa: E402

ELEM_URL = "https://ssd.jpl.nasa.gov/sats/elem/"
PHYS_URL = "https://ssd.jpl.nasa.gov/sats/phys_par/"
SOURCE_NAME = "JPL Solar System Dynamics — planetary satellites"
KM_PER_AU = 149_597_870.7
G_KM3_KG_S2 = 6.674_30e-20  # gravitational constant, km³ kg⁻¹ s⁻²

PARENT_IDS = {"Earth": "planet-earth", "Mars": "planet-mars", "Jupiter": "planet-jupiter",
              "Saturn": "planet-saturn", "Uranus": "planet-uranus", "Neptune": "planet-neptune",
              "Pluto": "dwarf-pluto"}
# JPL spellings / truncations that differ from the IAU names in our seed.
NAME_ALIASES = {"Moon": "Luna", "Magaclite": "Megaclite", "Philophrosyn": "Philophrosyne"}


# ---------------------------------------------------------------------------
# HTML parsing (no dependencies — the pages are plain tables)
# ---------------------------------------------------------------------------
def _cells(row_html: str) -> list[str]:
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[hd].*?</t[hd]>", row_html, re.S)]


def _first_table_rows(page: str) -> list[list[str]]:
    table = re.findall(r"<table.*?</table>", page, re.S)[0]
    return [_cells(r) for r in re.findall(r"<tr.*?</tr>", table, re.S)]


def _f(x: str | None) -> float | None:
    if x is None:
        return None
    x = x.strip().rstrip("*").replace(",", "")
    if x in ("", "n/a", "-", "—"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def match_key(name: str) -> str:
    """'S/2003 J 10', 'S2003_J_10' and 'moon-s/2003-j-10' all → 's2003j10'."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", n)


def display_name(jpl_name: str) -> str:
    """'S2010_J_1' → 'S/2010 J 1'; otherwise the IAU spelling."""
    m = re.fullmatch(r"S(\d{4})_([A-Z])_(\d+)", jpl_name)
    if m:
        return f"S/{m.group(1)} {m.group(2)} {m.group(3)}"
    return NAME_ALIASES.get(jpl_name, jpl_name)


def moon_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()).strip("-")
    return f"moon-{slug}"


def parse_elements(page: str) -> list[dict[str, Any]]:
    rows = _first_table_rows(page)
    head = [h.lower() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if len(r) < len(head):
            continue
        d = dict(zip(head, r))
        out.append({
            "planet": d["planet"], "jpl_name": d["satellite"], "code": int(d["code"]) if d["code"].isdigit() else None,
            "name": display_name(d["satellite"]), "ephemeris": d.get("ephemeris"), "frame": d.get("frame"),
            "epoch": d.get("epoch (tdb)"), "a_km": _f(d.get("a (km)")), "e": _f(d.get("e")),
            "w_deg": _f(d.get("ω (deg)")), "m_deg": _f(d.get("m (deg)")), "i_deg": _f(d.get("i (deg)")),
            "node_deg": _f(d.get("node (deg)")), "period_days": _f(d.get("p (days)")),
            "apsis_period_yr": _f(d.get("p apsis (yr)")), "node_period_yr": _f(d.get("p node (yr)")),
            "pole_ra_deg": _f(d.get("r.a. (deg)")), "pole_dec_deg": _f(d.get("dec. (deg)")), "tilt_deg": _f(d.get("tilt (deg)")),
        })
    return out


def parse_physical(page: str) -> list[dict[str, Any]]:
    rows = _first_table_rows(page)
    out = []
    for r in rows[2:]:  # two header rows
        if len(r) < 6:
            continue
        planet, sat, code, gm, radius, density = r[:6]
        gm_v, gm_s, *_ = (gm.split() + [None, None])[:3]
        r_v, r_s, *_ = (radius.split() + [None, None])[:3]
        d_v, d_s, *_ = (density.split() + [None, None])[:3]
        out.append({"planet": planet, "jpl_name": sat, "name": display_name(sat),
                    "code": int(code) if code.isdigit() else None,
                    "gm_km3_s2": _f(gm_v), "gm_sigma": _f(gm_s), "radius_km": _f(r_v), "radius_sigma": _f(r_s),
                    "density_g_cm3": _f(d_v), "density_sigma": _f(d_s)})
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def write_satellites(conn, elements: Iterable[dict[str, Any]], physical: Iterable[dict[str, Any]]) -> dict[str, int]:
    existing = {match_key(r[0][5:]): r[0] for r in conn.execute("SELECT id FROM objects WHERE object_type = 'moon'")}
    existing.update({match_key(r[1]): r[0] for r in conn.execute("SELECT id, name FROM objects WHERE object_type = 'moon'")})
    phys_by_key = {match_key(p["name"]): p for p in physical}
    created = updated = 0
    seen: set[tuple[str, str]] = set()
    for el in elements:
        parent = PARENT_IDS.get(el["planet"])
        if not parent:
            continue
        # JPL lists a few satellites under two ephemerides (e.g. Puck); keep the first.
        if (parent, match_key(el["name"])) in seen:
            continue
        seen.add((parent, match_key(el["name"])))
        key = match_key(el["name"])
        obj_id = existing.get(key) or existing.get(match_key(el["jpl_name"]))
        if obj_id is None:
            obj_id = moon_id(el["name"])
            upsert_object(conn, id=obj_id, name=el["name"], designation=el["name"] if el["name"].startswith("S/") else None,
                          object_type="moon", parent_id=parent)
            existing[key] = obj_id
            created += 1
        else:
            updated += 1
        conn.executemany(
            "INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, ?, ?)",
            [(obj_id, d, k, SOURCE_NAME) for d, k in {
                (el["name"], "name" if not el["name"].startswith("S/") else "provisional"),
                (el["jpl_name"], "alternate"),
                (f"NAIF {el['code']}", "alternate") if el["code"] else (el["name"], "name"),
            }],
        )
        a_au = el["a_km"] / KM_PER_AU if el["a_km"] else None
        orbital = {
            "epoch": el["epoch"], "frame": el["frame"], "centre": el["planet"],
            "semi_major_axis_au": a_au, "eccentricity": el["e"], "inclination_deg": el["i_deg"],
            "longitude_ascending_node_deg": el["node_deg"], "argument_periapsis_deg": el["w_deg"],
            "mean_anomaly_deg": el["m_deg"], "orbital_period_days": el["period_days"],
            "perihelion_au": a_au * (1 - el["e"]) if a_au is not None and el["e"] is not None else None,
            "aphelion_au": a_au * (1 + el["e"]) if a_au is not None and el["e"] is not None else None,
            "orbit_source": el["ephemeris"],
        }
        # Seed values (fact-sheet) win; JPL fills everything else.
        upsert_row_fill(conn, "orbital_elements", obj_id, orbital)
        phys = phys_by_key.get(key) or phys_by_key.get(match_key(el["jpl_name"]))
        physical_row: dict[str, Any] = {
            "pole_ra_dec": f"{el['pole_ra_deg']}/{el['pole_dec_deg']}" if el["pole_ra_deg"] is not None else None,
            "axial_tilt_deg": el["tilt_deg"],
        }
        if phys:
            physical_row.update({
                "gm_km3_s2": phys["gm_km3_s2"], "radius_km": phys["radius_km"], "density_g_cm3": phys["density_g_cm3"],
                "mass_kg": phys["gm_km3_s2"] / G_KM3_KG_S2 if phys["gm_km3_s2"] else None,
            })
        upsert_row_fill(conn, "physical_properties", obj_id, physical_row)
        upsert_row(conn, "visual_properties", obj_id, {})
        add_source(conn, object_id=obj_id, table_name="orbital_elements", source_name=SOURCE_NAME, source_url=ELEM_URL)
        if phys:
            add_source(conn, object_id=obj_id, table_name="physical_properties", source_name=SOURCE_NAME, source_url=PHYS_URL)
    conn.commit()
    return {"moons_created": created, "moons_updated": updated}


def fetch_pages(timeout: int = 120) -> tuple[str, str]:
    return fetch_text(ELEM_URL, timeout=timeout), fetch_text(PHYS_URL, timeout=timeout)
