"""One-page identity association under real SQLite read transaction isolation."""

import asyncio
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from solar_db import SolarDB
from solar_db.catalogue_identity import finalize
from solar_db import response_snapshot
from solar_db.response_snapshot import CatalogueReadError

ROOT = Path(__file__).resolve().parents[2]
BUILD = {
    "started_at": "2026-10-02T00:00:00Z",
    "finished_at": "2026-10-02T00:01:00Z",
    "mode": "test-offline",
}


def copy_database(source, target):
    with closing(
        sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    ) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)


@pytest.fixture
def catalogue(tmp_path):
    path = tmp_path / "private-catalogue.sqlite"
    copy_database(os.environ["SOLAR_DB_PATH"], path)
    return path


def test_page_counts_and_source_rows_carry_compact_known_identity_without_full_manifest(
    catalogue, monkeypatch
):
    from solar_db import catalogue_identity

    monkeypatch.setattr(
        catalogue_identity,
        "logical_hash",
        lambda _: pytest.fail("No per-request full hash"),
    )
    db = SolarDB(catalogue)
    for q in (None, "TRAPPIST", "missing exact name"):
        result = db.list_exoplanets(q=q, limit=2)
        snapshot = result["catalogue_snapshot"]
        assert set(snapshot) == {
            "schema_version",
            "association",
            "status",
            "reason",
            "catalogue_id",
            "build_identifier",
            "hash_policy",
        }
        assert snapshot["association"] == "same-read-transaction"
        assert snapshot["schema_version"] == 1 and snapshot["status"] == "known"
        assert (
            snapshot["reason"] is None
            and snapshot["hash_policy"] == "catalogue-logical-v1"
        )
        assert snapshot["catalogue_id"] == db.catalogue_identity()["catalogue_id"]
        assert len(result["results"]) <= 2
        assert result["has_more"] == (len(result["results"]) < result["total"])
        assert "source_manifest" not in snapshot
    assert db.list_exoplanets(q="missing exact name")["results"] == []


def test_read_connection_is_readonly_closes_on_exception_and_escapes_uri_names(
    catalogue, tmp_path
):
    path = tmp_path / "data?private#name.sqlite"
    copy_database(catalogue, path)
    db = SolarDB(path)
    assert db.list_exoplanets()["catalogue_snapshot"]["status"] == "known"
    with pytest.raises(CatalogueReadError, match="temporarily unavailable"):
        with db._snapshot_conn() as conn:
            conn.execute("DELETE FROM exoplanets")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")
    assert db.list_exoplanets()["total"] == 11


def test_wal_commit_between_identity_and_rows_stays_on_old_snapshot_then_reports_invalidation(
    catalogue, monkeypatch
):
    with closing(sqlite3.connect(catalogue)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db = SolarDB(catalogue)
    before = db.list_exoplanets()
    real_identity = response_snapshot.read_identity
    called = False

    def commit_after_identity(conn):
        nonlocal called
        metadata = real_identity(conn)
        assert conn.in_transaction
        if not called:
            called = True
            with closing(sqlite3.connect(catalogue)) as writer:
                writer.execute(
                    "DELETE FROM exoplanets WHERE id=(SELECT id FROM exoplanets ORDER BY name LIMIT 1)"
                )
                writer.commit()
        return metadata

    monkeypatch.setattr(response_snapshot, "read_identity", commit_after_identity)
    during = db.list_exoplanets()
    assert during == before
    after = db.list_exoplanets()
    assert after["total"] == before["total"] - 1
    assert after["catalogue_snapshot"]["status"] == "unknown"
    assert after["catalogue_snapshot"]["reason"] == "not_finalized_or_changed"
    assert after["catalogue_snapshot"]["catalogue_id"] is None


def test_atomic_catalogue_replacement_between_metadata_and_rows_cannot_mix_builds(
    catalogue, tmp_path, monkeypatch
):
    replacement = tmp_path / "next.sqlite"
    copy_database(catalogue, replacement)
    with closing(sqlite3.connect(replacement)) as conn:
        conn.execute(
            "DELETE FROM exoplanets WHERE id=(SELECT id FROM exoplanets ORDER BY name LIMIT 1)"
        )
        conn.commit()
        next_identity = finalize(conn, build=BUILD)
    db = SolarDB(catalogue)
    before = db.list_exoplanets()
    real_identity = response_snapshot.read_identity
    swapped = False

    def swap_after_identity(conn):
        nonlocal swapped
        value = real_identity(conn)
        if not swapped:
            swapped = True
            os.replace(replacement, catalogue)
        return value

    monkeypatch.setattr(response_snapshot, "read_identity", swap_after_identity)
    assert db.list_exoplanets() == before
    after = db.list_exoplanets()
    assert after["total"] == before["total"] - 1
    assert after["catalogue_snapshot"]["catalogue_id"] == next_identity["catalogue_id"]
    assert (
        after["catalogue_snapshot"]["catalogue_id"]
        != before["catalogue_snapshot"]["catalogue_id"]
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("DROP TABLE catalogue_identity", "not_recorded"),
        ("UPDATE catalogue_identity SET payload='{}'", "unsupported_metadata"),
        ("CREATE TABLE new_science(value INTEGER)", "schema_changed"),
    ],
)
def test_unknown_identity_is_not_manufactured_for_legacy_or_changed_sources(
    catalogue, change, reason
):
    with closing(sqlite3.connect(catalogue)) as conn:
        conn.execute(change)
        conn.commit()
    result = SolarDB(catalogue).list_exoplanets()
    assert result["available"] is True and result["total"] == 11
    snapshot = result["catalogue_snapshot"]
    assert snapshot["status"] == "unknown" and snapshot["reason"] == reason
    assert (
        snapshot["catalogue_id"] is None
        and snapshot["build_identifier"] is None
        and snapshot["hash_policy"] is None
    )


