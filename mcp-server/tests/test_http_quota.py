"""Client-key and window behaviour for the MCP observing quota."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from http_quota import ToolRateLimiter, client_address  # noqa: E402
from solar_db.http_quotas import (  # noqa: E402
    MCP_OBSERVING_TOOL_LIMITS,
    OBSERVING_DISCOVER_LIMIT,
    OBSERVING_NIGHT_LIMIT,
    parse_quota,
)


def test_shared_quota_strings_match_rest_observing_gates():
    assert OBSERVING_NIGHT_LIMIT == "10/minute"
    assert OBSERVING_DISCOVER_LIMIT == "5/minute"
    assert parse_quota(OBSERVING_NIGHT_LIMIT) == (10, 60.0)
    assert parse_quota(OBSERVING_DISCOVER_LIMIT) == (5, 60.0)
    assert MCP_OBSERVING_TOOL_LIMITS == {
        "plan_observing_night": OBSERVING_NIGHT_LIMIT,
        "discover_observing_targets": OBSERVING_DISCOVER_LIMIT,
    }


def test_forwarded_headers_apply_only_for_a_trusted_proxy():
    assert client_address("127.0.0.1", {"x-real-ip": "203.0.113.10"}) == "203.0.113.10"
    assert client_address("::1", {"X-Real-IP": "203.0.113.11"}) == "203.0.113.11"
    # Proxies append. A client-prepended address is not the key.
    assert client_address("127.0.0.1", {
        "x-forwarded-for": "198.51.100.1, 203.0.113.40",
    }) == "203.0.113.40"
    # X-Real-IP is the nginx-observed client and wins over X-Forwarded-For.
    assert client_address("127.0.0.1", {
        "x-real-ip": "203.0.113.10",
        "x-forwarded-for": "198.51.100.1, 203.0.113.99",
    }) == "203.0.113.10"
    # 127.0.0.2 is loopback-range but not the trusted proxy socket.
    spoofed = {
        "x-real-ip": "203.0.113.10",
        "x-forwarded-for": "198.51.100.50",
    }
    assert client_address("127.0.0.2", spoofed) == "127.0.0.2"
    assert client_address("203.0.113.8", spoofed) == "203.0.113.8"
    assert client_address("::ffff:127.0.0.1", {"x-real-ip": "203.0.113.12"}) == "203.0.113.12"


def test_sliding_window_is_per_tool_and_per_client():
    now = {"t": 1_000.0}

    def clock():
        return now["t"]

    limiter = ToolRateLimiter(
        {"plan_observing_night": "2/minute", "discover_observing_targets": "1/minute"},
        clock=clock,
    )
    assert limiter.refusal("plan_observing_night", "203.0.113.10") is None
    assert limiter.refusal("plan_observing_night", "203.0.113.10") is None
    blocked = limiter.refusal("plan_observing_night", "203.0.113.10")
    assert blocked is not None and blocked.startswith("Rate limit exceeded for plan_observing_night: 2/minute.")
    assert limiter.refusal("plan_observing_night", "203.0.113.11") is None
    assert limiter.refusal("discover_observing_targets", "203.0.113.10") is None
    assert limiter.refusal("get_stats", "203.0.113.10") is None
    now["t"] += 60
    assert limiter.refusal("plan_observing_night", "203.0.113.10") is None


def test_stdio_call_tool_is_not_rate_limited(monkeypatch):
    import server

    monkeypatch.setattr(
        server, "_observing_quota", ToolRateLimiter({"plan_observing_night": "1/minute"})
    )

    async def fake_super(self, name, arguments):
        return {"ran": name}

    monkeypatch.setattr(server.FastMCP, "call_tool", fake_super)

    async def call_twice():
        first = await server.mcp.call_tool("plan_observing_night", {})
        second = await server.mcp.call_tool("plan_observing_night", {})
        return first, second

    assert asyncio.run(call_twice()) == (
        {"ran": "plan_observing_night"},
        {"ran": "plan_observing_night"},
    )


def test_mcp_process_limiter_uses_the_shared_map():
    import server

    assert server._observing_quota.limits == MCP_OBSERVING_TOOL_LIMITS


def test_configured_proxy_is_trusted(monkeypatch):
    monkeypatch.setenv("MCP_TRUSTED_PROXIES", "192.0.2.10")
    assert client_address("192.0.2.10", {"x-real-ip": "203.0.113.15"}) == "203.0.113.15"
    assert client_address("192.0.2.11", {"x-real-ip": "203.0.113.15"}) == "192.0.2.11"
