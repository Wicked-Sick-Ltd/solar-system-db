"""Offline comparison of retained model output; not astronomical certification."""

from copy import deepcopy
from datetime import datetime
import math
import re

TOLERANCES = {
    "angle_degrees": 1e-8,
    "distance_au": 1e-10,
    "fraction": 1e-12,
    "event_seconds": 2,
}


def retained_result(plan):
    """A small representative capture, with all events and three sample instants."""

    def samples(rows):
        return [deepcopy(rows[i]) for i in sorted({0, len(rows) // 2, len(rows) - 1})]

    constraints = plan["constraints"]
    request = {
        "date": plan["night"]["date"],
        "timezone": plan["observer"]["timezone"],
        "lat": plan["observer"]["lat"],
        "lon": plan["observer"]["lon"],
        "targets": ",".join(target["id"] for target in plan["targets"]),
        **{
            key: deepcopy(constraints[key])
            for key in (
                "min_altitude_deg",
                "sun_altitude_deg",
                "min_moon_separation_deg",
                "window_start_utc",
                "window_end_utc",
                "horizon_mask",
            )
        },
    }
    return {
        "request": request,
        "identity": {
            "method": deepcopy(plan["method"]),
            "catalogues": {
                target["id"]: deepcopy(target["catalogue"])
                for target in plan["targets"]
                if "catalogue" in target
            },
        },
        "night": deepcopy(plan["night"]),
        "results": {
            "darkness": deepcopy(plan["darkness"]),
            "moon": {
                **deepcopy(plan["moon"]),
                "samples": samples(plan["moon"]["samples"]),
            },
            "targets": [
                {
                    key: (samples(value) if key == "samples" else deepcopy(value))
                    for key, value in target.items()
                    if key != "catalogue"
                }
                for target in plan["targets"]
            ],
        },
    }


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _same(left, right):
    # bool must never compare equal to 0/1. JSON numbers otherwise share a domain.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float):
        return _finite(left) and _finite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same(v, right[k]) for k, v in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _bounded(value):
    pending, seen = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        seen += 1
        if depth > 32 or seen > 20000:
            return False
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return True


def compare_retained(expected, actual):
    """Do not compare results under a different input/model/source identity."""
    for item in (expected, actual):
        if not _bounded(item):
            return {"status": "invalid", "reason": "capture_bounds"}
        if not isinstance(item, dict) or set(item) != {
            "request",
            "identity",
            "night",
            "results",
        }:
            return {"status": "invalid", "reason": "capture_shape"}
        if any(not isinstance(item[key], dict) or not item[key] for key in item):
            return {"status": "invalid", "reason": "capture_shape"}
        method = item["identity"].get("method")
        calculation = method.get("calculation", {}) if isinstance(method, dict) else {}
        if (
            not isinstance(calculation, dict)
            or not isinstance(calculation.get("source_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", calculation["source_sha256"])
        ):
            return {"status": "invalid", "reason": "calculation_identity_missing"}
    for field in ("request", "identity", "night"):
        if not _same(expected.get(field), actual.get(field)):
            return {"status": "not_comparable", "reason": field + "_mismatch"}
    mismatches = []

    def compare(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                mismatches.append(path)
            else:
                for key in left:
                    compare(left[key], right[key], path + "." + key)
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                mismatches.append(path)
            else:
                for i, (a, b) in enumerate(zip(left, right)):
                    compare(a, b, f"{path}[{i}]")
        elif type(left) in (int, float) and type(right) in (int, float):
            tolerance = (
                TOLERANCES["angle_degrees"]
                if path.endswith("_deg")
                else TOLERANCES["distance_au"]
                if path.endswith("distance_au")
                else TOLERANCES["fraction"]
                if path.endswith("_fraction")
                else 0
            )
            if not (
                _finite(left) and _finite(right) and abs(left - right) <= tolerance
            ):
                mismatches.append(path)
        elif (
            isinstance(left, str)
            and isinstance(right, str)
            and path.endswith((".start_utc", ".end_utc"))
        ):
            try:
                if any(
                    not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)
                    for value in (left, right)
                ):
                    raise ValueError("Event instants must be exact UTC")
                delta = abs(
                    (
                        datetime.fromisoformat(left.replace("Z", "+00:00"))
                        - datetime.fromisoformat(right.replace("Z", "+00:00"))
                    ).total_seconds()
                )
                if delta > TOLERANCES["event_seconds"]:
                    mismatches.append(path)
            except (ValueError, TypeError, OverflowError):
                mismatches.append(path)
        elif not _same(left, right):
            mismatches.append(path)

    compare(expected.get("results"), actual.get("results"), "results")
    return {
        "status": "matched" if not mismatches else "different",
        "mismatches": mismatches[:20],
    }
