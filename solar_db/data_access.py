"""Read-only data-access layer over solar_system.sqlite.

All query methods return plain dicts (or lists thereof). Both the MCP server
and the REST API call these — keep the surface narrow and stable.

DB is opened with the SQLite `mode=ro` URI so no write is possible even by
mistake.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from .positions import solar_longitude_deg

DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "solar_system.sqlite"
)

# The object_type values understood by the catalogue. Kept in sync with the
# CHECK constraint in schema/schema.sql.
OBJECT_TYPES = (
    "star", "planet", "moon", "dwarf_planet", "dwarf_planet_candidate",
    "asteroid", "comet", "tno", "centaur", "trojan", "hilda", "neo", "pha",
)

OBJECT_FIELDS = (
    "id", "name", "designation", "object_type", "parent_id",
    "discoverer", "discovery_date", "wikipedia_url", "notes",
)

ORBITAL_FIELDS = (
    "epoch", "epoch_jd", "frame", "centre",
    "semi_major_axis_au", "eccentricity", "inclination_deg",
    "longitude_ascending_node_deg", "argument_periapsis_deg",
    "mean_anomaly_deg", "orbital_period_days",
    "perihelion_au", "aphelion_au", "mean_motion_deg_per_day",
)


class SolarDB:
    """Thin SQLite wrapper, read-only."""

    def __init__(self, db_path: str | os.PathLike | None = None) -> None:
        self.db_path = Path(db_path or os.environ.get("SOLAR_DB_PATH",
                                                       str(DEFAULT_DB_PATH)))
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Solar System DB not found at {self.db_path}. "
                f"Download it with scripts/pull_latest.sh (MANIFEST_URL=…), build it with "
                f"scripts/build_full.py --fresh --online, or set SOLAR_DB_PATH."
            )

    @staticmethod
    def _lim(limit: int | None, cap: int) -> int:
        """Clamp a caller-supplied limit to [1, cap]; SQLite treats LIMIT -1 as unbounded."""
        try:
            return max(1, min(int(limit if limit is not None else cap), cap))
        except (TypeError, ValueError):
            return cap

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self.db_path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ----------------------------------------------------------------------
    # Catalog
    # ----------------------------------------------------------------------
    def find_objects(
        self,
        *,
        object_type: str | None = None,
        parent: str | None = None,
        min_radius_km: float | None = None,
        max_radius_km: float | None = None,
        max_eccentricity: float | None = None,
        min_semi_major_axis_au: float | None = None,
        max_semi_major_axis_au: float | None = None,
        neo: bool | None = None,
        pha: bool | None = None,
        named_only: bool | None = None,
        orbit_class: str | None = None,
        max_moid_au: float | None = None,
        min_diameter_km: float | None = None,
        max_condition_code: int | None = None,
        discovered_after: str | None = None,
        after: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Flexible object filter — see method args.

        `after` enables keyset pagination over the full catalogue (pass the last
        id of the previous page); results are then ordered by id, and `offset`
        is ignored."""
        clauses: list[str] = []
        params: list[Any] = []

        if orbit_class:
            clauses.append("oe.orbit_class_code = ?")
            params.append(orbit_class)
        if max_moid_au is not None:
            clauses.append("oe.moid_au <= ?")
            params.append(max_moid_au)
        if min_diameter_km is not None:
            clauses.append("p.radius_km >= ?")
            params.append(min_diameter_km / 2)
        if max_condition_code is not None:
            clauses.append("oe.condition_code <= ?")
            params.append(max_condition_code)
        if discovered_after:
            clauses.append("o.discovery_date >= ?")
            params.append(discovered_after)
        if after is not None:                      # "" = start of the keyset walk
            clauses.append("o.id > ?")
            params.append(after)

        if object_type:
            if object_type not in OBJECT_TYPES:
                raise ValueError(f"Unknown object_type {object_type!r}; "
                                 f"valid: {OBJECT_TYPES}")
            clauses.append("o.object_type = ?")
            params.append(object_type)

        if parent:
            parent_id = self._resolve_id(parent)
            if not parent_id:
                return []
            clauses.append("o.parent_id = ?")
            params.append(parent_id)

        if min_radius_km is not None:
            clauses.append("p.radius_km >= ?")
            params.append(min_radius_km)
        if max_radius_km is not None:
            clauses.append("p.radius_km <= ?")
            params.append(max_radius_km)
        if max_eccentricity is not None:
            clauses.append("oe.eccentricity <= ?")
            params.append(max_eccentricity)
        if min_semi_major_axis_au is not None:
            clauses.append("oe.semi_major_axis_au >= ?")
            params.append(min_semi_major_axis_au)
        if max_semi_major_axis_au is not None:
            clauses.append("oe.semi_major_axis_au <= ?")
            params.append(max_semi_major_axis_au)

        if neo:
            clauses.append("EXISTS (SELECT 1 FROM classifications c "
                           "WHERE c.object_id=o.id AND c.label='NEO')")
        if pha:
            clauses.append("EXISTS (SELECT 1 FROM classifications c "
                           "WHERE c.object_id=o.id AND c.label='PHA')")
        if named_only:
            clauses.append("o.name IS NOT NULL AND o.name <> ''")

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "o.id" if after is not None else "oe.semi_major_axis_au IS NULL, oe.semi_major_axis_au, o.id"
        with self._conn() as probe:
            v2 = self._has_table(probe, "designations")
        extra = "oe.orbit_class_code, oe.moid_au, oe.condition_code," if v2 else ""
        sql = f"""
            SELECT o.id, o.name, o.designation, o.object_type, o.parent_id,
                   o.discoverer, o.discovery_date, o.wikipedia_url,
                   p.radius_km, p.mass_kg,
                   oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
                   oe.orbital_period_days, oe.perihelion_au, oe.aphelion_au,
                   {extra}
                   v.geometric_albedo, v.absolute_magnitude_h
            FROM objects o
            LEFT JOIN physical_properties p  ON p.object_id  = o.id
            LEFT JOIN orbital_elements    oe ON oe.object_id = o.id
            LEFT JOIN visual_properties   v  ON v.object_id  = o.id
            {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        params.extend([self._lim(limit, 1000), max(0, int(offset or 0)) if after is None else 0])
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def get_object(self, name_or_designation: str) -> dict[str, Any] | None:
        """Return one full record (object + orbital + physical + visual +
        classifications + sources) by name, id, or designation."""
        obj_id = self._resolve_id(name_or_designation)
        if not obj_id:
            return None
        with self._conn() as conn:
            obj = conn.execute(
                "SELECT * FROM objects WHERE id = ?", (obj_id,)
            ).fetchone()
            if not obj:
                return None
            out: dict[str, Any] = dict(obj)
            for tbl, key in [("orbital_elements", "orbital"),
                              ("physical_properties", "physical"),
                              ("visual_properties", "visual")]:
                row = conn.execute(
                    f"SELECT * FROM {tbl} WHERE object_id = ?", (obj_id,)
                ).fetchone()
                out[key] = dict(row) if row else None
            out["classifications"] = [
                r["label"] for r in conn.execute(
                    "SELECT label FROM classifications WHERE object_id = ?",
                    (obj_id,),
                )
            ]
            # v2 detail blocks — each guarded so a v1 file still serves.
            if self._has_table(conn, "designations"):
                out["designations"] = [
                    dict(r) for r in conn.execute(
                        "SELECT designation, kind, source FROM designations WHERE object_id = ? "
                        "ORDER BY CASE kind WHEN 'number' THEN 0 WHEN 'name' THEN 1 WHEN 'provisional' THEN 2 ELSE 3 END",
                        (obj_id,))
                ]
            if self._has_table(conn, "discoveries"):
                row = conn.execute("SELECT * FROM discoveries WHERE object_id = ?", (obj_id,)).fetchone()
                out["discovery"] = dict(row) if row else None
            if self._has_table(conn, "close_approaches"):
                out["close_approach_count"] = conn.execute(
                    "SELECT COUNT(*) FROM close_approaches WHERE object_id = ?", (obj_id,)).fetchone()[0]
                out["close_approaches"] = [
                    dict(r) for r in conn.execute(
                        "SELECT body, cd_jd, cd_iso, dist_au, dist_min_au, dist_max_au, v_rel_km_s, v_inf_km_s, t_sigma "
                        "FROM close_approaches WHERE object_id = ? AND cd_jd >= julianday('now') - 30 "
                        "ORDER BY cd_jd LIMIT 10", (obj_id,))
                ]
            if self._has_table(conn, "atmospheres"):
                row = conn.execute("SELECT * FROM atmospheres WHERE object_id = ?", (obj_id,)).fetchone()
                atm = dict(row) if row else None
                if atm and atm.get("composition_json"):
                    try:
                        atm["composition"] = json.loads(atm.pop("composition_json"))
                    except ValueError:
                        atm["composition"] = None
                out["atmosphere"] = atm
            if self._has_table(conn, "radar_observations"):
                out["radar_observations"] = [
                    dict(r) for r in conn.execute(
                        "SELECT epoch, obs_type, reference FROM radar_observations WHERE object_id = ? ORDER BY epoch",
                        (obj_id,))
                ]
            if self._has_table(conn, "impact_monitoring"):
                row = conn.execute("SELECT flagged, source, retrieved_at FROM impact_monitoring WHERE object_id = ?",
                                   (obj_id,)).fetchone()
                out["impact_monitoring"] = dict(row) if row else None
            if self._has_table(conn, "meteor_showers"):
                showers: list[dict] = []
                seen_iau_no: set[int] = set()
                for r in conn.execute(
                    "SELECT iau_no, code, name, solar_longitude_deg, vg_km_s "
                    "FROM meteor_showers WHERE parent_object_id = ? ORDER BY iau_no, ad_no",
                    (obj_id,)):
                    if r["iau_no"] in seen_iau_no:
                        continue
                    seen_iau_no.add(r["iau_no"])
                    showers.append(dict(r))
                out["meteor_showers"] = showers
            out["sources"] = [
                dict(r) for r in conn.execute(
                    "SELECT table_name, source_name, source_url, retrieved_at "
                    "FROM sources WHERE object_id = ? "
                    "ORDER BY retrieved_at DESC", (obj_id,),
                )
            ]
            return out

    def _has_table(self, conn: sqlite3.Connection, name: str) -> bool:
        cache = getattr(self, "_table_cache", None)
        if cache is None:
            cache = self._table_cache = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
            }
        return name in cache

    def _resolve_id(self, name_or_designation: str) -> str | None:
        """Look up an object id from id, name, designation, or any alternate designation."""
        with self._conn() as conn:
            if self._has_table(conn, "designations"):
                exact = [
                    "SELECT id FROM objects WHERE id = ? LIMIT 1",
                    "SELECT id FROM objects WHERE name = ? COLLATE NOCASE LIMIT 1",
                    "SELECT id FROM objects WHERE designation = ? COLLATE NOCASE LIMIT 1",
                    "SELECT object_id AS id FROM designations WHERE designation = ? COLLATE NOCASE LIMIT 1",
                ]
                for sql in exact:
                    row = conn.execute(sql, (name_or_designation,)).fetchone()
                    if row:
                        return row["id"]
            for sql in (
                "SELECT id FROM objects WHERE id = ? LIMIT 1",
                "SELECT id FROM objects WHERE name = ? COLLATE NOCASE LIMIT 1",
                "SELECT id FROM objects WHERE designation = ? COLLATE NOCASE LIMIT 1",
                "SELECT id FROM objects WHERE designation LIKE ? COLLATE NOCASE LIMIT 1",
                "SELECT id FROM objects WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            ):
                pattern = name_or_designation
                if "LIKE" in sql:
                    pattern = f"%{name_or_designation}%"
                row = conn.execute(sql, (pattern,)).fetchone()
                if row:
                    return row["id"]
        return None

    # ----------------------------------------------------------------------
    # Convenience views
    # ----------------------------------------------------------------------
    def list_moons(self, planet_name: str) -> list[dict[str, Any]]:
        planet_id = self._resolve_id(planet_name)
        if not planet_id:
            return []
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT o.id, o.name, o.designation, o.discoverer, o.discovery_date,
                       p.radius_km, p.mass_kg, p.density_g_cm3, v.geometric_albedo,
                       oe.semi_major_axis_au, oe.orbital_period_days,
                       oe.eccentricity, oe.inclination_deg
                FROM objects o
                LEFT JOIN physical_properties p ON p.object_id = o.id
                LEFT JOIN visual_properties v ON v.object_id = o.id
                LEFT JOIN orbital_elements oe   ON oe.object_id = o.id
                WHERE o.object_type='moon' AND o.parent_id = ?
                ORDER BY oe.semi_major_axis_au IS NULL,
                         oe.semi_major_axis_au,
                         o.name
                """,
                (planet_id,),
            )]

    def list_dwarf_planets(self, include_candidates: bool = False) -> list[dict]:
        types = ("dwarf_planet", "dwarf_planet_candidate") if include_candidates \
            else ("dwarf_planet",)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                f"""
                SELECT o.id, o.name, o.designation, o.object_type,
                       p.radius_km, p.mass_kg,
                       oe.semi_major_axis_au, oe.eccentricity,
                       oe.orbital_period_days
                FROM objects o
                LEFT JOIN physical_properties p ON p.object_id = o.id
                LEFT JOIN orbital_elements oe ON oe.object_id = o.id
                WHERE o.object_type IN ({','.join('?' * len(types))})
                ORDER BY oe.semi_major_axis_au
                """, types,
            )]

    def list_neos(
        self,
        *,
        min_diameter_km: float | None = None,
        max_diameter_km: float | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if min_diameter_km is not None:
            clauses.append("p.radius_km >= ?")
            params.append(min_diameter_km / 2.0)
        if max_diameter_km is not None:
            clauses.append("p.radius_km <= ?")
            params.append(max_diameter_km / 2.0)
        where_extra = (" AND " + " AND ".join(clauses)) if clauses else ""
        params.append(self._lim(limit, 1000))
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                f"""
                SELECT o.id, o.name, o.designation,
                       oe.semi_major_axis_au, oe.perihelion_au,
                       oe.eccentricity, oe.inclination_deg,
                       p.radius_km,
                       v.absolute_magnitude_h,
                       EXISTS (SELECT 1 FROM classifications c
                               WHERE c.object_id=o.id AND c.label='PHA') AS is_pha
                FROM objects o
                JOIN classifications cn ON cn.object_id = o.id AND cn.label='NEO'
                LEFT JOIN orbital_elements oe ON oe.object_id = o.id
                LEFT JOIN physical_properties p ON p.object_id = o.id
                LEFT JOIN visual_properties v ON v.object_id = o.id
                WHERE 1=1 {where_extra}
                ORDER BY v.absolute_magnitude_h
                LIMIT ?
                """, params,
            )]

    def list_periodic_comets(self, limit: int = 2000) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT o.id, o.name, o.designation, o.discoverer, o.discovery_date,
                       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
                       oe.orbital_period_days,
                       oe.perihelion_au, oe.aphelion_au
                FROM objects o
                LEFT JOIN orbital_elements oe ON oe.object_id = o.id
                WHERE o.object_type='comet'
                  AND oe.orbital_period_days IS NOT NULL
                  AND oe.orbital_period_days < 200 * 365.25
                ORDER BY oe.orbital_period_days
                LIMIT ?
                """, (limit,),
            )]

    def list_tnos(self, limit: int = 500) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT o.id, o.name, o.designation, o.object_type,
                       oe.semi_major_axis_au, oe.eccentricity, oe.inclination_deg,
                       oe.orbital_period_days,
                       v.absolute_magnitude_h
                FROM objects o
                LEFT JOIN orbital_elements oe ON oe.object_id = o.id
                LEFT JOIN visual_properties v ON v.object_id = o.id
                WHERE o.object_type IN ('tno','centaur')
                ORDER BY oe.semi_major_axis_au
                LIMIT ?
                """, (limit,),
            )]

    def get_rings(self, planet_name: str) -> list[dict]:
        parent_id = self._resolve_id(planet_name)
        if not parent_id:
            return []
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT id, name, inner_radius_km, outer_radius_km,
                       width_km, thickness_km, notes
                FROM rings
                WHERE parent_id = ?
                ORDER BY inner_radius_km
                """, (parent_id,),
            )]

    @staticmethod
    def _fts_query(query: str) -> str:
        """User text → FTS5 prefix query: '2024 yr4' → '"2024"* "yr4"*'."""
        tokens = [t for t in re.split(r"[^0-9A-Za-z/'\-]+", query) if t]
        return " ".join(f'"{t}"*' for t in tokens) if tokens else '""'

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Text search across names and every designation. FTS5 on v2 files
        (instant at 1.4 M rows), LIKE fallback on v1."""
        like = f"%{query}%"
        with self._conn() as conn:
            if self._has_table(conn, "objects_fts"):
                rows = conn.execute(
                    """
                    SELECT o.id, o.name, o.designation, o.object_type, o.parent_id, o.discoverer,
                           bm25(objects_fts) AS score
                    FROM objects_fts JOIN objects o ON o.id = objects_fts.id
                    WHERE objects_fts MATCH ?
                    ORDER BY (CASE WHEN o.name = ? COLLATE NOCASE OR o.designation = ? COLLATE NOCASE THEN 0 ELSE 1 END),
                             (CASE WHEN o.object_type='planet' THEN 0 WHEN o.object_type='dwarf_planet' THEN 1
                                   WHEN o.object_type='moon' THEN 2 WHEN o.object_type='comet' THEN 3 ELSE 4 END),
                             score, length(o.name)
                    LIMIT ?
                    """, (self._fts_query(query), query, query, self._lim(limit, 100)),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            return [dict(r) for r in conn.execute(
                """
                SELECT id, name, designation, object_type, parent_id,
                       discoverer
                FROM objects
                WHERE name LIKE ? COLLATE NOCASE
                   OR designation LIKE ? COLLATE NOCASE
                   OR discoverer LIKE ? COLLATE NOCASE
                ORDER BY (CASE WHEN object_type='planet' THEN 0
                               WHEN object_type='dwarf_planet' THEN 1
                               WHEN object_type='moon' THEN 2
                               WHEN object_type='comet' THEN 3
                               ELSE 4 END),
                         length(name)
                LIMIT ?
                """, (like, like, like, self._lim(limit, 100)),
            )]

    # ----------------------------------------------------------------------
    # v2 detail (close approaches, discovery, designations, atmosphere, download)
    # ----------------------------------------------------------------------
    def close_approaches_for(self, name_or_designation: str, *, date_min: str | None = None,
                             date_max: str | None = None, body: str | None = None, limit: int = 100) -> list[dict] | None:
        obj_id = self._resolve_id(name_or_designation)
        if not obj_id:
            return None
        clauses, params = ["object_id = ?"], [obj_id]
        if date_min:
            clauses.append("cd_iso >= ?")
            params.append(date_min)
        if date_max:
            clauses.append("cd_iso <= ?")
            params.append(date_max)
        if body:
            clauses.append("body = ? COLLATE NOCASE")
            params.append(body)
        params.append(self._lim(limit, 1000))
        with self._conn() as conn:
            if not self._has_table(conn, "close_approaches"):
                return []
            return [dict(r) for r in conn.execute(
                f"SELECT body, cd_jd, cd_iso, dist_au, dist_min_au, dist_max_au, v_rel_km_s, v_inf_km_s, t_sigma, orbit_ref, source "
                f"FROM close_approaches WHERE {' AND '.join(clauses)} ORDER BY cd_jd LIMIT ?", params)]

    def close_approaches_between(self, date_min: str, date_max: str, *, body: str = "Earth",
                                 max_dist_au: float = 0.05, limit: int = 200) -> list[dict]:
        """Every close approach to `body` in a window, nearest first."""
        with self._conn() as conn:
            if not self._has_table(conn, "close_approaches"):
                return []
            return [dict(r) for r in conn.execute(
                """
                SELECT ca.object_id, o.name, o.designation, ca.body, ca.cd_iso, ca.dist_au, ca.dist_min_au,
                       ca.v_rel_km_s, v.absolute_magnitude_h, p.radius_km
                FROM close_approaches ca
                JOIN objects o ON o.id = ca.object_id
                LEFT JOIN visual_properties v ON v.object_id = ca.object_id
                LEFT JOIN physical_properties p ON p.object_id = ca.object_id
                WHERE ca.cd_iso >= ? AND ca.cd_iso <= ? AND ca.body = ? COLLATE NOCASE AND ca.dist_au <= ?
                ORDER BY ca.dist_au LIMIT ?
                """, (date_min, date_max, body, max(0.0, min(float(max_dist_au), 1.0)), self._lim(limit, 1000)))]

    def _one(self, table: str, name_or_designation: str) -> dict | None:
        obj_id = self._resolve_id(name_or_designation)
        if not obj_id:
            return None
        with self._conn() as conn:
            if not self._has_table(conn, table):
                return {}
            row = conn.execute(f"SELECT * FROM {table} WHERE object_id = ?", (obj_id,)).fetchone()
            return dict(row) if row else {}

    def get_discovery(self, name_or_designation: str) -> dict | None:
        return self._one("discoveries", name_or_designation)

    def get_atmosphere(self, name_or_designation: str) -> dict | None:
        out = self._one("atmospheres", name_or_designation)
        if out and out.get("composition_json"):
            try:
                out["composition"] = json.loads(out.pop("composition_json"))
            except ValueError:
                out["composition"] = None
        return out

    def get_designations(self, name_or_designation: str) -> list[dict] | None:
        obj_id = self._resolve_id(name_or_designation)
        if not obj_id:
            return None
        with self._conn() as conn:
            if not self._has_table(conn, "designations"):
                return []
            return [dict(r) for r in conn.execute(
                "SELECT designation, kind, source FROM designations WHERE object_id = ? ORDER BY kind, designation", (obj_id,))]

    # ----------------------------------------------------------------------
    # Meteor showers (IAU Meteor Data Center)
    # ----------------------------------------------------------------------
    def list_meteor_showers(self, *, established_only: bool = False,
                            active_on: str | None = None, limit: int = 500) -> list[dict]:
        """IAU Meteor Data Center shower list — one row per parameter set (a
        shower typically has several, from different observation campaigns).

        `established_only` keeps only rows whose MDC status code is 1
        ("single established shower, group") or 6 ("member of the established
        group") — an exact code match, not a text match against the label
        (status 2, "to be established shower", is deliberately excluded).
        `active_on` (an ISO YYYY-MM-DD date) keeps only showers whose
        solar-longitude activity peak is within 15 degrees (circular) of the
        Sun's ecliptic longitude on that date — raises ValueError for an
        unparseable date.
        """
        target_l: float | None = None
        if active_on is not None:
            try:
                target_l = solar_longitude_deg(date.fromisoformat(active_on))
            except ValueError as e:
                raise ValueError(f"active_on must be an ISO date (YYYY-MM-DD): {active_on!r}") from e
        with self._conn() as conn:
            if not self._has_table(conn, "meteor_showers"):
                return []
            clauses: list[str] = []
            params: list[Any] = []
            if established_only:
                clauses.append("status_code IN (1, 6)")
            if target_l is not None:
                # Circular distance in SQL, evaluated before LIMIT so the window
                # applies to the whole table (~1,420 live rows) rather than only
                # the first `limit` rows in iau_no/ad_no order. SQLite's two-arg
                # scalar MIN (not the aggregate MIN) picks the shorter arc.
                clauses.append(
                    "(solar_longitude_deg IS NOT NULL AND "
                    "MIN(ABS(solar_longitude_deg - ?) % 360.0, "
                    "360.0 - (ABS(solar_longitude_deg - ?) % 360.0)) <= 15.0)"
                )
                params.extend([target_l, target_l])
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            return [dict(r) for r in conn.execute(
                f"""
                SELECT iau_no, ad_no, code, name, status_code, status_label, activity,
                       solar_longitude_deg, ra_deg, dec_deg, dra_deg_per_day, ddec_deg_per_day,
                       vg_km_s, a_au, q_au, e, peri_deg, node_deg, incl_deg, n_members,
                       shower_group, parent_body, parent_object_id, technique, reference,
                       submitted_on, source
                FROM meteor_showers
                {where}
                ORDER BY iau_no, ad_no
                LIMIT ?
                """, [*params, self._lim(limit, 1000)])]

    def get_meteor_shower(self, code_or_name: str) -> dict | None:
        """One IAU-registered meteor shower (all its parameter sets, plus its
        parent body when the MDC has linked one), matched by IAU code (e.g.
        "GEM") or name (e.g. "Geminids"), case-insensitively."""
        with self._conn() as conn:
            if not self._has_table(conn, "meteor_showers"):
                return None
            head = conn.execute(
                "SELECT iau_no FROM meteor_showers WHERE code = ? COLLATE NOCASE "
                "OR name = ? COLLATE NOCASE ORDER BY iau_no LIMIT 1",
                (code_or_name, code_or_name)).fetchone()
            if not head:
                return None
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM meteor_showers WHERE iau_no = ? ORDER BY ad_no",
                (head["iau_no"],))]
            first = rows[0]
            # Prefer a parameter set the MDC has actually linked to a parent
            # body over the first one (which is frequently an earlier, less
            # complete campaign with no parent_object_id at all).
            head_row = next((r for r in rows if r.get("parent_object_id")), first)
            parent = None
            parent_id = head_row.get("parent_object_id")
            if parent_id:
                prow = conn.execute(
                    "SELECT id, name, designation, object_type FROM objects WHERE id = ?",
                    (parent_id,)).fetchone()
                parent = dict(prow) if prow else None
            return {
                "iau_no": first["iau_no"],
                "code": first["code"],
                "name": first["name"],
                "status_label": head_row["status_label"],
                "parameter_sets": rows,
                "parent": parent,
            }

    def download_manifest(self) -> dict | None:
        """The published-artefact manifest (latest.json) written by the nightly
        publish step, if this host has one. None before the first publish."""
        path = Path(os.environ.get("SOLAR_MANIFEST_PATH", str(self.db_path.parent / "latest.json")))
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except ValueError:
            return None

    # ----------------------------------------------------------------------
    # Reference / discovery
    # ----------------------------------------------------------------------
    def list_object_types(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM v_object_counts"
            )]

    def get_sources(self) -> list[dict]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT source_name, COUNT(*) AS n,
                       MIN(retrieved_at) AS first_seen,
                       MAX(retrieved_at) AS last_seen
                FROM sources
                GROUP BY source_name
                ORDER BY n DESC
                """,
            )]

    def get_schema(self) -> str:
        with self._conn() as conn:
            ddl = []
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' "
                "ORDER BY (type='view'), name"
            ):
                if row["sql"]:
                    ddl.append(row["sql"] + ";")
        return "\n\n".join(ddl)

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            counts = {r["object_type"]: r["n"]
                      for r in conn.execute("SELECT * FROM v_object_counts")}
            total = sum(counts.values())
            meta = conn.execute(
                "SELECT * FROM build_meta ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_refresh = dict(meta) if meta else None
        return {
            "total_objects": total,
            "by_object_type": counts,
            "last_build": last_refresh,
            "db_path": str(self.db_path),
        }

    def get_orbital_elements(self, name_or_designation: str) -> dict | None:
        """Get just the orbital elements record (for compute_position)."""
        obj_id = self._resolve_id(name_or_designation)
        if not obj_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT o.name, o.designation, oe.* FROM objects o "
                "JOIN orbital_elements oe ON oe.object_id = o.id "
                "WHERE o.id = ?", (obj_id,),
            ).fetchone()
        return dict(row) if row else None
