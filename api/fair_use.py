"""API key tiers for the Public Universe REST API.

Send the key in the ``X-PU-Api-Key`` request header. Cloudflare's edge exempts
a request that merely carries that header, so this process decides the tier:

* No header, or a blank header, is anonymous. The per-IP anonymous limit applies.
* An unknown or revoked key is anonymous as well. It spends the same per-IP
  anonymous budget, and it is also counted against a separate per-IP limit on
  invalid-key attempts. The HTTP response does not say the key was rejected.
* A valid key spends that key's own minute and day budget. ``X-PU-Key-Tier``
  is ``free`` or ``institution``. Every other request sends ``anonymous``.

Raw keys are never stored. The database keeps an HMAC-SHA256 digest. ``add``
prints the secret once.

Requests that arrive on the loopback interface with no forwarding headers and
no key are the co-located website. They are not counted. A forwarded client
address is counted. An invalid key is never exempt. ``OPTIONS`` is a CORS
preflight and is not counted.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, Response

HEADER_NAME = "x-pu-api-key"
TIER_HEADER = "X-PU-Key-Tier"
CORS_ALLOW_ORIGIN = "*"
CORS_ALLOW_METHODS = "GET, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type, X-PU-Api-Key"
CORS_EXPOSE_HEADERS = "X-PU-Key-Tier, Retry-After, Link"
CORS_MAX_AGE = "86400"
ANONYMOUS = "anonymous"
FREE = "free"
INSTITUTION = "institution"
KEY_TIERS = (FREE, INSTITUTION)

DEFAULT_API_ACCESS_URL = "https://publicuniverse.net/api-access"
DEFAULT_LIMITS = {
    ANONYMOUS: (60, 1000),
    FREE: (300, 10000),
    INSTITUTION: (5000, 100000),
}
DEFAULT_INVALID_PER_MINUTE = 30
_LOOPBACK = frozenset({
    ipaddress.ip_address("127.0.0.1"),
    ipaddress.ip_address("::1"),
})
_warned_proxies: set[str] = set()


_KEY_DIGEST_LABEL = b"publicuniverse-api-key-v1"


def hash_key(raw: str, pepper: str) -> str:
    """Digest a key for storage. The raw value is not recoverable.

    Issued keys are 256-bit random tokens, so a slow password KDF is the
    wrong primitive for this exact-match lookup. The token and ``pepper``
    are the HMAC-SHA256 key and a fixed label is the message: a keyed
    fingerprint, not a password hash. A copied database cannot be checked
    against a guessed token without the pepper.
    """
    mac_key = pepper.encode() + b"\0" + raw.encode()
    return hmac.digest(mac_key, _KEY_DIGEST_LABEL, "sha256").hex()


def _positive(value: str, default: int, name: str) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


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


def trusted_proxy_set(extra: str) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    found: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set(_LOOPBACK)
    for item in extra.split(","):
        entry = item.strip()
        if not entry:
            continue
        try:
            found.add(ipaddress.ip_address(entry))
        except ValueError:
            if entry not in _warned_proxies:
                _warned_proxies.add(entry)
                print(
                    "solar-system-db API: ignoring an invalid PU_TRUSTED_PROXIES entry",
                    file=sys.stderr,
                )
    return frozenset(found)


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value:
        return value
    for key, item in headers.items():
        if key.lower() == name and item:
            return item
    return ""


def _forwarded_client(
    value: str,
    trusted: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> str | None:
    """Rightmost hop that is not itself a trusted proxy."""
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
            return str(hop)
    return str(hops[0])


def client_address(
    peer_host: str | None,
    headers: Mapping[str, str],
    trusted: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> str:
    """Client identity for anonymous limits.

    Forwarding headers are read only when the TCP peer is a trusted proxy
    (loopback, plus ``PU_TRUSTED_PROXIES``). ``CF-Connecting-IP`` wins, then
    ``X-Real-IP``, then the rightmost untrusted ``X-Forwarded-For`` hop.
    An untrusted peer is keyed by its socket address so a caller cannot
    spoof a bucket.
    """
    peer = _parse_ip(peer_host or "")
    if peer is not None and peer in trusted:
        for name in ("cf-connecting-ip", "x-real-ip"):
            chosen = _parse_ip(_header(headers, name))
            if chosen is not None:
                return str(chosen)
        forwarded = _header(headers, "x-forwarded-for")
        if forwarded:
            picked = _forwarded_client(forwarded, trusted)
            if picked is not None:
                return picked
    if peer is not None:
        return str(peer)
    if peer_host:
        return peer_host.strip()[:128] or "unknown"
    return "unknown"


def direct_loopback(peer_host: str | None, headers: Mapping[str, str]) -> bool:
    """True when the socket is loopback and nobody supplied a client address."""
    peer = _parse_ip(peer_host or "")
    if peer not in _LOOPBACK:
        return False
    for name in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        if _header(headers, name).strip():
            return False
    return True


@dataclass(frozen=True)
class Decision:
    tier: str
    allowed: bool
    retry_after: int = 0


class KeyStore:
    """SQLite store of key hashes and fixed-window counters."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                tier TEXT NOT NULL CHECK (tier IN ('free', 'institution')),
                label TEXT NOT NULL DEFAULT '',
                revoked_at TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_buckets (
                bucket TEXT PRIMARY KEY,
                count INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()
        self._writes = 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add(self, key_id: str, key_hash: str, tier: str, label: str) -> None:
        if tier not in KEY_TIERS:
            raise ValueError("tier must be free or institution")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO api_keys (id, key_hash, tier, label, revoked_at, created_at)
                VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(key_hash) DO UPDATE SET
                    tier = excluded.tier,
                    label = excluded.label,
                    revoked_at = NULL
                """,
                (key_id, key_hash, tier, label[:200], now),
            )
            self._conn.commit()

    def revoke(self, key_id: str) -> bool:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, key_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def find(self, key_hash: str) -> tuple[str, str, str | None] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, tier, revoked_at FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), str(row[1]), row[2]

    def bump(self, bucket: str, expires_at: int, now: int) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                INSERT INTO usage_buckets (bucket, count, expires_at)
                VALUES (?, 1, ?)
                ON CONFLICT(bucket) DO UPDATE SET count = count + 1
                RETURNING count
                """,
                (bucket, expires_at),
            ).fetchone()
            self._writes += 1
            if self._writes % 50 == 0:
                self._conn.execute("DELETE FROM usage_buckets WHERE expires_at <= ?", (now,))
            self._conn.commit()
        return int(row[0])

    def rows(self) -> list[tuple]:
        with self._lock:
            return list(self._conn.execute(
                "SELECT id, tier, label, revoked_at, created_at FROM api_keys ORDER BY created_at"
            ).fetchall())

    def contains_text(self, needle: str) -> bool:
        """True when the raw database file contains ``needle`` (tests)."""
        if self.path == ":memory:":
            return False
        self._conn.commit()
        data = Path(self.path).read_bytes()
        for suffix in ("-wal", "-shm"):
            extra = Path(self.path + suffix)
            if extra.exists():
                data += extra.read_bytes()
        return needle.encode() in data


