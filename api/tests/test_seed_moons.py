"""The moon seed must never invent designations (they surface as empty ghost moons)."""
from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import seed_moons  # noqa: E402


def _names():
    return [row["name"] if isinstance(row, dict) else row[0] for row in seed_moons.ALL_MOONS]


def test_seed_has_no_duplicate_names():
    dupes = [n for n, c in collections.Counter(_names()).items() if c > 1]
    assert dupes == []


def test_seed_does_not_fabricate_saturn_provisionals():
    # These were auto-generated to "round out" a count and do not exist in JPL's table.
    for fake in ("S/2004 S 1", "S/2004 S 3", "S/2006 S 2", "S/2007 S 1"):
        assert fake not in _names()
