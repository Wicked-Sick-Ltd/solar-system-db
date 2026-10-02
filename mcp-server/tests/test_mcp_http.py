"""Exercise the deploy CLI over real loopback HTTP with proxy-style headers."""
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

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_HOST = "api.sol.wickedsick.com"


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_path = tmp_path_factory.mktemp("mcp-http") / "server.log"
    env = {**os.environ, "MCP_ALLOWED_HOSTS": PUBLIC_HOST,
           "MCP_ALLOWED_ORIGINS": f"https://{PUBLIC_HOST}"}
    with log_path.open("w+") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "mcp-server/server.py"), "--transport", "http",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
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
                yield client
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


@pytest.mark.parametrize("proxy_headers", [
    {"Host": PUBLIC_HOST, "Origin": f"https://{PUBLIC_HOST}"},
    {"Host": PUBLIC_HOST},
    {},  # Ordinary loopback Host with no Origin remains supported.
])
def test_http_session_lists_and_calls_tools(http_server, proxy_headers):
    headers = {"Accept": "application/json, text/event-stream", **proxy_headers}
    response = http_server.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "php01-deployment-test", "version": "1"},
        },
    })
    initialized = payload(response)
    assert initialized["result"]["serverInfo"]["name"] == "solar-system-db"
    headers["Mcp-Session-Id"] = response.headers["mcp-session-id"]
    headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
    response = http_server.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    })
    assert response.status_code == 202
    tools = payload(http_server.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
    }))
    assert "get_stats" in {tool["name"] for tool in tools["result"]["tools"]}
    result = payload(http_server.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_stats", "arguments": {}},
    }))["result"]
    assert not result.get("isError", False)
    assert result["content"]
    assert http_server.delete("/mcp", headers=headers).status_code == 200


@pytest.mark.parametrize("headers,status", [
    ({"Host": "untrusted.example"}, 421),
    ({"Host": PUBLIC_HOST, "Origin": "https://untrusted.example"}, 403),
])
def test_untrusted_proxy_host_or_origin_rejected(http_server, headers, status):
    response = http_server.post("/mcp", headers={
        "Accept": "application/json, text/event-stream", **headers,
    }, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response.status_code == status
