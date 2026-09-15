"""Merge the crawler's raw SBDB lookup payloads into the catalogue (build stage 7)."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import add_source, upsert_row_fill  # noqa: E402

SOURCE_NAME = "JPL SBDB (lookup)"
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_PHYS_COLUMNS = {  # SBDB phys_par name → (table, column)
    "H": ("visual_properties", "absolute_magnitude_h"), "G": ("physical_properties", "slope_g"),
    "diameter": ("physical_properties", "radius_km"), "GM": ("physical_properties", "gm_km3_s2"),
    "density": ("physical_properties", "density_g_cm3"), "rot_per": ("physical_properties", "rotation_period_hours"),
    "albedo": ("visual_properties", "geometric_albedo"), "BV": ("visual_properties", "colour_b_v"),
    "UB": ("visual_properties", "colour_u_b"), "spec_B": ("visual_properties", "spectral_type"),
    "spec_T": ("visual_properties", "spectral_type_tholen"), "M1": ("visual_properties", "comet_m1"),
    "K1": ("visual_properties", "comet_k1"), "M2": ("visual_properties", "comet_m2"), "K2": ("visual_properties", "comet_k2"),
}


def _f(x: Any) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date(d: str | None) -> str | None:
    """'2004-Jun-19' → '2004-06-19'; already-ISO passes through."""
    if not d:
        return None
    m = re.match(r"(\d{4})-([A-Za-z]{3})-(\d{1,2})", d)
    return f"{m.group(1)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(3)):02d}" if m else d


def _cd_to_iso(cd: str) -> str:
    d, t = cd.split()
    y, mon, day = d.split("-")
    return f"{y}-{_MONTHS[mon]:02d}-{int(day):02d}T{t}:00Z"


def apply_payload(conn: sqlite3.Connection, object_id: str, payload: dict[str, Any]) -> dict[str, int]:
    n = {"close_approaches": 0, "radar": 0, "designations": 0, "phys": 0}
    obj = payload.get("object") or {}

    disc = payload.get("discovery") or {}
    if disc:
        conn.execute(
            """INSERT INTO discoveries (object_id, discovered_on, discoverer, site, location, citation, reference, source)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(object_id) DO UPDATE SET
                 discovered_on = COALESCE(discoveries.discovered_on, excluded.discovered_on),
                 discoverer    = COALESCE(discoveries.discoverer, excluded.discoverer),
                 site          = COALESCE(discoveries.site, excluded.site),
                 location      = COALESCE(excluded.location, discoveries.location),
                 citation      = COALESCE(excluded.citation, discoveries.citation),
                 reference     = COALESCE(discoveries.reference, excluded.reference)""",
            (object_id, _date(disc.get("date")), disc.get("who"), disc.get("site") or disc.get("location"),
             disc.get("location"), disc.get("citation"), disc.get("ref"), SOURCE_NAME),
        )
        if disc.get("name"):
            conn.execute("INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, 'name', ?)",
                         (object_id, disc["name"], SOURCE_NAME))

    for alt in obj.get("des_alt") or []:
        for kind, des in alt.items():
            if des:
                conn.execute("INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, ?, ?)",
                             (object_id, des, "provisional" if kind == "pri" else "alternate", SOURCE_NAME))
                n["designations"] += 1

    rows = []
    for ca in payload.get("ca_data") or []:
        rows.append((object_id, ca.get("body") or "Earth", _cd_to_iso(ca["cd"]), _f(ca.get("dist")),
                     _f(ca.get("dist_min")), _f(ca.get("dist_max")), _f(ca.get("v_rel")), _f(ca.get("v_inf")),
                     ca.get("sigma_t"), ca.get("orbit_ref"), SOURCE_NAME))
    if rows:
        # Same encounter as a CAD row (stage 5) dedupes on (object, body, minute);
        # CAD's precise JD and source are kept, the lookup fills what is missing.
        conn.executemany(
            """INSERT INTO close_approaches
               (object_id, body, cd_iso, cd_jd, dist_au, dist_min_au, dist_max_au, v_rel_km_s, v_inf_km_s, t_sigma, orbit_ref, source)
               VALUES (?, ?, ?, julianday(?), ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(object_id, body, cd_iso) DO UPDATE SET
                 dist_au = COALESCE(close_approaches.dist_au, excluded.dist_au),
                 dist_min_au = COALESCE(close_approaches.dist_min_au, excluded.dist_min_au),
                 dist_max_au = COALESCE(close_approaches.dist_max_au, excluded.dist_max_au),
                 v_rel_km_s = COALESCE(close_approaches.v_rel_km_s, excluded.v_rel_km_s),
                 v_inf_km_s = COALESCE(close_approaches.v_inf_km_s, excluded.v_inf_km_s),
                 t_sigma = COALESCE(close_approaches.t_sigma, excluded.t_sigma)""",
            [(r[0], r[1], r[2], r[2], *r[3:]) for r in rows],
        )
        n["close_approaches"] = len(rows)

    conn.execute("DELETE FROM radar_observations WHERE object_id = ? AND source = ?", (object_id, SOURCE_NAME))
    for ro in payload.get("radar_obs") or []:
        conn.execute(
            "INSERT INTO radar_observations (object_id, epoch, obs_type, reference, source) VALUES (?, ?, ?, ?, ?)",
            (object_id, ro.get("epoch"), f"{ro.get('units')} @ {ro.get('freq')} MHz ({ro.get('bp')})",
             f"value {ro.get('value')} ± {ro.get('sigma')}", SOURCE_NAME),
        )
        n["radar"] += 1

    vi = payload.get("vi_data")
    if vi is not None:
        conn.execute("INSERT OR REPLACE INTO impact_monitoring (object_id, flagged, source) VALUES (?, ?, ?)",
                     (object_id, 1 if vi else 0, SOURCE_NAME))

    phys_fill: dict[str, dict[str, Any]] = {"physical_properties": {}, "visual_properties": {}}
    for par in payload.get("phys_par") or []:
        target = _PHYS_COLUMNS.get(par.get("name"))
        if not target:
            continue
        table, column = target
        value: Any = par.get("value")
        if column == "radius_km":
            value = _f(value) / 2 if _f(value) else None
        elif column not in ("spectral_type", "spectral_type_tholen"):
            value = _f(value)
        if value is not None:
            phys_fill[table][column] = value
            if par.get("ref"):
                add_source(conn, object_id=object_id, table_name=table, field_name=column,
                           source_name=SOURCE_NAME, source_url=par["ref"])
            n["phys"] += 1
    for table, fields in phys_fill.items():
        if fields:
            upsert_row_fill(conn, table, object_id, fields)

    model = {p.get("name"): _f(p.get("value")) for p in ((payload.get("orbit") or {}).get("model_pars") or [])}
    ng = {f"nongrav_{k.lower()}": v for k, v in model.items() if k in ("A1", "A2", "A3", "DT") and v is not None}
    if ng:
        upsert_row_fill(conn, "visual_properties", object_id, ng)

    for sat in payload.get("sat") or []:
        if sat.get("iau_name"):
            conn.execute("INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, 'alternate', ?)",
                         (object_id, f"satellite: {sat['iau_name']}", SOURCE_NAME))
    return n


def merge_store(conn: sqlite3.Connection, store_path: str | Path) -> dict[str, int]:
    """Apply every successful lookup in the crawler store to the catalogue.

    Plain connection with a busy timeout (the crawler keeps writing in WAL mode);
    rows are read in rowid pages so no long-lived cursor pins the store."""
    totals = {"applied": 0, "skipped": 0, "close_approaches": 0, "radar": 0}
    store = sqlite3.connect(store_path, timeout=60)
    store.row_factory = sqlite3.Row
    store.execute("PRAGMA busy_timeout = 60000")
    known = {r[0] for r in conn.execute("SELECT id FROM objects")}
    last_rowid, i = 0, 0
    while True:
        page = store.execute(
            "SELECT rowid, object_id, fetched_at, status, payload FROM lookups "
            "WHERE rowid > ? AND status = 'ok' ORDER BY rowid LIMIT 500", (last_rowid,)).fetchall()
        if not page:
            break
        last_rowid = page[-1]["rowid"]
        for row in page:
            i += 1
            if row["object_id"] not in known or not row["payload"]:
                totals["skipped"] += 1
                continue
            n = apply_payload(conn, row["object_id"], json.loads(row["payload"]))
            conn.execute("INSERT OR REPLACE INTO enrichment_state (object_id, tier, lookup_at, lookup_status) "
                         "VALUES (?, COALESCE((SELECT tier FROM enrichment_state WHERE object_id = ?), 2), ?, 'ok')",
                         (row["object_id"], row["object_id"], row["fetched_at"]))
            totals["applied"] += 1
            totals["close_approaches"] += n["close_approaches"]
            totals["radar"] += n["radar"]
            if i % 5000 == 0:
                conn.commit()
    conn.commit()
    store.close()
    return totals
