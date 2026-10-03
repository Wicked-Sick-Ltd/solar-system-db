"""HTTP quotas for observing tools, exercised through the real MCP CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from solar_db.http_quotas import (
    OBSERVING_DISCOVER_LIMIT,
    OBSERVING_NIGHT_LIMIT,
    parse_quota,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_HOST = "api.sol.wickedsick.com"
NIGHT_COUNT, _ = parse_quota(OBSERVING_NIGHT_LIMIT)
DISCOVER_COUNT, _ = parse_quota(OBSERVING_DISCOVER_LIMIT)


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        port = listener.getsockname()[1]
    log_path = tmp_path_factory.mktemp("mcp-http-quota") / "server.log"
    env = {**os.environ, "MCP_ALLOWED_HOSTS": PUBLIC_HOST,
           "MCP_ALLOWED_ORIGINS": f"https://{PUBLIC_HOST}"}
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp-server/server.py"), "--transport", "http",
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    try:
                        client.get("/mcp")
                        break
                    except httpx.ConnectError:
                        if process.poll() is not None:
                            pytest.fail(log_path.read_text())
                        time.sleep(0.05)
                else:
                    pytest.fail("MCP HTTP startup timed out: " + log_path.read_text())
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def payload(response):
    response.raise_for_status()
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return json.loads(next(line[6:] for line in response.text.splitlines()
                               if line.startswith("data: ")))
    return response.json()


def untrusted_connect_host():
    """An address of this host that is not a trusted proxy.

    Connecting to 127.0.0.2 still presents the peer as 127.0.0.1 on Linux, so
    that alias cannot prove an untrusted socket. A UDP connect selects the
    routed local address without sending a packet.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        host = probe.getsockname()[0]
    if host.startswith("127."):
        pytest.skip("this host has no non-loopback IPv4 address")
    return host


def open_session(port, connect_host, extra_headers=None):
    client = httpx.Client(base_url=f"http://{connect_host}:{port}", timeout=5)
    headers = {
        "Accept": "application/json, text/event-stream",
        # Keep the allowlisted Host. The socket peer is still connect_host.
        "Host": f"127.0.0.1:{port}",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "quota-test", "version": "1"},
        },
    })
    initialized = payload(response)
    headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
    headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
    noted = client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })
    assert noted.status_code == 202
    return client, headers


def tool_result(client, headers, name, arguments, request_id):
    started = time.monotonic()
    body = payload(client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }))
    elapsed = time.monotonic() - started
    result = body["result"]
    text = "\n".join(block.get("text", "") for block in result.get("content", []))
    return bool(result.get("isError", False)), text, elapsed


def test_observing_tools_are_limited_per_client_and_reads_are_not(http_server):
    port = http_server
    client, headers = open_session(port, "127.0.0.1")
    try:
        night_headers = {**headers, "X-Real-IP": "203.0.113.10"}
        other_headers = {**headers, "X-Real-IP": "203.0.113.11"}
        for index in range(NIGHT_COUNT):
            is_error, text, _elapsed = tool_result(
                client, night_headers, "plan_observing_night", {}, index + 10,
            )
            assert is_error
            assert "Rate limit exceeded" not in text
        is_error, text, elapsed = tool_result(
            client, night_headers, "plan_observing_night",
            {"date": "2026-10-03", "timezone": "UTC", "lat": 51.5, "lon": -0.12},
            100,
        )
        assert is_error and text.startswith("Rate limit exceeded for plan_observing_night: 10/minute.")
        assert elapsed < 2
        is_error, text, _elapsed = tool_result(
            client, other_headers, "plan_observing_night", {}, 101,
        )
        assert is_error and "Rate limit exceeded" not in text

        discover_headers = {**headers, "X-Real-IP": "203.0.113.12"}
        for index in range(DISCOVER_COUNT):
            is_error, text, _elapsed = tool_result(
                client, discover_headers, "discover_observing_targets", {}, 200 + index,
            )
            assert is_error and "Rate limit exceeded" not in text
        is_error, text, elapsed = tool_result(
            client, discover_headers, "discover_observing_targets", {}, 250,
        )
        assert is_error and text.startswith(
            "Rate limit exceeded for discover_observing_targets: 5/minute."
        )
        assert elapsed < 2
        # Discovery and night planning do not share a bucket.
        is_error, text, _elapsed = tool_result(
            client, discover_headers, "plan_observing_night", {}, 251,
        )
        assert "Rate limit exceeded" not in text

        for index in range(NIGHT_COUNT + 2):
            is_error, text, _elapsed = tool_result(
                client, night_headers, "get_stats", {}, 300 + index,
            )
            assert not is_error, text
            assert "total_objects" in text
    finally:
        client.close()


def test_spoofed_forwarded_headers_from_an_untrusted_peer_do_not_bypass(http_server):
    port = http_server
    untrusted, untrusted_headers = open_session(port, untrusted_connect_host())
    trusted, trusted_headers = open_session(port, "127.0.0.1")
    try:
        spoofed = {
            **untrusted_headers,
            "X-Real-IP": "198.51.100.20",
            "X-Forwarded-For": "198.51.100.20",
        }
        for index in range(NIGHT_COUNT):
            is_error, text, _elapsed = tool_result(
                untrusted, spoofed, "plan_observing_night", {}, 400 + index,
            )
            assert is_error and "Rate limit exceeded" not in text
        moved = {
            **untrusted_headers,
            "X-Real-IP": "198.51.100.21",
            "X-Forwarded-For": "203.0.113.77, 198.51.100.21",
        }
        is_error, text, elapsed = tool_result(
            untrusted, moved, "plan_observing_night", {}, 490,
        )
        assert is_error and text.startswith("Rate limit exceeded for plan_observing_night:")
        assert elapsed < 2
        # The spoofed address never became its own bucket.
        fresh = {**trusted_headers, "X-Real-IP": "198.51.100.20"}
        is_error, text, _elapsed = tool_result(
            trusted, fresh, "plan_observing_night", {}, 491,
        )
        assert "Rate limit exceeded" not in text
    finally:
        untrusted.close()
        trusted.close()


def test_trusted_proxy_uses_the_appended_forwarded_hop(http_server):
    port = http_server
    client, headers = open_session(port, "127.0.0.1")
    try:
        primary = {**headers, "X-Forwarded-For": "198.51.100.1, 203.0.113.70"}
        for index in range(NIGHT_COUNT):
            is_error, text, _elapsed = tool_result(
                client, primary, "plan_observing_night", {}, 500 + index,
            )
            assert "Rate limit exceeded" not in text
        is_error, text, _elapsed = tool_result(
            client, primary, "plan_observing_night", {}, 580,
        )
        assert is_error and "Rate limit exceeded for plan_observing_night:" in text
        swapped = {**headers, "X-Forwarded-For": "203.0.113.70, 198.51.100.1"}
        is_error, text, _elapsed = tool_result(
            client, swapped, "plan_observing_night", {}, 581,
        )
        assert "Rate limit exceeded" not in text
    finally:
        client.close()
