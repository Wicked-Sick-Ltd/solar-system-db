"""JPL Small-Body Database bulk ingest: every field the query API publishes.

Pure mapping (`map_row`) is separated from I/O (`iter_bulk`, `write_mapped`)
so the fixture-driven tests exercise exactly the code the nightly build runs.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (SBDB_QUERY_URL, add_classification, add_source, asteroid_id,  # noqa: E402
                    comet_id, fetch_json, slugify, upsert_object, upsert_row)

# Every field the SBDB query API accepts for small bodies (verified 2026-09-15).
FIELDS: list[str] = [
    "spkid", "full_name", "name", "pdes", "prefix", "kind", "neo", "pha", "class",
    "orbit_id", "epoch", "equinox",
    "e", "a", "q", "i", "om", "w", "ma", "tp", "per", "per_y", "n", "ad",
    "moid", "moid_jup", "t_jup",
    "condition_code", "data_arc", "first_obs", "last_obs",
    "n_obs_used", "n_del_obs_used", "n_dop_obs_used", "rms", "soln_date", "producer", "source",
    "two_body", "pe_used", "sb_used",
    "sigma_e", "sigma_a", "sigma_q", "sigma_i", "sigma_om", "sigma_w", "sigma_ma",
    "sigma_tp", "sigma_per", "sigma_n", "sigma_ad",
    "H", "G", "GM", "diameter", "diameter_sigma", "extent", "density", "rot_per", "pole",
    "albedo", "BV", "UB", "IR", "spec_B", "spec_T",
    "M1", "K1", "M2", "K2", "A1", "A2", "A3", "DT",
]

ORBIT_CLASS_NAMES: dict[str, str] = {
    # asteroids
    "IEO": "Atira (interior-Earth object)", "ATE": "Aten", "APO": "Apollo", "AMO": "Amor",
    "MCA": "Mars-crossing asteroid", "IMB": "Inner main-belt asteroid", "MBA": "Main-belt asteroid",
    "OMB": "Outer main-belt asteroid", "TJN": "Jupiter trojan", "CEN": "Centaur",
    "TNO": "Trans-Neptunian object", "PAA": "Parabolic asteroid", "HYA": "Hyperbolic asteroid",
    "AST": "Asteroid (other)",
    # comets
    "COM": "Comet", "CTc": "Chiron-type comet", "ETc": "Encke-type comet", "HTC": "Halley-type comet",
    "HYP": "Hyperbolic comet", "JFc": "Jupiter-family comet (P < 20 y)",
    "JFC": "Jupiter-family comet (2 < T_J < 3)", "PAR": "Parabolic comet",
}
ORBIT_CLASS_CODES = frozenset(ORBIT_CLASS_NAMES)

# Site-facing classification labels derived from SBDB flags / classes
_CLASS_LABELS = {
    "MBA": "MBA", "IMB": "MBA", "OMB": "MBA", "TJN": "Trojan", "CEN": "Centaur", "TNO": "TNO",
    "MCA": "Mars-crosser", "ATE": "Aten", "APO": "Apollo", "AMO": "Amor", "IEO": "Atira",
}


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x: Any) -> int | None:
    f = _f(x)
    return int(f) if f is not None else None


def _s(x: Any) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    return s or None


def _yes(x: Any) -> bool:
    return x in (True, "Y", "y", 1, "1")


def sbdb_number(spkid: Any) -> int | None:
    """Permanent number from an SBDB spkid (2NNNNNNN numbered; 3… unnumbered)."""
    s = _i(spkid)
    if s is None:
        return None
    if 20000000 <= s < 30000000:
        return s - 20000000
    if 2000000 <= s < 3000000:  # legacy 7-digit form
        return s - 2000000
    return None


def map_row(fields: list[str], row: list[Any], *, curated: dict[int, str] | None = None) -> dict[str, Any]:
    """Map one SBDB query row onto our tables.

    `curated` maps permanent number → curated object id (e.g. 134340 → 'dwarf-pluto');
    such rows keep the curated id and do not overwrite curated physical/visual values.
    """
    r = dict(zip(fields, row))
    kind = (_s(r.get("kind")) or "").lower()
    is_comet = kind.startswith("c")
    name = _s(r.get("name"))
    full_name = _s(r.get("full_name"))
    pdes = _s(r.get("pdes"))
    prefix = _s(r.get("prefix"))
    number = sbdb_number(r.get("spkid"))
    cls = _s(r.get("class")) or ""

    curated_id = (curated or {}).get(number) if number is not None else None
    if curated_id:
        obj_id = curated_id
    elif is_comet:
        obj_id = comet_id(full_name or pdes or str(r.get("spkid")))
    else:
        obj_id = asteroid_id(r.get("spkid"), name)

    if is_comet:
        obj_type = "comet"
    elif cls == "TNO":
        obj_type = "tno"
    elif cls == "CEN":
        obj_type = "centaur"
    else:
        obj_type = "asteroid"

    # --- orbital -------------------------------------------------------
    a, e, q = _f(r.get("a")), _f(r.get("e")), _f(r.get("q"))
    ad = _f(r.get("ad"))
    if a is None and q is not None and e is not None and e < 1:
        a = q / (1 - e)
    if q is None and a is not None and e is not None:
        q = a * (1 - e)
    if ad is None and a is not None and e is not None and e < 1:
        ad = a * (1 + e)
    per = _f(r.get("per"))
    if per is None and _f(r.get("per_y")) is not None:
        per = _f(r.get("per_y")) * 365.25  # type: ignore[operator]
    tp = _f(r.get("tp"))
    ma = _f(r.get("ma"))
    epoch_jd = _f(r.get("epoch"))
    epoch = _s(r.get("epoch"))
    if is_comet and tp is not None and (ma is None or e is None or e >= 1):
        # Propagate comets from perihelion passage (M = 0 there) — robust for
        # near-parabolic orbits where the mean anomaly at epoch is meaningless.
        epoch_jd, epoch, ma = tp, f"JD {tp:.5f} (perihelion)", 0.0

    orbital = {
        "epoch": epoch, "epoch_jd": epoch_jd, "frame": "J2000", "centre": "Sun",
        "semi_major_axis_au": a, "eccentricity": e, "inclination_deg": _f(r.get("i")),
        "longitude_ascending_node_deg": _f(r.get("om")), "argument_periapsis_deg": _f(r.get("w")),
        "mean_anomaly_deg": ma, "orbital_period_days": per, "perihelion_au": q, "aphelion_au": ad,
        "mean_motion_deg_per_day": _f(r.get("n")),
        "perihelion_time_jd": tp, "moid_au": _f(r.get("moid")), "moid_jupiter_au": _f(r.get("moid_jup")),
        "tisserand_jupiter": _f(r.get("t_jup")), "condition_code": _i(r.get("condition_code")),
        "data_arc_days": _f(r.get("data_arc")), "first_obs": _s(r.get("first_obs")), "last_obs": _s(r.get("last_obs")),
        "n_obs_used": _i(r.get("n_obs_used")), "n_delay_obs_used": _i(r.get("n_del_obs_used")),
        "n_doppler_obs_used": _i(r.get("n_dop_obs_used")), "rms_arcsec": _f(r.get("rms")),
        "solution_date": _s(r.get("soln_date")), "orbit_id": _s(r.get("orbit_id")), "producer": _s(r.get("producer")),
        "orbit_source": _s(r.get("source")), "equinox": _s(r.get("equinox")), "two_body": _s(r.get("two_body")),
        "pe_used": _s(r.get("pe_used")), "sb_used": _s(r.get("sb_used")),
        "orbit_class_code": cls or None, "orbit_class_name": ORBIT_CLASS_NAMES.get(cls),
        **{f"sigma_{k}": _f(r.get(f"sigma_{k}")) for k in ("e", "a", "q", "i", "om", "w", "ma", "tp", "per", "n", "ad")},
    }

    # --- physical / visual ------------------------------------------------
    diameter = _f(r.get("diameter"))
    physical = {
        "radius_km": diameter / 2 if diameter else None, "rotation_period_hours": _f(r.get("rot_per")),
        "density_g_cm3": _f(r.get("density")), "gm_km3_s2": _f(r.get("GM")), "slope_g": _f(r.get("G")),
        "extent_km": _s(r.get("extent")), "pole_ra_dec": _s(r.get("pole")), "diameter_sigma_km": _f(r.get("diameter_sigma")),
    }
    visual = {
        "geometric_albedo": _f(r.get("albedo")), "absolute_magnitude_h": _f(r.get("H")),
        "colour_b_v": _f(r.get("BV")), "colour_u_b": _f(r.get("UB")), "colour_i_r": _f(r.get("IR")),
        "spectral_type": _s(r.get("spec_B")), "spectral_type_tholen": _s(r.get("spec_T")),
        "comet_m1": _f(r.get("M1")), "comet_k1": _f(r.get("K1")), "comet_m2": _f(r.get("M2")), "comet_k2": _f(r.get("K2")),
        "nongrav_a1": _f(r.get("A1")), "nongrav_a2": _f(r.get("A2")), "nongrav_a3": _f(r.get("A3")), "nongrav_dt": _f(r.get("DT")),
    }
    if is_comet and visual["absolute_magnitude_h"] is None:
        visual["absolute_magnitude_h"] = visual["comet_m1"]  # v1 convention: M1 stands in for H

    # --- classifications & designations -----------------------------------
    labels: list[str] = []
    if _yes(r.get("neo")):
        labels.append("NEO")
    if _yes(r.get("pha")):
        labels.append("PHA")
    if cls in _CLASS_LABELS:
        labels.append(_CLASS_LABELS[cls])
    if name:
        labels.append("Named")

    designations: list[tuple[str, str]] = []
    if number is not None:
        designations.append((str(number), "number"))
    if pdes and (number is None or pdes != str(number)):
        designations.append((pdes, "provisional"))
    if full_name:
        # "     1 Ceres (A801 AA)" → provisional in parentheses
        if "(" in full_name and full_name.endswith(")"):
            prov = full_name[full_name.rfind("(") + 1:-1].strip()
            if prov and prov != pdes and not prov.isdigit():
                designations.append((prov, "provisional"))
        designations.append((full_name.strip(), "alternate"))
    if name:
        designations.append((name, "name"))
    if not curated_id:
        legacy = asteroid_id(r.get("spkid"), name) if not is_comet else obj_id
        if legacy != obj_id:
            designations.append((legacy, "alternate"))

    display = name or (f"{prefix}/{pdes}" if is_comet and prefix and pdes else None) or pdes or full_name or f"SPKID {r.get('spkid')}"
    if is_comet and full_name and not name:
        display = full_name.strip()

    return {
        "id": obj_id, "is_curated": bool(curated_id),
        "object": {"id": obj_id, "name": display, "designation": (full_name or pdes), "object_type": obj_type},
        "orbital": orbital,
        "physical": None if curated_id else physical,
        "visual": None if curated_id else visual,
        "classifications": labels,
        "designations": designations,
    }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def load_fixture(path: str | Path) -> tuple[list[str], list[list[Any]]]:
    d = json.loads(Path(path).read_text())
    return d["fields"], d["data"]


def iter_bulk(kind: str, *, page: int = 50000, extra: dict[str, str] | None = None,
              timeout: int = 600) -> Iterator[tuple[list[str], list[list[Any]]]]:
    """Page through the whole SBDB catalogue for one kind ('a' or 'c')."""
    start = 0
    while True:
        params = {"fields": ",".join(FIELDS), "sb-kind": kind, "full-prec": "true",
                  "limit": str(page), "limit-from": str(start), **(extra or {})}
        data = fetch_json(SBDB_QUERY_URL, params=params, timeout=timeout)
        rows = data.get("data") or []
        if not rows:
            return
        yield data["fields"], rows
        if len(rows) < page:
            return
        start += page


def write_mapped(conn, mapped: Iterable[dict[str, Any]], *, source_name: str = "JPL SBDB",
                 source_url: str = "https://ssd-api.jpl.nasa.gov/sbdb_query.api",
                 commit_every: int = 50000) -> Counter:
    counts: Counter = Counter()
    for n, m in enumerate(mapped, 1):
        o = m["object"]
        if m["is_curated"]:
            conn.execute("UPDATE objects SET designation = COALESCE(designation, ?) WHERE id = ?",
                         (o["designation"], o["id"]))
        else:
            upsert_object(conn, id=o["id"], name=o["name"], designation=o["designation"],
                          object_type=o["object_type"], parent_id="sun")
        upsert_row(conn, "orbital_elements", m["id"], m["orbital"])
        if m["physical"] is not None:
            upsert_row(conn, "physical_properties", m["id"], m["physical"])
            upsert_row(conn, "visual_properties", m["id"], m["visual"])
        else:
            upsert_row(conn, "physical_properties", m["id"], {})
            upsert_row(conn, "visual_properties", m["id"], {})
        for label in m["classifications"]:
            add_classification(conn, m["id"], label)
        conn.executemany(
            "INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, ?, ?)",
            [(m["id"], d, k, source_name) for d, k in m["designations"]],
        )
        add_source(conn, object_id=m["id"], table_name="orbital_elements", source_name=source_name, source_url=source_url)
        counts[o["object_type"]] += 1
        if n % commit_every == 0:
            conn.commit()
    conn.commit()
    return counts


def map_all(fields: list[str], rows: Iterable[list[Any]], *, curated: dict[int, str] | None = None) -> Iterator[dict[str, Any]]:
    for row in rows:
        yield map_row(fields, row, curated=curated)
