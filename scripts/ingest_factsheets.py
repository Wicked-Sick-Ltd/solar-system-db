"""Load seed/factsheets.json (NASA planetary fact sheets, scraped by
scrape_factsheets.py and committed) into physical / visual / atmosphere rows.

Labels vary slightly between the table pages and the <pre> pages, so every
label is matched on a normalised key (lower-case alphanumerics only).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ROOT, add_source, upsert_row  # noqa: E402

SEED_PATH = ROOT / "seed" / "factsheets.json"
SOURCE_NAME = "NASA Planetary Fact Sheet"


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


# normalised label → (table, column, scale). First match wins; order matters for radii/gravity variants.
MAPPING: list[tuple[str, str, str, float]] = [
    ("mass1024kg", "physical_properties", "mass_kg", 1e24),
    ("mass1022kg", "physical_properties", "mass_kg", 1e22),          # Moon page
    ("volume1010km3", "physical_properties", "volume_km3", 1e10),
    ("equatorialradius1barlevelkm", "physical_properties", "equatorial_radius_km", 1),
    ("equatorialradiuskm", "physical_properties", "equatorial_radius_km", 1),
    ("polarradiuskm", "physical_properties", "polar_radius_km", 1),
    ("volumetricmeanradiuskm", "physical_properties", "radius_km", 1),
    ("ellipticityflattening", "physical_properties", "ellipticity", 1),
    ("ellipticity", "physical_properties", "ellipticity", 1),
    ("meandensitykgm3", "physical_properties", "density_g_cm3", 1e-3),
    ("surfacegravitymeanms2", "physical_properties", "surface_gravity_m_s2", 1),
    ("surfacegravityeqms2", "physical_properties", "surface_gravity_m_s2", 1),
    ("surfacegravityms2", "physical_properties", "surface_gravity_m_s2", 1),
    ("gravitymean1barms2", "physical_properties", "surface_gravity_m_s2", 1),
    ("escapevelocitykms", "physical_properties", "escape_velocity_km_s", 1),
    ("gmx106km3s2", "physical_properties", "gm_km3_s2", 1e6),
    ("solarirradiancewm2", "physical_properties", "solar_irradiance_w_m2", 1),
    ("blackbodytemperaturek", "physical_properties", "black_body_temperature_k", 1),
    ("momentofinertiaimr2", "physical_properties", "moment_of_inertia", 1),
    ("j2x106", "physical_properties", "j2", 1),
    ("tropicalorbitperioddays", "physical_properties", "tropical_period_days", 1),
    ("synodicperioddays", "physical_properties", "synodic_period_days", 1),
    ("meanorbitalvelocitykms", "physical_properties", "mean_orbital_velocity_km_s", 1),
    ("maxorbitalvelocitykms", "physical_properties", "max_orbital_velocity_km_s", 1),
    ("minorbitalvelocitykms", "physical_properties", "min_orbital_velocity_km_s", 1),
    ("siderealrotationperiodhrs", "physical_properties", "rotation_period_hours", 1),
    ("lengthofdayhrs", "physical_properties", "length_of_day_hours", 1),
    ("obliquitytoorbitdeg", "physical_properties", "axial_tilt_deg", 1),
    ("bondalbedo", "visual_properties", "bond_albedo", 1),
    ("geometricalbedo", "visual_properties", "geometric_albedo", 1),
    ("vbandmagnitudev10", "visual_properties", "magnitude_v10", 1),
]


def load_seed(path: str | Path = SEED_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def mapped_rows(values: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Fact-sheet label/value pairs → {table: {column: value}} (numeric values only)."""
    out: dict[str, dict[str, float]] = {}
    normalised = {norm(k): v for k, v in values.items()}
    for key, table, column, scale in MAPPING:
        v = normalised.get(key)
        if isinstance(v, (int, float)) and column not in out.get(table, {}):
            out.setdefault(table, {})[column] = float(v) * scale
    return out


def write_factsheets(conn, seed: dict[str, Any]) -> dict[str, int]:
    n_bodies = n_atm = 0
    index_magnetic = seed.get("_index", {}).get("magnetic_field", {})
    for obj_id, entry in seed.items():
        if obj_id.startswith("_"):
            continue
        if not conn.execute("SELECT 1 FROM objects WHERE id = ?", (obj_id,)).fetchone():
            continue
        rows = mapped_rows(entry.get("values", {}))
        mag = index_magnetic.get(obj_id)
        if mag:
            rows.setdefault("physical_properties", {})["magnetic_field"] = mag  # type: ignore[assignment]
        # The fact sheet *is* the curated source for these bodies, so it overwrites.
        for table, fields in rows.items():
            upsert_row(conn, table, obj_id, fields)
            add_source(conn, object_id=obj_id, table_name=table, source_name=SOURCE_NAME, source_url=entry.get("source_url"))
        atm = entry.get("atmosphere")
        if atm and (atm.get("surface_pressure_bar") is not None or atm.get("temperature_k") is not None or atm.get("composition")):
            conn.execute(
                """INSERT OR REPLACE INTO atmospheres
                   (object_id, surface_pressure_bar, pressure_note, temperature_k, temperature_note, density_kg_m3,
                    scale_height_km, mean_molecular_weight, wind_note, composition_json, source)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (obj_id, atm.get("surface_pressure_bar"), atm.get("pressure_note"), atm.get("temperature_k"),
                 atm.get("temperature_note"), atm.get("density_kg_m3"), atm.get("scale_height_km"),
                 atm.get("mean_molecular_weight"), atm.get("wind_note"), json.dumps(atm.get("composition") or []),
                 SOURCE_NAME),
            )
            add_source(conn, object_id=obj_id, table_name="atmospheres", source_name=SOURCE_NAME, source_url=entry.get("source_url"))
            n_atm += 1
        n_bodies += 1
    conn.commit()
    return {"factsheet_bodies": n_bodies, "atmospheres": n_atm}