def test_unavailable_table_is_not_a_genuine_empty_catalogue(catalogue):
    with closing(sqlite3.connect(catalogue)) as conn:
        conn.execute("DROP TABLE exoplanets")
        conn.commit()
    result = SolarDB(catalogue).list_exoplanets()
    assert result["available"] is False and result["results"] == []
    assert result["catalogue_snapshot"]["status"] == "unknown"


def test_rest_and_actual_mcp_dispatcher_expose_identical_page_association(
    catalogue, monkeypatch
):
    import api.main as api

    db = SolarDB(catalogue)
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    expected = db.list_exoplanets(q="TRAPPIST", limit=2)
    with TestClient(api.app) as client:
        result = client.get("/api/v1/exoplanets", params={"q": "TRAPPIST", "limit": 2})
        assert result.status_code == 200 and result.json() == expected
    spec = importlib.util.spec_from_file_location(
        "snapshot_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: db)
    result = asyncio.run(
        server.mcp.call_tool("list_exoplanets", {"q": "TRAPPIST", "limit": 2})
    )
    # Dictionary-returning FastMCP tools retain structured output as well as text.
    content = result[0] if isinstance(result, tuple) else result
    assert json.loads(content[0].text) == expected


def test_locked_and_corrupt_reads_fail_unavailable_without_sql_or_paths(
    catalogue, monkeypatch
):
    import api.main as api

    db = SolarDB(catalogue)
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    with closing(sqlite3.connect(catalogue)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        with TestClient(api.app) as client:
            result = client.get("/api/v1/exoplanets")
        assert result.status_code == 503
        assert result.headers["cache-control"] == "no-store"
        assert result.json() == {
            "detail": "Exoplanet catalogue is temporarily unavailable."
        }
        writer.rollback()
    assert db.list_exoplanets()["available"] is True
    catalogue.write_bytes(b"not SQLite data /private/path")
    with pytest.raises(CatalogueReadError) as error:
        db.list_exoplanets()
    assert "private" not in str(error.value)


def test_unreadable_source_json_and_actual_mcp_errors_never_expose_private_values(
    catalogue, monkeypatch
):
    import api.main as api

    db = SolarDB(catalogue)
    with closing(sqlite3.connect(catalogue)) as conn:
        conn.execute("UPDATE exoplanets SET source_data='/private/invalid-source-data'")
        conn.commit()
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    with TestClient(api.app) as client:
        result = client.get("/api/v1/exoplanets")
    assert result.status_code == 503 and "private" not in result.text
    spec = importlib.util.spec_from_file_location(
        "snapshot_error_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: db)
    with pytest.raises(Exception) as error:
        asyncio.run(server.mcp.call_tool("list_exoplanets", {}))
    assert "temporarily unavailable" in str(error.value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("starts_with_catalogue", [False, True])
def test_page_capability_does_not_reuse_process_schema_cache_after_replacement(
    catalogue, tmp_path, starts_with_catalogue
):
    legacy = tmp_path / "old-empty.sqlite"
    with closing(sqlite3.connect(legacy)) as conn:
        conn.execute("CREATE TABLE legacy_marker(value TEXT)")
    active, replacement = (
        (catalogue, legacy) if starts_with_catalogue else (legacy, catalogue)
    )
    db = SolarDB(active)
    db.get_exoplanet("missing name")  # Prime the legacy API's process schema cache.
    os.replace(replacement, active)
    result = db.list_exoplanets()
    assert result["available"] is (not starts_with_catalogue)
    assert result["total"] == (0 if starts_with_catalogue else 11)
    assert result["catalogue_snapshot"]["status"] == (
        "unknown" if starts_with_catalogue else "known"
    )
