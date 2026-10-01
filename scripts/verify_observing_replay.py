"""Replay retained public fixture inputs locally; never downloads source data."""

import argparse
import json
import os
from pathlib import Path
import re
import sys

from solar_db.observing import PlanningError, plan_night
from solar_db.observing.replay import TOLERANCES, compare_retained, retained_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--provider", choices=["builtin", "jpl-de440s", "all"], default="all"
    )
    args = parser.parse_args()
    try:
        with args.fixture.open("rb") as stream:
            raw = stream.read(524289)
        if len(raw) > 524288:
            raise ValueError("oversized")
        fixture = json.loads(raw)
        if (
            not isinstance(fixture, dict)
            or type(fixture.get("schema_version")) is not int
            or fixture.get("schema_version") != 1
            or fixture.get("tolerances") != TOLERANCES
        ):
            raise ValueError("unsupported fixture")
        cases = fixture["cases"]
        if not isinstance(cases, list) or not 1 <= len(cases) <= 4:
            raise ValueError("case count")
        for case in cases:
            if (
                not isinstance(case, dict)
                or not re.fullmatch(r"[a-z0-9-]{1,80}", case["name"])
                or case["provider"] not in ("builtin", "jpl-de440s")
            ):
                raise ValueError("invalid case")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("Fixture is missing, malformed or outside the bounded replay contract.")
        return 2
    attempted, success = 0, True
    previous = os.environ.get("OBSERVING_EPHEMERIS")
    try:
        for case in cases:
            if args.provider != "all" and case["provider"] != args.provider:
                continue
            attempted += 1
            os.environ["OBSERVING_EPHEMERIS"] = case["provider"]
            try:
                plan = plan_night(**case["reference"]["request"])
                verdict = compare_retained(case["reference"], retained_result(plan))
            except (PlanningError, ValueError, KeyError, TypeError):
                verdict = {"status": "unavailable"}
            print(json.dumps({"case": case["name"], **verdict}, sort_keys=True))
            success = success and verdict["status"] == "matched"
    finally:
        if previous is None:
            os.environ.pop("OBSERVING_EPHEMERIS", None)
        else:
            os.environ["OBSERVING_EPHEMERIS"] = previous
    if not attempted:
        print("No retained case matches the requested provider.")
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
