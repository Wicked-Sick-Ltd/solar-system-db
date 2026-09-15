"""Resolve a catalogue object and produce its sky report.

Shared by the REST API and the MCP server so the two cannot drift. Moons
have planetocentric elements, so they always report their parent's sky
position (they sit within a fraction of a degree of it at this precision).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .data_access import SolarDB
from .positions import date_to_jd
from .sky import sky_report


class SkyLookupError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _has_elements(elements: dict[str, Any] | None) -> bool:
    return bool(elements) and elements.get("semi_major_axis_au") is not None


def resolve_and_report(db: SolarDB, name_or_designation: str, date: str | None = None,
                       lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
    if (lat is None) != (lon is None):
        raise SkyLookupError(422, "Provide both lat and lon, or neither.")
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):  # type: ignore[operator]
        raise SkyLookupError(422, "lat must be within [-90, 90] and lon within [-180, 180].")

    obj = db.get_object(name_or_designation)
    if not obj:
        raise SkyLookupError(404, f"No object found matching {name_or_designation!r}")

    is_sun = obj["id"] == "sun"
    resolved_from = None
    elements = None
    if not is_sun:
        source_id = obj["id"]
        if obj.get("object_type") == "moon" and obj.get("parent_id"):
            source_id = obj["parent_id"]
            resolved_from = source_id
        elements = db.get_orbital_elements(source_id)
        if not _has_elements(elements):
            raise SkyLookupError(404, f"No propagatable orbital elements for {obj['name']!r}")

    earth = db.get_orbital_elements("planet-earth")
    if not _has_elements(earth):
        raise SkyLookupError(500, "Earth's orbital elements are missing from the catalogue.")

    when = date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        jd = date_to_jd(when)
        report = sky_report(elements, earth, jd, lat=lat, lon=lon, is_sun=is_sun)  # type: ignore[arg-type]
    except ValueError as e:
        raise SkyLookupError(422, str(e)) from e

    return {
        "name": obj["name"],
        "designation": obj.get("designation"),
        "input_datetime": when,
        "resolved_from": resolved_from,
        **report,
    }
