"""REST observing routes must keep using the shared quota constants."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solar_db.http_quotas import OBSERVING_DISCOVER_LIMIT, OBSERVING_NIGHT_LIMIT  # noqa: E402


def test_observing_routes_use_shared_quota_constants():
    import main

    night = inspect.getsource(main.observing_night)
    night_post = inspect.getsource(main.observing_night_post)
    discover = inspect.getsource(main.observing_discover_post)
    assert "@limiter.limit(OBSERVING_NIGHT_LIMIT)" in night
    assert "@limiter.limit(OBSERVING_NIGHT_LIMIT)" in night_post
    assert "@limiter.limit(OBSERVING_DISCOVER_LIMIT)" in discover
    assert OBSERVING_NIGHT_LIMIT == "10/minute"
    assert OBSERVING_DISCOVER_LIMIT == "5/minute"
