"""One local night of geometric planning, independent of weather/catalogue data."""

from datetime import datetime, timezone
from functools import lru_cache
import math

import numpy as np

from threading import BoundedSemaphore

from .ephemeris import BuiltinEphemeris
from .inputs import PlanningError
from .worker import run_jpl_worker, configured_provider

PLANNING_CAPACITY = BoundedSemaphore(2)
from .inputs import NightInput

SAMPLE_SECONDS = 300
ROOT_TOLERANCE_SECONDS = 1


def utc(timestamp):
    return (
        datetime.fromtimestamp(timestamp, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def bisect_root(function, lo, hi, tolerance=ROOT_TOLERANCE_SECONDS):
    left = function(lo)
    for _ in range(40):
        if hi - lo <= tolerance:
            break
        mid = (lo + hi) / 2
        value = function(mid)
        if (value >= 0) == (left >= 0):
            lo, left = mid, value
        else:
            hi = mid
    return (lo + hi) / 2


def threshold_events(function, grid):
    """Refine extrema before threshold crossings, including sub-grid grazes.

    Solar-system tracks change smoothly across this bounded night. A derivative
    bracket finds a short window around a maximum even when every five-minute
    altitude sample is below the threshold. A near-tangent result is explicitly
    unresolved rather than asserted to be an empty observing window.
    """
    start, end = grid[0], grid[-1]

    def derivative(t):
        if t <= start:
            return (-3 * function(t) + 4 * function(t + 1) - function(t + 2)) / 2
        if t >= end:
            return (3 * function(t) - 4 * function(t - 1) + function(t - 2)) / 2
        a, b = max(start, t - 1), min(end, t + 1)
        return (function(b) - function(a)) / (b - a)

    slopes = [derivative(t) for t in grid]
    points = list(grid)
    grazing = any(
        abs(slope) < 1e-12 and abs(function(t)) < 1e-5 for t, slope in zip(grid, slopes)
    )
    for index in range(len(grid) - 1):
        if slopes[index] * slopes[index + 1] < 0:
            extremum = bisect_root(
                derivative, grid[index], grid[index + 1], tolerance=0.001
            )
            points.append(extremum)
            grazing = grazing or abs(function(extremum)) < 1e-5
    points.sort()
    events = []
    for a, b in zip(points, points[1:]):
        fa, fb = function(a), function(b)
        if fa == 0:
            events.append(a)
        if fa * fb < 0:
            events.append(bisect_root(function, a, b))
    return events, grazing


def intervals(events, start, end, predicate):
    points = sorted(set([start, end, *events]))
    accepted = []
    for a, b in zip(points, points[1:]):
        if b - a <= 0 or not predicate((a + b) / 2):
            continue
        if accepted and abs(accepted[-1][1] - a) < 0.001:
            accepted[-1] = (accepted[-1][0], b)
        else:
            accepted.append((a, b))
    return [
        {"start_utc": utc(a), "end_utc": utc(b)}
        for a, b in accepted
        if b - a >= ROOT_TOLERANCE_SECONDS
    ]


def plan_night(
    date,
    timezone,
    lat,
    lon,
    targets="moon,jupiter,saturn",
    min_altitude_deg=20,
    sun_altitude_deg=-12,
    min_moon_separation_deg=0,
    *,
    provider_factory=None,
):
    request = NightInput.parse(
        date,
        timezone,
        lat,
        lon,
        targets,
        min_altitude_deg,
        sun_altitude_deg,
        min_moon_separation_deg,
    )
    if not PLANNING_CAPACITY.acquire(blocking=False):
        raise PlanningError("Observing planner is busy; try again later.", 503)
    provider = None
    try:
        if provider_factory is None:
            name = configured_provider()
            if name == "jpl-de440s":
                return run_jpl_worker(request)
            provider_factory = BuiltinEphemeris
        provider = provider_factory(request)
        return _plan(request, provider)
    finally:
        close = getattr(provider, "close", None)
        try:
            if close is not None:
                close()
        finally:
            PLANNING_CAPACITY.release()


def _plan(request, provider):
    start, end = request.start.timestamp(), request.end.timestamp()
    grid = np.linspace(
        start, end, math.ceil((end - start) / SAMPLE_SECONDS) + 1
    ).tolist()
    # Seed all derivatives in one vectorized request; refine only event brackets.
    times = sorted(
        set(
            t for base in grid for t in (max(start, base - 1), base, min(end, base + 1))
        )
    )
    bodies = tuple(dict.fromkeys(("moon", *request.targets)))
    initial = provider.positions(times, bodies)
    index = {t: i for i, t in enumerate(times)}

    @lru_cache(maxsize=8192)
    def point(body, t):
        if t in index:
            i, data = index[t], initial
        else:
            i, data = 0, provider.positions([t], (body,))
        return {
            **{k: float(v[i]) for k, v in data[body].items()},
            "sun_altitude_deg": float(data["sun_altitude_deg"][i]),
            "moon_altitude_deg": float(data["moon_altitude_deg"][i]),
        }

    dark = lambda t: request.sun_altitude_deg - point("moon", t)["sun_altitude_deg"]
    dark_events, dark_grazing = threshold_events(dark, grid)
    dark_windows = intervals(dark_events, start, end, lambda t: dark(t) >= 0)
    moon_horizon = lambda t: point("moon", t)["moon_altitude_deg"]
    moon_events, moon_grazing = threshold_events(moon_horizon, grid)
    results = []
    for body in request.targets:
        above = lambda t: point(body, t)["altitude_deg"] - request.min_altitude_deg
        safe_angle = lambda t: point(body, t)["sun_separation_deg"] - 30
        above_events, above_grazing = threshold_events(above, grid)
        sun_events, sun_grazing = threshold_events(safe_angle, grid)
        events = [*dark_events, *above_events, *sun_events]
        uncertain = dark_grazing or above_grazing or sun_grazing
        moon_clear = lambda t: True
        if body != "moon" and request.min_moon_separation_deg > 0:
            separation = lambda t: (
                point(body, t)["moon_separation_deg"] - request.min_moon_separation_deg
            )
            separation_events, separation_grazing = threshold_events(separation, grid)
            events.extend([*moon_events, *separation_events])
            uncertain = uncertain or moon_grazing or separation_grazing
            moon_clear = lambda t: moon_horizon(t) <= 0 or separation(t) >= 0
        windows = intervals(
            events,
            start,
            end,
            lambda t: (
                dark(t) >= 0 and above(t) >= 0 and safe_angle(t) >= 0 and moon_clear(t)
            ),
        )
        results.append(
            {
                "id": body,
                "name": body.title(),
                "status": "unresolved_grazing"
                if uncertain
                else ("windows_found" if windows else "no_matching_window"),
                "windows": windows,
                "samples": [{"time_utc": utc(t), **point(body, t)} for t in grid],
            }
        )
    return {
        "schema_version": 1,
        "observer": {
            "lat": request.lat,
            "lon": request.lon,
            "timezone": request.timezone,
            "elevation_m": 0,
        },
        "night": {
            "date": request.date,
            "start_utc": utc(start),
            "end_utc": utc(end),
            "duration_hours": (end - start) / 3600,
        },
        "constraints": {
            "min_altitude_deg": request.min_altitude_deg,
            "sun_altitude_deg": request.sun_altitude_deg,
            "min_sun_separation_deg": 30,
            "min_moon_separation_deg": request.min_moon_separation_deg,
            "moon_separation_rule": "For targets other than Moon, apply only while Moon's geometric centre is above 0 degrees.",
        },
        "method": {
            **provider.metadata,
            "sample_minutes": SAMPLE_SECONDS / 60,
            "root_tolerance_seconds": ROOT_TOLERANCE_SECONDS,
            "window_note": "Geometric model windows, not visibility or eye-safety advice. Near-tangent crossings are marked unresolved; sub-second intervals are omitted.",
        },
        "darkness": {
            "intervals": dark_windows,
            "status": "unresolved_grazing"
            if dark_grazing
            else ("intervals_found" if dark_windows else "no_matching_interval"),
        },
        "moon": {
            **provider.lunar_phase((start + end) / 2),
            "samples": [{"time_utc": utc(t), **point("moon", t)} for t in grid],
        },
        "targets": results,
    }