class FairUseGate:
    def __init__(
        self,
        store: KeyStore,
        *,
        pepper: str = "",
        anon_per_minute: int = DEFAULT_LIMITS[ANONYMOUS][0],
        anon_per_day: int = DEFAULT_LIMITS[ANONYMOUS][1],
        free_per_minute: int = DEFAULT_LIMITS[FREE][0],
        free_per_day: int = DEFAULT_LIMITS[FREE][1],
        institution_per_minute: int = DEFAULT_LIMITS[INSTITUTION][0],
        institution_per_day: int = DEFAULT_LIMITS[INSTITUTION][1],
        invalid_per_minute: int = DEFAULT_INVALID_PER_MINUTE,
        api_access_url: str = DEFAULT_API_ACCESS_URL,
        trusted_proxies: str = "",
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.pepper = pepper
        self.limits = {
            ANONYMOUS: (anon_per_minute, anon_per_day),
            FREE: (free_per_minute, free_per_day),
            INSTITUTION: (institution_per_minute, institution_per_day),
        }
        self.invalid_per_minute = invalid_per_minute
        self.api_access_url = api_access_url
        self.trusted = trusted_proxy_set(trusted_proxies)
        self._clock = clock

    @classmethod
    def from_environ(cls) -> FairUseGate:
        path = os.environ.get("PU_API_KEYS_PATH", "").strip() or str(Path("data") / "api_keys.sqlite")
        gate = cls(
            open_key_store(path),
            pepper=os.environ.get("PU_API_KEY_PEPPER", ""),
            anon_per_minute=_positive(os.environ.get("PU_ANON_PER_MINUTE", ""), 60, "PU_ANON_PER_MINUTE"),
            anon_per_day=_positive(os.environ.get("PU_ANON_PER_DAY", ""), 1000, "PU_ANON_PER_DAY"),
            free_per_minute=_positive(os.environ.get("PU_FREE_PER_MINUTE", ""), 300, "PU_FREE_PER_MINUTE"),
            free_per_day=_positive(os.environ.get("PU_FREE_PER_DAY", ""), 10000, "PU_FREE_PER_DAY"),
            institution_per_minute=_positive(
                os.environ.get("PU_INSTITUTION_PER_MINUTE", ""), 5000, "PU_INSTITUTION_PER_MINUTE"
            ),
            institution_per_day=_positive(
                os.environ.get("PU_INSTITUTION_PER_DAY", ""), 100000, "PU_INSTITUTION_PER_DAY"
            ),
            invalid_per_minute=_positive(
                os.environ.get("PU_INVALID_KEY_PER_MINUTE", ""),
                DEFAULT_INVALID_PER_MINUTE,
                "PU_INVALID_KEY_PER_MINUTE",
            ),
            api_access_url=os.environ.get("PU_API_ACCESS_URL", "").strip() or DEFAULT_API_ACCESS_URL,
            trusted_proxies=os.environ.get("PU_TRUSTED_PROXIES", ""),
        )
        raw = os.environ.get("PU_API_KEYS", "").strip()
        if raw:
            gate.load_bootstrap(raw)
        return gate

    def load_bootstrap(self, raw_json: str) -> None:
        """Upsert keys from ``PU_API_KEYS`` (a JSON list). Secrets stay in the environment."""
        try:
            payload = json.loads(raw_json)
            if not isinstance(payload, list):
                raise ValueError("expected a list")
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError("expected objects")
                secret = item.get("key")
                tier = item.get("tier")
                if not isinstance(secret, str) or not secret.strip() or tier not in KEY_TIERS:
                    raise ValueError("expected key and tier")
                label = item.get("label") if isinstance(item.get("label"), str) else ""
                key_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else secrets.token_hex(8)
                self.store.add(key_id, hash_key(secret.strip(), self.pepper), tier, label)
        except (json.JSONDecodeError, ValueError, sqlite3.Error):
            print(
                "solar-system-db API: ignoring PU_API_KEYS because it is not a list of {key, tier} objects",
                file=sys.stderr,
            )

    def issue(self, tier: str, label: str) -> tuple[str, str]:
        if tier not in KEY_TIERS:
            raise ValueError("tier must be free or institution")
        raw = "pu_" + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(8)
        self.store.add(key_id, hash_key(raw, self.pepper), tier, label)
        return key_id, raw

    def presented_key(self, headers: Mapping[str, str]) -> str | None:
        """Return a presented key, or None when the header is absent or blank.

        Query strings are ignored so keys are not written into access logs.
        """
        raw = _header(headers, HEADER_NAME)
        if not raw or not raw.strip():
            return None
        return raw.strip()[:256]

    def evaluate(self, peer_host: str | None, headers: Mapping[str, str]) -> Decision:
        now = int(self._clock())
        presented = self.presented_key(headers)
        identity = client_address(peer_host, headers, self.trusted)
        tier = ANONYMOUS
        subject = "ip:" + identity
        invalid = False

        if presented is not None:
            found = self.store.find(hash_key(presented, self.pepper))
            if found is not None and found[2] is None and found[1] in KEY_TIERS:
                tier = found[1]
                subject = "key:" + found[0]
            else:
                invalid = True

        if tier == ANONYMOUS and presented is None and direct_loopback(peer_host, headers):
            return Decision(tier=ANONYMOUS, allowed=True)

        if invalid:
            expires = (now // 60 + 1) * 60
            count = self.store.bump(f"invalid:{identity}:{now // 60}", expires, now)
            if count > self.invalid_per_minute:
                return Decision(tier=ANONYMOUS, allowed=False, retry_after=max(1, expires - now))

        per_minute, per_day = self.limits[tier]
        minute_expires = (now // 60 + 1) * 60
        day_index = now // 86400
        day_expires = (day_index + 1) * 86400
        minute_count = self.store.bump(f"m:{subject}:{now // 60}", minute_expires, now)
        day_count = self.store.bump(f"d:{subject}:{day_index}", day_expires, now)
        if minute_count > per_minute:
            return Decision(tier=tier, allowed=False, retry_after=max(1, minute_expires - now))
        if day_count > per_day:
            return Decision(tier=tier, allowed=False, retry_after=max(1, day_expires - now))
        return Decision(tier=tier, allowed=True)

    def headers_for(self, decision: Decision) -> dict[str, str]:
        headers = {
            TIER_HEADER: decision.tier,
            "Cache-Control": "no-store",
        }
        if not decision.allowed:
            headers["Retry-After"] = str(decision.retry_after)
            headers["Link"] = f'<{self.api_access_url}>; rel="help"'
        return headers

    def deny(self, decision: Decision) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    "Rate limit exceeded. Anonymous use of the Public Universe API "
                    "is limited per IP. Bulk and automated use needs an API key."
                ),
                "api_access_url": self.api_access_url,
            },
            headers=self.headers_for(decision),
        )

    def preflight(self) -> Response:
        """CORS preflight. No key is presented and nothing is counted."""
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
                "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
                "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
                "Access-Control-Expose-Headers": CORS_EXPOSE_HEADERS,
                "Access-Control-Max-Age": CORS_MAX_AGE,
            },
        )

    async def handle(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return self.preflight()
        peer = request.client.host if request.client else None
        decision = self.evaluate(peer, request.headers)
        if not decision.allowed:
            return self.deny(decision)
        response = await call_next(request)
        response.headers[TIER_HEADER] = decision.tier
        return response


def open_key_store(path: str) -> KeyStore:
    """Open ``path``, or an in-memory store when that file cannot be opened.

    The container runs as ``app`` with a root-owned ``/app`` and a read-only
    catalogue mount. A ``PermissionError`` while creating the default
    ``data/api_keys.sqlite`` must not stop the API. Keys and counters in the
    fallback store last for this process only.
    """
    try:
        return KeyStore(path)
    except (OSError, sqlite3.Error) as exc:
        print(
            "solar-system-db API: cannot open key store at "
            f"{path} ({exc}). Serving with an in-memory store.",
            file=sys.stderr,
        )
        return KeyStore(":memory:")


def attach(app, gate: FairUseGate) -> None:
    """Install the fair-use check in front of ``/api/`` routes."""

    @app.middleware("http")
    async def fair_use(request: Request, call_next):
        return await gate.handle(request, call_next)


def main(argv: list[str] | None = None) -> int:
    """Issue or revoke a key. The secret is printed once and stored only as a hash."""
    parser = argparse.ArgumentParser(description="Manage Public Universe API keys")
    parser.add_argument("--db", default=os.environ.get("PU_API_KEYS_PATH", "data/api_keys.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="create a key and print it once")
    add.add_argument("--tier", required=True, choices=KEY_TIERS)
    add.add_argument("--label", default="")
    revoke = sub.add_parser("revoke", help="revoke a key by id")
    revoke.add_argument("--id", required=True)
    sub.add_parser("list", help="list key ids without secrets")
    args = parser.parse_args(argv)
    pepper = os.environ.get("PU_API_KEY_PEPPER", "")
    try:
        store = KeyStore(args.db)
    except (OSError, sqlite3.Error) as exc:
        print(f"Cannot open key store at {args.db}: {exc}", file=sys.stderr)
        return 1
    gate = FairUseGate(store, pepper=pepper)
    if args.command == "add":
        key_id, raw = gate.issue(args.tier, args.label)
        print(f"id={key_id}")
        print(f"tier={args.tier}")
        print(raw)
        print("Store this key now. It is not saved in the database and cannot be shown again.", file=sys.stderr)
        return 0
    if args.command == "revoke":
        if not gate.store.revoke(args.id):
            print("No active key with that id.", file=sys.stderr)
            return 1
        print(f"revoked {args.id}")
        return 0
    for row in gate.store.rows():
        state = "revoked" if row[3] else "active"
        print(f"{row[0]}\t{row[1]}\t{state}\t{row[2]}\t{row[4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
