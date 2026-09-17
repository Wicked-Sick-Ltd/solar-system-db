"""Shared helpers for the populate / update / verify scripts."""
from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "schema.sql"
# DB lives at ROOT/data/solar_system.sqlite. Builds that need to write a lot
# can opt into using SSDB_BUILD_PATH to point at a fast local path (e.g. /tmp)
# and the publish step copies the finished file into ROOT/data.
DB_PATH = Path(os.environ.get(
    "SSDB_BUILD_PATH",
    str(ROOT / "data" / "solar_system.sqlite"),
))
PUBLISH_PATH = ROOT / "data" / "solar_system.sqlite"

HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
SBDB_QUERY_URL = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
SBDB_LOOKUP_URL = "https://ssd-api.jpl.nasa.gov/sbdb.api"

USER_AGENT = "solar-system-db/0.1 (+https://github.com/wizzouk2/solar-system-db)"

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def connect(create: bool = False) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    if create:
        with SCHEMA_PATH.open() as f:
            conn.executescript(f.read())
    return conn


def publish() -> Path:
    """Copy the working DB to its published location (a no-op if already in place).

    SSDB_NO_PUBLISH=1 leaves the build where it is (tests, scratch builds)."""
    if DB_PATH.resolve() == PUBLISH_PATH.resolve() or os.environ.get("SSDB_NO_PUBLISH") == "1":
        return DB_PATH
    PUBLISH_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DB_PATH, PUBLISH_PATH)
    return PUBLISH_PATH


def upsert_object(conn, *, id: str, name: str, object_type: str,
                  designation: str | None = None, parent_id: str | None = None,
                  discoverer: str | None = None, discovery_date: str | None = None,
                  wikipedia_url: str | None = None, notes: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO objects (id, name, designation, object_type, parent_id,
                             discoverer, discovery_date, wikipedia_url, notes)
        VALUES (:id, :name, :designation, :object_type, :parent_id,
                :discoverer, :discovery_date, :wikipedia_url, :notes)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            designation = COALESCE(excluded.designation, objects.designation),
            object_type = excluded.object_type,
            parent_id = COALESCE(excluded.parent_id, objects.parent_id),
            discoverer = COALESCE(excluded.discoverer, objects.discoverer),
            discovery_date = COALESCE(excluded.discovery_date, objects.discovery_date),
            wikipedia_url = COALESCE(excluded.wikipedia_url, objects.wikipedia_url),
            notes = COALESCE(excluded.notes, objects.notes),
            updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
        """,
        dict(id=id, name=name, designation=designation, object_type=object_type,
             parent_id=parent_id, discoverer=discoverer, discovery_date=discovery_date,
             wikipedia_url=wikipedia_url, notes=notes),
    )


def upsert_row(conn, table: str, object_id: str, fields: dict[str, Any]) -> None:
    """Generic upsert keyed on object_id for the *_properties / orbital_elements tables."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        # nothing to write — ensure a row exists so FK joins work
        conn.execute(
            f"INSERT OR IGNORE INTO {table} (object_id) VALUES (?)", (object_id,)
        )
        return
    fields["object_id"] = object_id
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    updates = ", ".join(f"{k} = COALESCE(excluded.{k}, {table}.{k})" for k in fields if k != "object_id")
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(object_id) DO UPDATE SET {updates}, "
        f"updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')"
    )
    conn.execute(sql, fields)


def upsert_row_fill(conn, table: str, object_id: str, fields: dict[str, Any]) -> None:
    """Like upsert_row, but existing non-null values win: bulk sources may only
    fill gaps in curated rows, never overwrite fact-sheet values."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        conn.execute(f"INSERT OR IGNORE INTO {table} (object_id) VALUES (?)", (object_id,))
        return
    fields["object_id"] = object_id
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    updates = ", ".join(f"{k} = COALESCE({table}.{k}, excluded.{k})" for k in fields if k != "object_id")
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(object_id) DO UPDATE SET {updates}, "
        f"updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')",
        fields,
    )


def add_classification(conn, object_id: str, label: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO classifications (object_id, label) VALUES (?, ?)",
        (object_id, label),
    )


def add_source(conn, *, object_id: str | None, table_name: str,
               source_name: str, source_url: str | None = None,
               field_name: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO sources (object_id, table_name, field_name, source_name, source_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (object_id, table_name, field_name, source_name, source_url),
    )


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------
def fetch_json(url: str, params: dict | None = None, retries: int = 3, timeout: int = 30) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"fetch_json failed for {url}: {last_err}")


def fetch_text(url: str, params: dict | None = None, retries: int = 3, timeout: int = 30,
               encoding: str = "utf-8") -> str:
    """Text fetch with the same retry/429 handling as fetch_bytes (a thin
    decode on top of it, rather than a second copy of the retry loop).
    Decodes leniently (errors="replace") since upstream pages occasionally
    carry a stray non-UTF-8 byte — see ingest_showers's module docstring for
    a concrete example."""
    return fetch_bytes(url, params=params, retries=retries, timeout=timeout).decode(encoding, errors="replace")


def fetch_bytes(url: str, params: dict | None = None, retries: int = 3, timeout: int = 30) -> bytes:
    last_err: Exception | None = None
    last_status: int | None = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                last_status = 429
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last_err = e
            last_status = getattr(getattr(e, "response", None), "status_code", None)
            time.sleep(1 + attempt)
    detail = f"last status {last_status}" if last_status is not None else str(last_err)
    raise RuntimeError(f"fetch_bytes gave up after {retries} attempts ({detail}) for {url}")


# ---------------------------------------------------------------------------
# Slug / ID helpers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def asteroid_id(spkid_or_num: Any, name: str | None = None) -> str:
    if name:
        return f"ast-{spkid_or_num}-{slugify(name)}"
    return f"ast-{spkid_or_num}"


def comet_id(designation: str) -> str:
    return f"comet-{slugify(designation)}"


def tno_id(name_or_design: str) -> str:
    return f"tno-{slugify(name_or_design)}"
