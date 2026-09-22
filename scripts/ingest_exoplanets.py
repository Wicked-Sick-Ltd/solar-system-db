"""NASA Exoplanet Archive PSCompPars snapshot (one row per confirmed planet).

Composite measurements may come from different papers or calculated values.
Preserve the selected raw columns, including references, errors and limits.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from urllib.parse import quote

from common import fetch_json, add_source
from solar_db.galactic import FRAME, coordinates

SOURCE = "NASA Exoplanet Archive / PSCompPars (doi:10.26133/NEA13)"
TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
MEASUREMENTS = ("pl_rade", "pl_bmasse", "pl_orbper", "pl_orbsmax", "pl_eqt", "st_teff", "st_mass", "st_rad")
COLUMNS = ["pl_name", "hostname", "discoverymethod", "disc_year", "disc_refname", "pl_controv_flag",
           "pl_bmassprov", "sy_snum", "sy_pnum", "cb_flag", "gaia_dr3_id", "ra", "dec",
           "ra_reflink", "dec_reflink", "sy_dist", "sy_disterr1", "sy_disterr2", "sy_distlim", "sy_dist_reflink"]
for field in MEASUREMENTS:
    COLUMNS.extend([field, field + "err1", field + "err2", field + "lim", field + "_reflink"])
QUERY = "select " + ",".join(COLUMNS) + " from pscomppars order by pl_name"


def fetch_exoplanets() -> list[dict]:
    result = fetch_json(TAP_URL, params={"query": QUERY, "format": "json"})
    if not isinstance(result, list) or not result:
        raise ValueError("NASA exoplanet snapshot is empty or malformed")
    return result


def stable_id(kind: str, name: str) -> str:
    return kind + "-" + hashlib.sha256(name.encode()).hexdigest()[:20]


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def host_values(row: dict) -> dict:
    ra, dec, distance = (number(row.get(k)) for k in ("ra", "dec", "sy_dist"))
    ra = ra if ra is not None and 0 <= ra < 360 else None
    dec = dec if dec is not None and -90 <= dec <= 90 else None
    # A distance limit is not a measured position.
    distance = distance if distance is not None and distance > 0 and not row.get("sy_distlim") else None
    return {"ra": ra, "dec": dec, "distance": distance}


def write_exoplanets(conn, rows: list[dict], *, retrieved_at: str | None = None) -> dict[str, int]:
    import astropy

    if not rows or any(not isinstance(r, dict) or not r.get("pl_name") or not r.get("hostname") for r in rows):
        raise ValueError("Every exoplanet must have a planet and host name; snapshot must not be empty")
    if len({r["pl_name"] for r in rows}) != len(rows):
        raise ValueError("Duplicate planet names in PSCompPars snapshot")
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    # Deterministically choose one whole astrometry tuple per host. Prefer a
    # mappable row, never stitch distances and coordinates from different rows.
    hosts = {}
    ordered = sorted(rows, key=lambda r: (any(v is None for v in host_values(r).values()), r["pl_name"]))
    for row in ordered:
        hosts.setdefault(row["hostname"], row)
    metadata = json.dumps({**FRAME, "astropy_version": astropy.__version__})
    mapped = 0
    with conn:
        conn.execute("DELETE FROM exoplanets")
        conn.execute("DELETE FROM exoplanet_hosts")
        for name, row in hosts.items():
            values = host_values(row)
            position = coordinates(**values)
            mapped += bool(position)
            conn.execute("""INSERT INTO exoplanet_hosts (
                id,name,ra_deg,dec_deg,distance_pc,distance_error_plus_pc,distance_error_minus_pc,
                x_pc,y_pc,z_pc,galactocentric_x_pc,galactocentric_y_pc,galactocentric_z_pc,
                coordinate_source_planet,coordinate_metadata,source,retrieved_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (stable_id("host", name), name, values["ra"], values["dec"], values["distance"],
                 number(row.get("sy_disterr1")), number(row.get("sy_disterr2")),
                 *(position.get(k) for k in ("x_pc", "y_pc", "z_pc", "galactocentric_x_pc", "galactocentric_y_pc", "galactocentric_z_pc")),
                 row["pl_name"], metadata, SOURCE, retrieved_at))
        for row in rows:
            name = row["pl_name"]
            conn.execute("""INSERT INTO exoplanets (
                id,name,host_id,discovery_method,discovery_year,radius_earth,mass_earth,mass_provenance,
                period_days,semi_major_axis_au,equilibrium_temperature_k,controversial,
                source,source_url,retrieved_at,source_data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (stable_id("exo", name), name, stable_id("host", row["hostname"]), row.get("discoverymethod"),
                 row.get("disc_year"), number(row.get("pl_rade")), number(row.get("pl_bmasse")), row.get("pl_bmassprov"),
                 number(row.get("pl_orbper")), number(row.get("pl_orbsmax")), number(row.get("pl_eqt")),
                 int(bool(row.get("pl_controv_flag"))), SOURCE,
                 "https://exoplanetarchive.ipac.caltech.edu/overview/" + quote(name, safe=""),
                 retrieved_at, json.dumps(row, allow_nan=False)))
        conn.execute("DELETE FROM sources WHERE table_name='exoplanets'")
        add_source(conn, object_id=None, table_name="exoplanets", source_name=SOURCE,
                   source_url="https://exoplanetarchive.ipac.caltech.edu/docs/PSCompPars.html")
    return {"exoplanets": len(rows), "hosts": len(hosts), "mapped_hosts": mapped,
            "unmapped_hosts": len(hosts) - mapped}
