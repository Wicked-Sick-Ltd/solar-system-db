"""Minor Planet Center — discovery circumstances for every numbered minor planet.

Source: https://minorplanetcenter.net/iau/lists/NumberedMPs.txt (one fixed-width
line per numbered body: number, name, discovery date, site, discoverer(s)).
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import add_source, fetch_text  # noqa: E402

MPC_NUMBERED_URL = "https://minorplanetcenter.net/iau/lists/NumberedMPs.txt"
SOURCE_NAME = "IAU Minor Planet Center"

# Fixed-width layout (verified against the 2026-09 file):
#   [0:8]   "(NNNNNN)"   [9:29] name   [29:41] provisional designation (may be blank)
#   [41:51] "YYYY MM DD" [51] "*" when the discovery date is by MPC convention rather than first observation
#   [53:78] discovery site   [78:] discoverer(s)
_NUMBER = re.compile(r"^\s*\((\d+)\)")


def parse_line(line: str) -> dict[str, Any] | None:
    m = _NUMBER.match(line)
    if not m or len(line) < 52:
        return None
    date = line[41:51]
    if not re.fullmatch(r"\d{4} \d{2} \d{2}", date):
        return None
    y, mo, d = date.split()
    name = line[9:29].strip() or None
    prov = line[29:41].strip() or None
    site_field = line[53:78].strip()
    site_code = None
    if site_field:
        parts = site_field.rsplit(None, 1)
        # The column header on minorplanetcenter.net (e.g.
        # /iau/lists/NumberedMPs005001.html) labels this trailing token
        # "Name Ref." — a reference to the Minor Planet Circular that
        # published the naming citation, NOT a site/observatory code (those
        # are 3-4 char alphanumeric, e.g. "691"). Confirmed 2026-09-17 by
        # fetching that page. So strip it out of the site name but do not
        # store it as a site code anywhere.
        if len(parts) == 2 and parts[1].isdigit():
            site_field, site_code = parts[0], int(parts[1])
    return {
        "number": int(m.group(1)),
        "name": name,
        "provisional": prov,
        "discovered_on": f"{y}-{mo}-{d}",
        "date_is_conventional": line[51:52] == "*",
        "site": site_field or None,
        "site_code": site_code,
        "discoverer": line[78:].strip() or None,
    }


def parse_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        rec = parse_line(line)
        if rec:
            yield rec


def fetch_numbered(timeout: int = 300) -> Iterator[dict[str, Any]]:
    text = fetch_text(MPC_NUMBERED_URL, timeout=timeout)
    return parse_lines(text.splitlines())


def write_discoveries(conn, records: Iterable[dict[str, Any]], *, commit_every: int = 50000) -> dict[str, int]:
    """Resolve number → object_id via designations(kind='number') and upsert."""
    number_to_id = {
        r[0]: r[1] for r in conn.execute("SELECT designation, object_id FROM designations WHERE kind = 'number'")
    }
    n_written = n_unmatched = 0
    for i, rec in enumerate(records, 1):
        obj_id = number_to_id.get(str(rec["number"]))
        if not obj_id:
            n_unmatched += 1
            continue
        conn.execute(
            """
            INSERT INTO discoveries (object_id, discovered_on, discoverer, site, reference, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
                discovered_on = COALESCE(excluded.discovered_on, discoveries.discovered_on),
                discoverer    = COALESCE(excluded.discoverer, discoveries.discoverer),
                site          = COALESCE(excluded.site, discoveries.site),
                reference     = COALESCE(excluded.reference, discoveries.reference),
                source        = excluded.source,
                updated_at    = strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (obj_id, rec["discovered_on"], rec["discoverer"], rec["site"],
             "MPC numbered list; date by MPC convention" if rec.get("date_is_conventional") else "MPC numbered list",
             SOURCE_NAME),
        )
        if rec.get("provisional"):
            conn.execute(
                "INSERT OR IGNORE INTO designations (object_id, designation, kind, source) VALUES (?, ?, 'provisional', ?)",
                (obj_id, rec["provisional"], SOURCE_NAME),
            )
        # Keep the v1 convenience columns populated where the seed left them empty.
        conn.execute(
            "UPDATE objects SET discoverer = COALESCE(discoverer, ?), discovery_date = COALESCE(discovery_date, ?) WHERE id = ?",
            (rec["discoverer"], rec["discovered_on"], obj_id),
        )
        n_written += 1
        if i % commit_every == 0:
            conn.commit()
    if n_written:
        add_source(conn, object_id=None, table_name="discoveries", source_name=SOURCE_NAME, source_url=MPC_NUMBERED_URL)
    conn.commit()
    return {"discoveries": n_written, "unmatched": n_unmatched}
