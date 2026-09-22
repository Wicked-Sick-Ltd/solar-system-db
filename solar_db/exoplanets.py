"""Read-only exoplanet queries shared by REST and MCP."""
from __future__ import annotations

import json
import math

from .galactic import FRAME


def positive_distance(value: float | None) -> float | None:
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError("max_distance_pc must be finite and positive")
    return value


def planet_record(row) -> dict:
    result = dict(row)
    result["source_data"] = json.loads(result["source_data"])
    return result


class ExoplanetQueries:
    def list_exoplanets(self, *, q: str | None = None, discovery_method: str | None = None,
                        max_distance_pc: float | None = None, limit: int = 50, offset: int = 0) -> dict:
        distance = positive_distance(max_distance_pc)
        limit, offset = self._lim(limit, 1000), max(0, int(offset))
        # instr treats %, _ and quotes literally; all user input is bound.
        params = {"q": (q or "").strip(), "method": discovery_method, "distance": distance,
                  "limit": limit, "offset": offset}
        with self._conn() as conn:
            if not self._has_table(conn, "exoplanets"):
                return {"available": False, "results": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}
            rows = conn.execute("""SELECT p.*, h.name AS host_name, h.distance_pc
                FROM exoplanets p JOIN exoplanet_hosts h ON h.id=p.host_id
                WHERE (:q='' OR instr(lower(p.name),lower(:q))>0 OR instr(lower(h.name),lower(:q))>0)
                AND (:method IS NULL OR p.discovery_method=:method)
                AND (:distance IS NULL OR h.distance_pc<=:distance)
                ORDER BY p.name COLLATE NOCASE, p.id LIMIT :limit OFFSET :offset""", params).fetchall()
            total = conn.execute("""SELECT count(*) FROM exoplanets p JOIN exoplanet_hosts h ON h.id=p.host_id
                WHERE (:q='' OR instr(lower(p.name),lower(:q))>0 OR instr(lower(h.name),lower(:q))>0)
                AND (:method IS NULL OR p.discovery_method=:method)
                AND (:distance IS NULL OR h.distance_pc<=:distance)""", params).fetchone()[0]
        return {"available": True, "results": [planet_record(r) for r in rows], "total": total,
                "limit": limit, "offset": offset, "has_more": offset + len(rows) < total}

    def get_exoplanet(self, name_or_id: str) -> dict | None:
        with self._conn() as conn:
            if not self._has_table(conn, "exoplanets"):
                return None
            row = conn.execute("""SELECT p.*, h.name AS host_name, h.distance_pc
                FROM exoplanets p JOIN exoplanet_hosts h ON h.id=p.host_id
                WHERE p.id=? OR p.name=? COLLATE NOCASE LIMIT 1""", (name_or_id, name_or_id)).fetchone()
        return planet_record(row) if row else None

    def get_exoplanet_host(self, name_or_id: str) -> dict | None:
        with self._conn() as conn:
            if not self._has_table(conn, "exoplanet_hosts"):
                return None
            row = conn.execute("SELECT * FROM exoplanet_hosts WHERE id=? OR name=? COLLATE NOCASE LIMIT 1",
                               (name_or_id, name_or_id)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["coordinate_metadata"] = json.loads(result["coordinate_metadata"])
            planets = conn.execute("""SELECT p.*, h.name AS host_name, h.distance_pc
                FROM exoplanets p JOIN exoplanet_hosts h ON h.id=p.host_id
                WHERE p.host_id=? ORDER BY p.name""", (row["id"],)).fetchall()
            result["planets"] = [planet_record(p) for p in planets]
        return result

    def galaxy_map(self, *, max_distance_pc: float | None = None, limit: int = 10000) -> dict:
        distance = positive_distance(max_distance_pc)
        limit = self._lim(limit, 10000)
        with self._conn() as conn:
            if not self._has_table(conn, "exoplanet_hosts"):
                return {"available": False, "results": [], "frame": FRAME, "total_hosts": 0,
                        "mapped_hosts": 0, "unmapped_hosts": 0, "matching_hosts": 0, "truncated": False}
            total, mapped = conn.execute("SELECT count(*),count(x_pc) FROM exoplanet_hosts").fetchone()
            rows = conn.execute("""SELECT h.id,h.name,h.distance_pc,h.distance_error_plus_pc,h.distance_error_minus_pc,
                h.x_pc,h.y_pc,h.z_pc,h.galactocentric_x_pc,h.galactocentric_y_pc,h.galactocentric_z_pc,
                count(p.id) AS planet_count
                FROM exoplanet_hosts h JOIN exoplanets p ON p.host_id=h.id
                WHERE h.x_pc IS NOT NULL AND (:distance IS NULL OR h.distance_pc<=:distance)
                GROUP BY h.id ORDER BY h.distance_pc,h.id LIMIT :limit""",
                {"distance": distance, "limit": limit}).fetchall()
            matching = conn.execute("""SELECT count(*) FROM exoplanet_hosts
                WHERE x_pc IS NOT NULL AND (? IS NULL OR distance_pc<=?)""", (distance, distance)).fetchone()[0]
        return {"available": True, "results": [dict(r) for r in rows], "frame": FRAME,
                "total_hosts": total, "mapped_hosts": mapped, "unmapped_hosts": total - mapped,
                "matching_hosts": matching, "truncated": matching > len(rows)}
