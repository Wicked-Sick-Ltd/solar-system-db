"""Versioned logical identities, finalized only by the catalogue builder.

No request hashes catalogue rows. SQLite triggers invalidate the stored identity
after writes; a cheap schema fingerprint catches added/dropped tables or triggers.
The hash is not a signature or a promise that a historical file remains hosted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime

HASH_POLICY = "catalogue-logical-v1"
IDENTITY_TABLE = "catalogue_identity"
EXCLUDED_TABLES = {"build_meta", "enrichment_state", IDENTITY_TABLE,
                   "objects_fts", "objects_fts_data", "objects_fts_idx", "objects_fts_content", "objects_fts_docsize", "objects_fts_config"}
EXCLUDED_COLUMNS = {
    "objects": {"created_at", "updated_at"},
    "orbital_elements": {"updated_at"},
    "physical_properties": {"updated_at"},
    "visual_properties": {"updated_at"},
    "discoveries": {"updated_at"},
    "sources": {"id", "retrieved_at"},
    "rings": {"id"},
    "close_approaches": {"id"},
    "radar_observations": {"id"},
    "impact_monitoring": {"retrieved_at"},
    "exoplanets": {"retrieved_at"},
    "exoplanet_hosts": {"retrieved_at"},
}
SURROGATE_TABLES = {"sources", "rings", "close_approaches", "radar_observations"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def schema_fingerprint(conn):
    rows = [list(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
    )]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    return hashlib.sha256(canonical({"sql": rows, "user_version": version})).hexdigest()


def logical_tables(conn):
    return [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) if row[0] not in EXCLUDED_TABLES and not row[0].startswith("sqlite_")]


def typed(value):
    if value is None:
        return ["null"]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Cannot identify a catalogue containing nonfinite numbers")
        return ["real", value.hex()]
    if isinstance(value, bytes):
        return ["blob", base64.b64encode(value).decode("ascii")]
    if isinstance(value, str):
        return ["text", value]
    raise ValueError("Unsupported SQLite value type")


def logical_hash(conn):
    """Stream ordered rows; duplicate rows count, operational identifiers do not.

    Scientific keys (including meteor iau_no/ad_no and all foreign-key values)
    are retained. Refuse surrogate exclusions if a future schema references one.
    JSON text is deliberately exact text: whitespace/key-order changes alter ID.
    """
    tables = logical_tables(conn)
    for table in tables:
        for foreign in conn.execute(f"PRAGMA foreign_key_list({quote(table)})"):
            if foreign[2].lower() in SURROGATE_TABLES and (foreign[4] is None or foreign[4].lower() == "id"):
                raise ValueError("Hash policy must be revised for a referenced surrogate identifier")
    digest = hashlib.sha256()
    digest.update(canonical([HASH_POLICY]) + b"\n")
    row_counts = {}
    for table in tables:
        columns = [row for row in conn.execute(f"PRAGMA table_info({quote(table)})")
                   if row[1] not in EXCLUDED_COLUMNS.get(table, set())]
        if not columns:
            raise ValueError("Logical table has no retained columns")
        names = [row[1] for row in columns]
        digest.update(canonical(["table", table, [[r[1], r[2]] for r in columns]]) + b"\n")
        # Stable primary keys avoid wide sorts on the largest property tables.
        primary = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]
        order = primary + [name for name in names if name not in primary]
        ordering = ",".join(f"{quote(name)} COLLATE BINARY" for name in order)
        ordering += "," + ",".join(f"typeof({quote(name)})" for name in names)
        query = f"SELECT {','.join(map(quote, names))} FROM {quote(table)} ORDER BY {ordering}"
        count = 0
        for row in conn.execute(query):
            digest.update(canonical([typed(value) for value in row]) + b"\n")
            count += 1
        digest.update(canonical(["rows", count]) + b"\n")
        row_counts[table] = count
    return digest.hexdigest(), row_counts


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if parsed.tzinfo is not None else None


def _date_only(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def source_manifest(conn, inputs, builder):
    tables = set(logical_tables(conn))
    sources = []
    if "sources" in tables:
        for row in conn.execute("SELECT source_name,COUNT(*),MIN(retrieved_at),MAX(retrieved_at) FROM sources GROUP BY source_name ORDER BY source_name"):
            sources.append({"name": row[0], "scope": "object provenance", "records": row[1],
                            "recorded_retrieval_first": _timestamp(row[2]), "recorded_retrieval_last": _timestamp(row[3]), "source_version": None})
    for table in ("exoplanets", "exoplanet_hosts", "meteor_showers", "impact_monitoring"):
        if table not in tables:
            continue
        has_time = "retrieved_at" in {r[1] for r in conn.execute(f"PRAGMA table_info({quote(table)})")}
        times = "MIN(retrieved_at),MAX(retrieved_at)" if has_time else "NULL,NULL"
        for row in conn.execute(f"SELECT source,COUNT(*),{times} FROM {quote(table)} GROUP BY source ORDER BY source"):
            sources.append({"name": row[0], "scope": table, "records": row[1],
                            "recorded_retrieval_first": _timestamp(row[2]), "recorded_retrieval_last": _timestamp(row[3]), "source_version": None})
    pinned = []
    if "starter_sources" in tables:
        for source, payload in conn.execute("SELECT source,payload FROM starter_sources ORDER BY source"):
            data = json.loads(payload)
            evidence = data.get("astrometry_evidence", {})
            pinned.append({"name": source, "retrieved_at": _timestamp(data.get("retrieved_at")),
                           "snapshot_sha256": data.get("snapshot_sha256"), "upstream_sha256": data.get("upstream_sha256", {}),
                           "coordinate_evidence_sha256": evidence.get("response_sha256"),
                           "coordinate_evidence_retrieved_at": _timestamp(evidence.get("retrieved_at")),
                           "coordinate_evidence_retrieved_date": _date_only(evidence.get("retrieved_at")), "source_version": data.get("version")})
    retrieval_digest = hashlib.sha256()
    for table in sorted(tables):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({quote(table)})")]
        if "retrieved_at" not in columns:
            continue
        columns = [name for name in columns if name != "id" or table not in SURROGATE_TABLES]
        selection = ",".join(map(quote, columns))
        retrieval_digest.update(canonical([table, columns]) + b"\n")
        for row in conn.execute(f"SELECT {selection} FROM {quote(table)} ORDER BY {selection}"):
            retrieval_digest.update(canonical([typed(value) for value in row]) + b"\n")
    return {"sources": sources, "pinned_catalogues": pinned, "inputs": inputs, "builder": builder,
            "recorded_retrieval_sha256": retrieval_digest.hexdigest(),
            "retrieval_note": "Stored retrieval markers may record ingestion time for curated seeds; they are not observation epochs. Unrecorded upstream versions are null.",
            "coverage": "Recorded catalogue provenance and retained builder inputs; not every live upstream response is retained."}


def finalize(conn, *, build, inputs=None, builder=None):
    """Finalize atomically on a builder-owned connection, never a retained input."""
    if conn.in_transaction:
        raise ValueError("Commit build data before finalizing identity")
    if _timestamp(build.get("finished_at")) is None or _timestamp(build.get("started_at")) is None:
        raise ValueError("A completed UTC-aware build record is required")
    # Bulk ingestion uses journal_mode=OFF. Restore durable rollback before
    # promising an atomic metadata finalization, including exceptional exits.
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() in {"off", "memory"}:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS catalogue_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL)")
        invalidated_by = logical_tables(conn)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='build_meta'").fetchone():
            invalidated_by.append("build_meta")
        for table in invalidated_by:
            for action in ("INSERT", "UPDATE", "DELETE"):
                name = "identity_invalidate_" + hashlib.sha256(f"{table}:{action}".encode()).hexdigest()[:20]
                # This namespace is builder-owned. Do not bless an accidental
                # same-name no-op trigger into a newly finalized identity.
                conn.execute(f"DROP TRIGGER IF EXISTS {quote(name)}")
                conn.execute(f"CREATE TRIGGER {quote(name)} AFTER {action} ON {quote(table)} BEGIN DELETE FROM catalogue_identity; END")
        content_hash, counts = logical_hash(conn)
        manifest = source_manifest(conn, inputs or [], builder or {})
        schema_hash = schema_fingerprint(conn)
        build_data = {"started_at": build["started_at"], "finished_at": build["finished_at"], "mode": build["mode"]}
        build_hash = hashlib.sha256(canonical({"catalogue_id": content_hash, "build": build_data, "source_manifest": manifest, "schema_sha256": schema_hash, "row_counts": counts})).hexdigest()
        payload = {"schema_version": 1, "status": "known", "reason": None,
                   "catalogue_id": "sha256:" + content_hash, "build_identifier": "sha256:" + build_hash,
                   "hash_policy": HASH_POLICY, "schema_sha256": schema_hash, "built_at": build["finished_at"],
                   "build": build_data, "source_manifest": manifest,
                   "source_manifest_sha256": hashlib.sha256(canonical(manifest)).hexdigest(), "row_counts": counts}
        conn.execute("INSERT OR REPLACE INTO catalogue_identity VALUES (1,?)", (canonical(payload).decode(),))
    return payload


def unknown(reason):
    return {"schema_version": 1, "status": "unknown", "reason": reason, "catalogue_id": None,
            "build_identifier": None, "hash_policy": None, "schema_sha256": None, "built_at": None,
            "build": None, "source_manifest": None, "source_manifest_sha256": None, "row_counts": None}


def read_identity(conn):
    """Small metadata/schema reads only; never scan data to manufacture an ID."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalogue_identity'").fetchone():
        return unknown("not_recorded")
    try:
        row = conn.execute("SELECT payload FROM catalogue_identity WHERE singleton=1").fetchone()
        if row is None:
            return unknown("not_finalized_or_changed")
        if not isinstance(row[0], str) or len(row[0].encode()) > 1048576:
            return unknown("invalid_metadata")
        value = json.loads(row[0])
        if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("status") != "known" or value.get("hash_policy") != HASH_POLICY:
            return unknown("unsupported_metadata")
        if set(value) != set(unknown("shape")) or value.get("reason") is not None:
            return unknown("invalid_metadata")
        build = value.get("build")
        counts = value.get("row_counts")
        if (not isinstance(build, dict) or set(build) != {"started_at", "finished_at", "mode"}
                or _timestamp(build.get("started_at")) is None or _timestamp(build.get("finished_at")) is None
                or not isinstance(build.get("mode"), str) or not isinstance(value.get("source_manifest"), dict)
                or not isinstance(counts, dict) or any(type(count) is not int or count < 0 for count in counts.values())):
            return unknown("invalid_metadata")
        if any(not isinstance(value.get(key), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) for key in ("catalogue_id", "build_identifier")):
            return unknown("invalid_metadata")
        if value.get("schema_sha256") != schema_fingerprint(conn):
            return unknown("schema_changed")
        if hashlib.sha256(canonical(value.get("source_manifest"))).hexdigest() != value.get("source_manifest_sha256"):
            return unknown("invalid_metadata")
        expected = hashlib.sha256(canonical({"catalogue_id": value["catalogue_id"][7:], "build": value.get("build"), "source_manifest": value.get("source_manifest"), "schema_sha256": value["schema_sha256"], "row_counts": counts})).hexdigest()
        if value["build_identifier"] != "sha256:" + expected or value.get("built_at") != value.get("build", {}).get("finished_at"):
            return unknown("invalid_metadata")
        return value
    except (sqlite3.DatabaseError, ValueError, TypeError, AttributeError):
        return unknown("invalid_metadata")
