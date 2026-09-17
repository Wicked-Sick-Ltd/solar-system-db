"""IAU Meteor Data Center shower list: pipe-delimited, quoted, header legend for status codes.

Source: https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt

Header/comment lines start with ":" (not "#"). Data rows are quoted,
pipe-delimited, space-padded, and end with a trailing pipe, so csv.reader
yields one extra empty trailing field per row (harmless, ignored by zip()).

The header itself carries at least one non-UTF-8 byte (a Latin-1 accented
character in a contributor's name), so any code that reads the raw file must
decode leniently rather than crash on it.
"""
from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fetch_bytes  # noqa: E402

MDC_URL = "https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt"
SOURCE_NAME = "IAU Meteor Data Center (Jenniskens et al. 2020; Hajdukova & Rudawska)"

# Column order in the data rows, verified against the 2022-02-28 header's two
# ruler lines (`LP IAUNo AdNo Code shower name activity s LaSun Ra De dRa dDe
# Vg a q e peri node inc N Group CG Parent body Remarks Ote References LT SD`).
# "lp", "cg", "remarks" and "lt" have no matching meteor_showers column and are
# parsed only to keep field positions aligned, then dropped.
COLS = [
    "lp", "iau_no", "ad_no", "code", "name", "activity", "status_code",
    "solar_longitude_deg", "ra_deg", "dec_deg", "dra_deg_per_day", "ddec_deg_per_day",
    "vg_km_s", "a_au", "q_au", "e", "peri_deg", "node_deg", "incl_deg",
    "n_members", "shower_group", "cg", "parent_body", "remarks", "technique",
    "reference", "lt", "submitted_on",
]
_DROP = ("lp", "cg", "remarks", "lt")

# The header's status legend looks like:
#   :s     - shower status
#   :     -2 shower removed from the MDC lists, but in the future, it can return to the database
#   :      0 single shower, working list
#   :      1 single established shower, group
#   ...
# i.e. no separator character between the code and its label, just whitespace.
_LEGEND = re.compile(r"^[:#]\s*(-?\d+)\s+(.+?)\s*$")


def _num(v: str | None, kind=float):
    v = (v or "").strip()
    if v in ("", "-", "--", "?"):
        return None
    try:
        return kind(v)
    except ValueError:
        return None


def parse_legend(header_lines: list[str]) -> dict[int, str]:
    legend: dict[int, str] = {}
    in_status = False
    for line in header_lines:
        low = line.lower()
        if "shower status" in low:
            in_status = True
            continue
        if in_status:
            m = _LEGEND.match(line)
            if m:
                legend[int(m.group(1))] = m.group(2)
            elif legend:
                in_status = False
    return legend


def parse_showers(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    header = [l for l in lines if l.startswith(":") or l.startswith("#")]
    legend = parse_legend(header)
    data = [l for l in lines if l.startswith('"')]
    out = []
    for rec in csv.reader(io.StringIO("\n".join(data)), delimiter="|", quotechar='"', skipinitialspace=True):
        if len(rec) < len(COLS):
            continue
        row = {k: (v.strip() or None) for k, v in zip(COLS, rec)}
        for k in ("iau_no", "ad_no", "status_code", "n_members"):
            row[k] = _num(row[k], int)
        for k in ("solar_longitude_deg", "ra_deg", "dec_deg", "dra_deg_per_day", "ddec_deg_per_day", "vg_km_s",
                  "a_au", "q_au", "e", "peri_deg", "node_deg", "incl_deg"):
            row[k] = _num(row[k])
        for k in _DROP:
            row.pop(k, None)
        row["status_label"] = legend.get(row["status_code"]) if row["status_code"] is not None else None
        if row["iau_no"] is None or not row["code"]:
            continue
        out.append(row)
    return out


_NUM = re.compile(r"^\s*\(?(\d+)\)?")
_COMET = re.compile(r"\b(\d+[PDCIX])(?:/|\b)")


def resolve_parent(conn, parent_body: str | None) -> str | None:
    """Match a shower's free-text parent body to an ingested object.id.

    Tries, in order: a comet designation like "1P" (or "109P/Swift-Tuttle"),
    a leading asteroid number like "3200" (or "(3200) Phaethon"), then a
    plain name match. Returns None (never raises) when nothing matches, so
    callers can count unresolved parents instead of crashing on them.
    """
    if not parent_body:
        return None
    m = _COMET.search(parent_body)
    if m:
        r = conn.execute("SELECT object_id FROM designations WHERE designation = ? LIMIT 1", (m.group(1),)).fetchone()
        if r:
            return r[0]
        r = conn.execute("SELECT id FROM objects WHERE designation LIKE ? AND object_type='comet' LIMIT 1",
                          (m.group(1) + "/%",)).fetchone()
        if r:
            return r[0]
    m = _NUM.match(parent_body)
    if m:
        r = conn.execute("SELECT object_id FROM designations WHERE designation = ? AND kind='number' LIMIT 1",
                          (m.group(1),)).fetchone()
        if r:
            return r[0]
    name = parent_body.split("/")[-1].strip()
    r = conn.execute("SELECT id FROM objects WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)).fetchone()
    return r[0] if r else None


def write_showers(conn, rows: list[dict[str, Any]]) -> dict[str, int]:
    """Insert/replace shower rows keyed on (iau_no, ad_no), resolving parent bodies.

    Records one provenance row for the whole run (not one per shower) since
    every row comes from the same fetch of the same source document.
    """
    n = res = unres = 0
    cols = ["iau_no", "ad_no", "code", "name", "activity", "status_code", "status_label", "solar_longitude_deg",
            "ra_deg", "dec_deg", "dra_deg_per_day", "ddec_deg_per_day", "vg_km_s", "a_au", "q_au", "e", "peri_deg",
            "node_deg", "incl_deg", "n_members", "shower_group", "parent_body", "parent_object_id", "technique",
            "reference", "submitted_on", "source"]
    sql = f"INSERT OR REPLACE INTO meteor_showers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    for r in rows:
        pid = resolve_parent(conn, r.get("parent_body"))
        res += pid is not None
        unres += pid is None and bool(r.get("parent_body"))
        r = {**r, "parent_object_id": pid, "source": SOURCE_NAME}
        conn.execute(sql, [r.get(c) for c in cols])
        n += 1
    from common import add_source
    if n:
        add_source(conn, object_id=None, table_name="meteor_showers", source_name=SOURCE_NAME, source_url=MDC_URL)
    conn.commit()
    return {"showers": n, "parents_resolved": res, "parents_unresolved": unres}


def fetch_showers(timeout: int = 120) -> str:
    """Fetch the raw shower list, decoding leniently.

    The file carries at least one stray non-UTF-8 byte, so we read it as
    bytes (via common.fetch_bytes, for the same retry/429/backoff handling
    as every other fetcher in this repo) and decode with errors="replace"
    rather than let requests' own text-decoding guess (or a strict decode)
    raise/garble the whole file.
    """
    raw = fetch_bytes(MDC_URL, timeout=timeout)
    return raw.decode("utf-8", errors="replace")
