"""Bounded, versioned observing catalogue records; never current ephemerides."""

from __future__ import annotations

import hashlib
import json
import math
import re
from importlib.resources import files

FAMILIES = ("bright_star", "double_star", "deep_sky")
NONSTELLAR_HR = {
    92,
    95,
    182,
    1057,
    1841,
    2472,
    2496,
    3515,
    3671,
    6309,
    6515,
    7189,
    7539,
    8296,
}


def number(raw: str, *, minimum=None, maximum=None):
    if raw == "":
        return None
    value = float(raw)
    if (
        not math.isfinite(value)
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        raise ValueError("Invalid catalogue measurement")
    return value


def sexagesimal(raw: str, *, ra=False):
    pattern = (
        r"(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)"
        if ra
        else r"([+-]\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)"
    )
    match = re.fullmatch(pattern, raw)
    if not match:
        raise ValueError("Invalid catalogue coordinate")
    a, b, c = match.groups()
    first, minute, second = abs(int(a)), int(b), float(c)
    bound = 24 if ra else 90
    if (
        first > bound
        or minute >= 60
        or second >= 60
        or (first == bound and (ra or minute or second))
    ):
        raise ValueError("Invalid catalogue coordinate")
    return (
        (-1 if a.startswith("-") else 1)
        * (first + minute / 60 + second / 3600)
        * (15 if ra else 1)
    )


def normalise(source: str, raw: dict) -> dict:
    """Keep original row strings alongside explicitly converted units."""
    if source == "bsc5p":
        hr = int(raw["hr"])
        mag = number(raw["vmag"], maximum=2)
        if hr in NONSTELLAR_HR or not 1 <= hr <= 9110 or mag is None:
            raise ValueError("Outside the bright-star starter selection")
        separation = number(raw["m_sep"], minimum=0)
        # Component identity is required: do not manufacture a pair for blank IDs.
        is_double = bool(raw["m_id"] and separation is not None and separation > 0)
        return dict(
            id=f"bsc5p:hr{hr}",
            name=raw["name"],
            aliases=[v for v in [raw["alt_name"]] if v],
            families=["bright_star"] + (["double_star"] if is_double else []),
            object_type="star",
            ra_deg=number(raw["ra"], minimum=0, maximum=360),
            dec_deg=number(raw["dec"], minimum=-90, maximum=90),
            coordinate_equinox="J2000.0",
            coordinate_epoch=None,
            magnitude=mag,
            magnitude_band="V (BSC5P; retain code/uncertainty flags)",
            magnitude_flag=raw["vmag_uncert"] or None,
            magnitude_code=raw["vmag_code"] or None,
            major_axis_arcmin=None,
            minor_axis_arcmin=None,
            components=raw["m_id"] or None,
            separation_arcsec=separation if is_double else None,
            separation_epoch=None,
            position_angle_deg=None,
            source=source,
            source_data=raw,
        )
    if source != "openngc" or not raw["M"] or raw["Type"] in ("Dup", "**"):
        raise ValueError("Outside the deep-sky starter selection")
    return dict(
        id=f"openngc:{raw['Name']}",
        name=raw["Name"],
        aliases=[f"M {int(raw['M'])}"],
        families=["deep_sky"],
        object_type=raw["Type"],
        ra_deg=sexagesimal(raw["RA"], ra=True),
        dec_deg=sexagesimal(raw["Dec"]),
        coordinate_equinox=None,
        coordinate_epoch="J2000.0 (as documented by OpenNGC)",
        magnitude=number(raw["V-Mag"]),
        magnitude_band="V",
        magnitude_flag=None,
        magnitude_code=None,
        major_axis_arcmin=number(raw["MajAx"], minimum=0),
        minor_axis_arcmin=number(raw["MinAx"], minimum=0),
        components=None,
        separation_arcsec=None,
        separation_epoch=None,
        position_angle_deg=None,
        source=source,
        source_data=raw,
    )


def load_starter_catalogues() -> tuple[list[dict], dict]:
    root = files("solar_db.data").joinpath("starter")
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    records, sources = [], {}
    for source in ("bsc5p", "openngc"):
        data = root.joinpath(f"{source}.json").read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest[source]["sha256"]:
            raise ValueError("Starter snapshot hash mismatch")
        snapshot = json.loads(data)
        if (
            snapshot["source"] != source
            or len(snapshot["rows"]) != manifest[source]["count"]
        ):
            raise ValueError("Starter snapshot metadata mismatch")
        sources[source] = {k: v for k, v in snapshot.items() if k != "rows"}
        sources[source]["snapshot_sha256"] = manifest[source]["sha256"]
        records.extend(normalise(source, row) for row in snapshot["rows"])
    ids = [row["id"] for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate starter identifier")
    for row in records:
        if (
            row["ra_deg"] is None
            or not 0 <= row["ra_deg"] < 360
            or row["dec_deg"] is None
        ):
            raise ValueError("Missing or invalid coordinates")
    return records, sources


class StarterCatalogueQueries:
    @staticmethod
    def _starter_available(conn):
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('starter_targets','starter_sources')"
            )
        }
        if tables != {"starter_targets", "starter_sources"}:
            return False
        return {
            row[0] for row in conn.execute("SELECT source FROM starter_sources")
        } == {"bsc5p", "openngc"}

    def list_starter_targets(self, *, family=None, q=None, limit=50, offset=0):
        if family is not None and family not in FAMILIES:
            raise ValueError("Unsupported starter family")
        if q is not None and (not isinstance(q, str) or len(q) > 200):
            raise ValueError("Search must be text of at most 200 characters")
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or not 0 <= offset <= 1000
        ):
            raise ValueError("Invalid starter pagination")
        with self._conn() as conn:
            if not self._starter_available(conn):
                return dict(
                    available=False,
                    results=[],
                    total=0,
                    sources={},
                    coverage="bounded starter sample",
                )
            rows = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload FROM starter_targets ORDER BY id"
                )
            ]
            sources = {
                row[0]: json.loads(row[1])
                for row in conn.execute(
                    "SELECT source,payload FROM starter_sources ORDER BY source"
                )
            }
        if family:
            rows = [r for r in rows if family in r["families"]]
        if q:
            needle = q.casefold()
            rows = [
                r
                for r in rows
                if any(
                    needle in text.casefold()
                    for text in [r["id"], r["name"], *r["aliases"]]
                )
            ]
        return dict(
            available=True,
            results=rows[offset : offset + limit],
            total=len(rows),
            limit=limit,
            offset=offset,
            sources=sources,
            coverage="bounded starter sample; catalogue coordinates, not current ephemerides",
        )

    def get_starter_target(self, target_id):
        if not isinstance(target_id, str) or len(target_id) > 100:
            raise ValueError("Invalid starter identifier")
        # Exact identifiers only; aliases are discoverable through list search.
        with self._conn() as conn:
            if not self._starter_available(conn):
                return None
            row = conn.execute(
                "SELECT payload FROM starter_targets WHERE id=?", (target_id,)
            ).fetchone()
            if row is None:
                return None
            result = json.loads(row[0])
            source = conn.execute(
                "SELECT payload FROM starter_sources WHERE source=?",
                (result["source"],),
            ).fetchone()
            return dict(result, provenance=json.loads(source[0]))
