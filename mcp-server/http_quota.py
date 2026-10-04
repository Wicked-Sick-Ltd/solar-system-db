"""Per-client quotas for MCP Streamable HTTP and legacy SSE observing tools.

The TCP peer is the client unless that peer is a trusted proxy (loopback, or
``MCP_TRUSTED_PROXIES``). Only then are ``X-Real-IP`` and ``X-Forwarded-For``
consulted. Stdio has no HTTP request, so callers skip this limiter.
"""

from __future__ import annotations

import ipaddress
import os
import sys
import threading
import time
from collections.abc import Mapping

from solar_db.http_quotas import parse_quota

_LOOPBACK_PROXIES = (
    ipaddress.ip_address("127.0.0.1"),
    ipaddress.ip_address("::1"),
)
_warned_proxy_entries: set[str] = set()


def trusted_proxies() -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Loopback addresses nginx uses, plus any well-formed ``MCP_TRUSTED_PROXIES``."""
    found = set(_LOOPBACK_PROXIES)
    for item in os.environ.get("MCP_TRUSTED_PROXIES", "").split(","):
        entry = item.strip()
        if not entry:
            continue
        try:
            found.add(ipaddress.ip_address(entry))
        except ValueError:
            if entry not in _warned_proxy_entries:
                _warned_proxy_entries.add(entry)
                print(
                    "solar-system-db MCP: ignoring an invalid MCP_TRUSTED_PROXIES entry",
                    file=sys.stderr,
                )
    return frozenset(found)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = value.strip().strip('"')
    if not text or len(text) > 128 or "," in text or any(char.isspace() for char in text):
        return None
    if text.startswith("["):
        end = text.find("]")
        if end <= 1:
            return None
        text = text[1:end]
    elif text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            text = host
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(parsed, "ipv4_mapped", None)
    return mapped or parsed


def _header(headers: Mapping[str, str], name: str) -> str:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value:
            return value
    # Starlette lower-cases; a plain dict might not.
    for key, value in headers.items():
        if key.lower() == name and value:
            return value
    return ""


def _forwarded_client(
    value: str, trusted: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Rightmost untrusted hop. Proxies append, so the client-prepended value is ignored."""
    if len(value) > 2000:
        return None
    hops: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for part in value.split(","):
        parsed = _parse_ip(part)
        if parsed is None:
            return None
        hops.append(parsed)
    if not hops:
        return None
    for hop in reversed(hops):
        if hop not in trusted:
            return hop
    return hops[0]


def client_address(peer_host: str | None, headers: Mapping[str, str]) -> str:
    """Rate-limit key for one HTTP request.

    ``X-Real-IP`` (single IP) wins when the peer is trusted, because nginx sets
    it from ``$remote_addr`` and replaces a client-supplied value. Otherwise the
    rightmost untrusted ``X-Forwarded-For`` hop is used. An untrusted peer is
    keyed by its socket address; forwarded headers are ignored.
    """
    trusted = trusted_proxies()
    peer = _parse_ip(peer_host or "")
    if peer is not None and peer in trusted:
        real = _parse_ip(_header(headers, "x-real-ip"))
        if real is not None:
            return str(real)
        forwarded = _header(headers, "x-forwarded-for")
        if forwarded:
            chosen = _forwarded_client(forwarded, trusted)
            if chosen is not None:
                return str(chosen)
    if peer is not None:
        return str(peer)
    if peer_host:
        return peer_host.strip()[:128] or "unknown"
    return "unknown"


class ToolRateLimiter:
    """Sliding-window counter. Over-limit calls are refused before any planner work."""

    def __init__(self, limits: Mapping[str, str], clock=time.monotonic):
        self.limits = dict(limits)
        self._quotas = {name: parse_quota(spec) for name, spec in self.limits.items()}
        self._clock = clock
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()

    def refusal(self, tool: str, client: str) -> str | None:
        quota = self._quotas.get(tool)
        if quota is None:
            return None
        amount, window = quota
        now = self._clock()
        key = (tool, client)
        with self._lock:
            if len(self._hits) > 8192:
                self._hits = {
                    old_key: stamps
                    for old_key, stamps in self._hits.items()
                    if stamps and now - stamps[-1] < self._quotas[old_key[0]][1]
                }
            recent = [stamp for stamp in self._hits.get(key, ()) if now - stamp < window]
            if len(recent) >= amount:
                self._hits[key] = recent
                retry_after = max(1, int(window - (now - recent[0]) + 0.999))
                return (
                    f"Rate limit exceeded for {tool}: {self.limits[tool]}. "
                    f"Retry after {retry_after}s."
                )
            recent.append(now)
            self._hits[key] = recent
            return None
