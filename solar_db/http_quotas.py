"""Shared HTTP quota strings for REST (slowapi) and MCP HTTP.

REST route decorators and the MCP observing-tool limiter both import these
constants. Change a number here so the two surfaces stay aligned.
"""

from __future__ import annotations

# slowapi limit strings. MCP parses the same text; do not format them differently.
OBSERVING_NIGHT_LIMIT = "10/minute"
OBSERVING_DISCOVER_LIMIT = "5/minute"

# Tools that run the shared night planner. Other MCP tools are not on these quotas.
MCP_OBSERVING_TOOL_LIMITS = {
    "plan_observing_night": OBSERVING_NIGHT_LIMIT,
    "discover_observing_targets": OBSERVING_DISCOVER_LIMIT,
}

_WINDOWS = {
    "second": 1.0,
    "minute": 60.0,
    "hour": 3600.0,
}


def parse_quota(spec: str) -> tuple[int, float]:
    """Return ``(count, window_seconds)`` for a slowapi-style ``N/period`` string."""
    count_text, separator, period = spec.partition("/")
    if not separator:
        raise ValueError(f"Quota must look like '10/minute', not {spec!r}")
    try:
        count = int(count_text)
    except ValueError as exc:
        raise ValueError(f"Quota count must be an integer in {spec!r}") from exc
    window = _WINDOWS.get(period.strip().lower())
    if window is None:
        raise ValueError(f"Unsupported quota period in {spec!r}")
    if count < 1:
        raise ValueError(f"Quota must allow at least one request: {spec!r}")
    return count, window
