"""Shared REST/MCP input contract; no coercion before validation."""

from dataclasses import dataclass
from datetime import date as Date, datetime, time, timedelta, timezone as UTCZone
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BODIES = ("moon", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune")


class PlanningError(ValueError):
    def __init__(self, detail: str, status: int = 422):
        super().__init__(detail)
        self.status = status


def number(value, name, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PlanningError(f"{name} must be a finite number between {lo} and {hi}.")
    try:
        parsed = float(value)
    except ValueError:
        parsed = math.nan
    if not math.isfinite(parsed) or not lo <= parsed <= hi:
        raise PlanningError(f"{name} must be a finite number between {lo} and {hi}.")
    return parsed


@dataclass(frozen=True)
class NightInput:
    date: str
    timezone: str
    lat: float
    lon: float
    targets: tuple[str, ...]
    min_altitude_deg: float
    sun_altitude_deg: float
    min_moon_separation_deg: float
    start: datetime
    end: datetime

    @classmethod
    def parse(
        cls,
        date,
        timezone,
        lat,
        lon,
        targets="moon,jupiter,saturn",
        min_altitude_deg=20,
        sun_altitude_deg=-12,
        min_moon_separation_deg=0,
    ):
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise PlanningError("date must be YYYY-MM-DD.")
        try:
            day = Date.fromisoformat(date)
        except ValueError as exc:
            raise PlanningError("date must be a real calendar date.") from exc
        if not 1900 <= day.year <= 2100:
            raise PlanningError(
                "date must be within 1900–2100; Earth-orientation coverage further limits availability."
            )
        if (
            not isinstance(timezone, str)
            or len(timezone) > 100
            or timezone.startswith(("/", "."))
        ):
            raise PlanningError("timezone must be an IANA timezone name.")
        try:
            zone = ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise PlanningError("timezone must be an IANA timezone name.") from exc
        if not isinstance(targets, str) or len(targets) > 100:
            raise PlanningError(
                "targets must be a comma-separated list of up to eight supported bodies."
            )
        selected = tuple(targets.split(","))
        if (
            not selected
            or len(selected) > 8
            or len(set(selected)) != len(selected)
            or any(b not in BODIES for b in selected)
        ):
            raise PlanningError(
                "targets must be unique supported body names; Earth and the Sun are excluded."
            )
        lat = round(number(lat, "lat", -90, 90), 2)
        lon = round(number(lon, "lon", -180, 180), 2)
        altitude = number(min_altitude_deg, "min_altitude_deg", 0, 85)
        darkness = number(sun_altitude_deg, "sun_altitude_deg", -18, -6)
        if darkness not in (-6, -12, -18):
            raise PlanningError("sun_altitude_deg must be -6, -12 or -18.")
        moon_sep = number(min_moon_separation_deg, "min_moon_separation_deg", 0, 180)
        starts = datetime.combine(day, time(12), zone)
        ends = datetime.combine(day + timedelta(days=1), time(12), zone)
        # Some historical zone changes skip a whole civil day. Do not normalize
        # an imaginary local noon into an unrelated date without telling callers.
        for local in (starts, ends):
            if local.astimezone(UTCZone.utc).astimezone(zone).replace(
                tzinfo=None
            ) != local.replace(tzinfo=None):
                raise PlanningError(
                    "The requested local noon does not exist in this timezone."
                )
        start, end = starts.astimezone(UTCZone.utc), ends.astimezone(UTCZone.utc)
        if not 22 <= (end - start).total_seconds() / 3600 <= 26:
            raise PlanningError(
                "The local-night interval must be between 22 and 26 hours."
            )
        return cls(
            date, timezone, lat, lon, selected, altitude, darkness, moon_sep, start, end
        )
