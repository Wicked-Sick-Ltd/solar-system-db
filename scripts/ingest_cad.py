"""JPL close-approach data (cad.api): every close approach of any small body
to a planet or the Moon inside a date window, in bulk."""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import add_source, fetch_json  # noqa: E402

CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api"
SOURCE_NAME = "JPL CAD"
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _f(x: Any) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def cd_to_iso(cd: str) -> str:
    """'2026-Sep-01 02:53' → '2026-09-01T02:53:00Z'."""
    d, t = cd.split()
    y, mon, day = d.split("-")
    return f"{y}-{_MONTHS[mon]:02d}-{int(day):02d}T{t}:00Z"


def map_row(fields: list[str], row: list[Any]) -> dict[str, Any]:
    r = dict(zip(fields, row))
    return {
        "des": str(r["des"]).strip(),
        "orbit_ref": str(r.get("orbit_id") or "") or None,
        "cd_jd": _f(r.get("jd")),
        "cd_iso": cd_to_iso(r["cd"]),
        "dist_au": _f(r.get("dist")), "dist_min_au": _f(r.get("dist_min")), "dist_max_au": _f(r.get("dist_max")),
        "v_rel_km_s": _f(r.get("v_rel")), "v_inf_km_s": _f(r.get("v_inf")),
        "t_sigma": str(r.get("t_sigma_f") or "") or None,
        "body": str(r.get("body") or "Earth"),
    }


def load_fixture(path: str | Path) -> tuple[list[str], list[list[Any]]]:
    d = json.loads(Path(path).read_text())
    return d["fields"], d.get("data") or []


def iter_window(date_min: str, date_max: str, *, body: str = "ALL", dist_max: str = "0.5",
                timeout: int = 600) -> Iterator[tuple[list[str], list[list[Any]]]]:
    """One request per calendar year keeps each response well under JPL's size cap."""
    y0, y1 = int(date_min[:4]), int(date_max[:4])
    for y in range(y0, y1 + 1):
        params = {"date-min": f"{y}-01-01" if y > y0 else date_min,
                  "date-max": f"{y}-12-31" if y < y1 else date_max,
                  "dist-max": dist_max, "body": body, "fullname": "false"}
        data = fetch_json(CAD_URL, params=params, timeout=timeout)
        rows = data.get("data") or []
        if rows:
            yield data["fields"], rows


def write_close_approaches(conn, fields: list[str], rows: Iterable[list[Any]], *, commit_every: int = 50000) -> dict[str, int]:
    """Resolve `des` (number or provisional designation) → object_id via designations."""
    des_to_id = {
        r[0]: r[1] for r in conn.execute(
            "SELECT designation, object_id FROM designations WHERE kind IN ('number','provisional')")
    }
    n_written = n_unmatched = 0
    batch: list[tuple] = []
    for i, row in enumerate(rows, 1):
        m = map_row(fields, row)
        obj_id = des_to_id.get(m["des"])
        if not obj_id:
            n_unmatched += 1
            continue
        batch.append((obj_id, m["body"], m["cd_jd"], m["cd_iso"], m["dist_au"], m["dist_min_au"], m["dist_max_au"],
                      m["v_rel_km_s"], m["v_inf_km_s"], m["t_sigma"], m["orbit_ref"], SOURCE_NAME))
        n_written += 1
        if len(batch) >= commit_every:
            _flush(conn, batch)
            batch = []
    _flush(conn, batch)
    if n_written:
        add_source(conn, object_id=None, table_name="close_approaches", source_name=SOURCE_NAME, source_url=CAD_URL)
    conn.commit()
    return {"close_approaches": n_written, "unmatched": n_unmatched}


def _flush(conn, batch: list[tuple]) -> None:
    if batch:
        conn.executemany(
            """INSERT OR REPLACE INTO close_approaches
               (object_id, body, cd_jd, cd_iso, dist_au, dist_min_au, dist_max_au, v_rel_km_s, v_inf_km_s, t_sigma, orbit_ref, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )
        conn.commit()
