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
