"""Bounded user-supplied circular horizon, not surveyed terrain."""

import bisect

from .inputs import PlanningError


def validate_mask(value):
    if value is None:
        return None
    if not isinstance(value, list) or not 2 <= len(value) <= 72:
        raise PlanningError(
            "horizon_mask must be null or 2–72 azimuth/altitude points."
        )
    points = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {"azimuth_deg", "min_altitude_deg"}:
            raise PlanningError(
                "Horizon points require exactly azimuth_deg and min_altitude_deg."
            )
        az, alt = row["azimuth_deg"], row["min_altitude_deg"]
        if (
            any(
                isinstance(v, bool) or not isinstance(v, (int, float))
                for v in (az, alt)
            )
            or not 0 <= az <= 360
            or not -90 <= alt <= 90
        ):
            raise PlanningError(
                "Horizon azimuth must be 0–360 and altitude −90–90 finite numeric degrees."
            )
        points.append((float(az % 360), float(alt)))
    points.sort()
    if len({p[0] for p in points}) != len(points):
        raise PlanningError(
            "Horizon directions must be distinct; 0 and 360 are the same direction."
        )
    return tuple(points)


def mask_json(mask):
    return (
        None
        if mask is None
        else [{"azimuth_deg": az, "min_altitude_deg": alt} for az, alt in mask]
    )


def altitude_at(mask, azimuth):
    if mask is None:
        return None
    azimuth %= 360
    index = bisect.bisect_right([p[0] for p in mask], azimuth)
    lower = mask[index - 1] if index else (mask[-1][0] - 360, mask[-1][1])
    upper = mask[index] if index < len(mask) else (mask[0][0] + 360, mask[0][1])
    fraction = (azimuth - lower[0]) / (upper[0] - lower[0])
    return lower[1] + fraction * (upper[1] - lower[1])


def knots_with_baseline(mask, baseline):
    knots = [az for az, _ in mask]
    extended = [*mask, (mask[0][0] + 360, mask[0][1])]
    for (a, x), (b, y) in zip(extended, extended[1:]):
        if (x - baseline) * (y - baseline) < 0:
            knots.append((a + (b - a) * (baseline - x) / (y - x)) % 360)
    return sorted(set(knots))
