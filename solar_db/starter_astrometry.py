"""Verified coordinate interpretations, not propagated sky positions.

Exact catalogue identifiers and values bind the pinned source rows to published
VOTable coordinate metadata. No frame is assigned by name or angular proximity.
"""

from __future__ import annotations

import hashlib
import json
import math
from importlib.resources import files
from xml.etree import ElementTree

NS = {"v": "http://www.ivoa.net/xml/VOTable/v1.3"}


def _table(data: bytes, expected_frame: str, coordinate_names: tuple[str, str]):
    xml = ElementTree.fromstring(data)
    system_nodes = xml.findall(".//v:COOSYS", NS)
    systems = {node.attrib["ID"]: node.attrib for node in system_nodes}
    if len(systems) != len(system_nodes):
        raise ValueError("Duplicate astrometry coordinate systems")
    tables = xml.findall(".//v:TABLE", NS)
    if len(tables) != 1:
        raise ValueError("Astrometry evidence must contain one table")
    table = tables[0]
    fields = {field.attrib["name"]: field for field in table.findall("v:FIELD", NS)}
    if len(fields) != len(table.findall("v:FIELD", NS)):
        raise ValueError("Duplicate astrometry evidence columns")
    for name in coordinate_names:
        field = fields.get(name)
        system = systems.get(field.attrib.get("ref")) if field is not None else None
        unit_key = "xtype" if expected_frame == "eq_FK5" else "unit"
        unit = (
            ("hms" if name == "RAJ2000" else "dms")
            if expected_frame == "eq_FK5"
            else "deg"
        )
        if field is not None and field.attrib.get(unit_key) != unit:
            raise ValueError("Astrometry coordinate units changed")
        if not system or system.get("system") != expected_frame:
            raise ValueError(
                "Astrometry coordinate frame is not declared on the fields"
            )
        if system.get("epoch") not in ("2000.000", "J2000.0"):
            raise ValueError("Astrometry reference epoch changed")
        if expected_frame == "eq_FK5" and system.get("equinox") != "J2000":
            raise ValueError("FK5 equinox changed")
    records = []
    for tr in table.findall("v:DATA/v:TABLEDATA/v:TR", NS):
        cells = tr.findall("v:TD", NS)
        if len(cells) != len(fields):
            raise ValueError("Malformed astrometry evidence row")
        records.append(dict(zip(fields, [cell.text or "" for cell in cells])))
    return fields, records


def _angle(value: str, ra: bool):
    sign = -1 if value.startswith("-") else 1
    parts = value.lstrip("+-").split()
    if len(parts) != 3:
        raise ValueError("Malformed evidence coordinate")
    a, b, c = map(float, parts)
    if (
        not all(math.isfinite(v) for v in (a, b, c))
        or not 0 <= b < 60
        or not 0 <= c < 60
    ):
        raise ValueError("Malformed evidence coordinate")
    return sign * (a + b / 60 + c / 3600) * (15 if ra else 1)


def attach_astrometry(records: list[dict], sources: dict) -> None:
    """Validate all source/frame bindings before assigning additive metadata."""
    root = files("solar_db.data").joinpath("starter/astrometry")
    manifest = json.loads(root.joinpath("manifest.json").read_text())
    additions = {}
    source_additions = {}
    epochs = {}
    for source in ("bsc5p", "openngc"):
        evidence = manifest[source]
        data = root.joinpath(evidence["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != evidence["response_sha256"]:
            raise ValueError("Astrometry evidence hash mismatch")
        bright = source == "bsc5p"
        fields, rows = _table(
            data,
            "eq_FK5" if bright else "ICRS",
            ("RAJ2000", "DEJ2000") if bright else ("raj2000", "dej2000"),
        )
        key = "HR" if bright else "name"
        index = {row[key]: row for row in rows}
        if len(index) != len(rows) or len(rows) != evidence["matched_records"]:
            raise ValueError("Duplicate or missing astrometry evidence identities")
        if bright:
            for name in ("pmRA", "pmDE"):
                field = fields.get(name)
                if (
                    field is None
                    or field.attrib.get("ref") != fields["RAJ2000"].attrib["ref"]
                    or field.attrib.get("unit") != "arcsec/yr"
                ):
                    raise ValueError("Proper-motion frame or units changed")
        selected = [record for record in records if record["source"] == source]
        expected_ids = {
            record["source_data"]["hr" if bright else "Name"] for record in selected
        }
        if set(index) | set(evidence["unsupported_identifiers"]) != expected_ids or set(
            index
        ) & set(evidence["unsupported_identifiers"]):
            raise ValueError(
                "Astrometry evidence does not match the exact starter identities"
            )
        source_additions[source] = evidence
        for record in selected:
            identity = record["source_data"]["hr" if bright else "Name"]
            matched = index.get(identity)
            astrometry = {
                "status": "unsupported",
                "frame": None,
                "equinox": None,
                "reference_epoch_jyear": None,
                "observation_epoch_jyear": None,
                "pm_ra_cosdec_arcsec_per_year": None,
                "pm_dec_arcsec_per_year": None,
                "motion_model": None,
                "coordinates_propagated": False,
                "evidence_source": source,
                "matched_identifier": None,
                "unsupported_reason": "No matching row in the pinned author-linked coordinate-frame publication.",
            }
            if matched is not None:
                ra = (
                    _angle(matched["RAJ2000"], True)
                    if bright
                    else float(matched["raj2000"])
                )
                dec = (
                    _angle(matched["DEJ2000"], False)
                    if bright
                    else float(matched["dej2000"])
                )
                tolerance = 0.000050001 if bright else 1e-10
                if (
                    not math.isfinite(ra)
                    or not math.isfinite(dec)
                    or abs(ra - record["ra_deg"]) > tolerance
                    or abs(dec - record["dec_deg"]) > tolerance
                ):
                    raise ValueError(
                        "Coordinate evidence differs from the pinned source row"
                    )
                astrometry.update(
                    status="verified",
                    frame="FK5" if bright else "ICRS",
                    equinox="J2000.0" if bright else None,
                    reference_epoch_jyear=2000.0,
                    matched_identifier=identity,
                    unsupported_reason=None,
                    motion_model="linear_angular_proper_motion"
                    if bright
                    else "static_catalogue_direction",
                )
                if bright:
                    for original, column, output in [
                        ("pmra", "pmRA", "pm_ra_cosdec_arcsec_per_year"),
                        ("pmdec", "pmDE", "pm_dec_arcsec_per_year"),
                    ]:
                        raw = record["source_data"][original]
                        value = None if raw == "" else float(raw)
                        measured = (
                            None if matched[column] == "" else float(matched[column])
                        )
                        if value != measured or (
                            value is not None and not math.isfinite(value)
                        ):
                            raise ValueError(
                                "Proper motion differs from the exact source row"
                            )
                        astrometry[output] = value
                    if (
                        astrometry["pm_ra_cosdec_arcsec_per_year"] is None
                        or astrometry["pm_dec_arcsec_per_year"] is None
                    ):
                        astrometry["motion_model"] = "static_catalogue_direction"
                    epochs[record["id"]] = (
                        "J2000.0 reference epoch; individual observation epoch not reported"
                    )
            additions[record["id"]] = astrometry
    for source, evidence in source_additions.items():
        sources[source]["astrometry_evidence"] = evidence
    for record in records:
        record["astrometry"] = additions[record["id"]]
        if record["id"] in epochs:
            record["coordinate_epoch"] = epochs[record["id"]]
