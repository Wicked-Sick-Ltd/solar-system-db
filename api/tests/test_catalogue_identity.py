"""Logical data/build identity contracts, using disposable offline catalogues."""

import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solar_db import SolarDB
from solar_db import catalogue_identity as identity

ROOT = Path(__file__).resolve().parents[2]
BUILD = {"started_at": "2026-10-01T12:00:00Z", "finished_at": "2026-10-01T12:01:00Z", "mode": "test-offline"}


@pytest.fixture
def catalogue(tmp_path):
    path = tmp_path / "private-directory-name.sqlite"
    with sqlite3.connect(Path(os.environ["SOLAR_DB_PATH"]).as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
    return path


def finalize(path, **kwargs):
    with sqlite3.connect(path) as conn:
        return identity.finalize(conn, build=BUILD, **kwargs)


def test_repeated_offline_builds_have_same_logical_content_identity(catalogue, tmp_path):
    results = [SolarDB(catalogue).catalogue_identity()]
    for seed in (1, 2):
        target = tmp_path / f"seed-{seed}.sqlite"
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": str(seed),
               "SSDB_BUILD_PATH": str(target), "SSDB_NO_PUBLISH": "1"}
        subprocess.run([sys.executable, str(ROOT / "scripts/build_full.py"), "--offline", "--fresh"],
                       env=env, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        results.append(SolarDB(target).catalogue_identity())
        with sqlite3.connect(target) as conn:
            assert conn.execute("SELECT kind FROM designations WHERE object_id='moon-phobos' AND designation='Phobos'").fetchone() == ("name",)
    assert all(value["status"] == "known" for value in results)
    assert len({value["catalogue_id"] for value in results}) == 1
    rebuilt = results[-1]
    assert rebuilt["hash_policy"] == "catalogue-logical-v1"
    assert rebuilt["source_manifest"]["builder"]["revision"]
    assert rebuilt["source_manifest"]["builder"]["source_tree_sha256"]
    assert any(row["name"] == "tests/fixtures/cad.json" for row in rebuilt["source_manifest"]["inputs"])
    assert any(row["name"] == "openngc" and row["snapshot_sha256"] for row in rebuilt["source_manifest"]["pinned_catalogues"])


def test_logical_hash_ignores_layout_and_surrogate_insertion_ids_but_preserves_duplicates(catalogue):
    before = finalize(catalogue)["catalogue_id"]
    with sqlite3.connect(catalogue) as conn:
        rows = conn.execute("SELECT parent_id,name,inner_radius_km,outer_radius_km,width_km,thickness_km,notes FROM rings ORDER BY id DESC").fetchall()
        conn.execute("DELETE FROM rings")
        conn.executemany("INSERT INTO rings(parent_id,name,inner_radius_km,outer_radius_km,width_km,thickness_km,notes) VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.execute("VACUUM")
    assert finalize(catalogue)["catalogue_id"] == before
    with sqlite3.connect(catalogue) as conn:
        conn.execute("INSERT INTO rings(parent_id,name,inner_radius_km,outer_radius_km,width_km,thickness_km,notes) VALUES (?,?,?,?,?,?,?)", rows[0])
    assert finalize(catalogue)["catalogue_id"] != before


@pytest.mark.parametrize("sql", [
    "UPDATE objects SET name=name||' changed' WHERE id='planet-earth'",
    "DELETE FROM sources WHERE id=(SELECT MIN(id) FROM sources)",
    "INSERT INTO sources(object_id,table_name,source_name,source_url) VALUES ('planet-earth','objects','fixture','https://example.test/source')",
    "UPDATE meteor_showers SET ad_no=ad_no+10000 WHERE rowid=(SELECT MIN(rowid) FROM meteor_showers)",
    "UPDATE starter_targets SET payload=payload||' ' WHERE id=(SELECT MIN(id) FROM starter_targets)",
])
def test_scientific_or_provenance_writes_invalidate_then_change_logical_id(catalogue, sql):
    before = finalize(catalogue)
    with sqlite3.connect(catalogue) as conn:
        conn.execute(sql)
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"
    after = finalize(catalogue)
    assert after["catalogue_id"] != before["catalogue_id"]
    assert after["build_identifier"] != before["build_identifier"]


def test_retrieval_only_change_preserves_data_hash_but_changes_build_identity(catalogue):
    before = finalize(catalogue)
    with sqlite3.connect(catalogue) as conn:
        conn.execute("UPDATE sources SET retrieved_at='2026-10-01T11:00:00Z' WHERE id=(SELECT MIN(id) FROM sources)")
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"
    after = finalize(catalogue)
    assert after["catalogue_id"] == before["catalogue_id"]
    assert after["build_identifier"] != before["build_identifier"]
    assert after["source_manifest"]["recorded_retrieval_sha256"] != before["source_manifest"]["recorded_retrieval_sha256"]


def test_unknown_versions_do_not_become_fake_source_versions(catalogue):
    value = SolarDB(catalogue).catalogue_identity()
    assert all(row["source_version"] is None for row in value["source_manifest"]["sources"])
    assert "ingestion time" in value["source_manifest"]["retrieval_note"]


@pytest.mark.parametrize("change", [
    "CREATE TABLE unexpected_science (id TEXT PRIMARY KEY, value REAL)",
    "CREATE TABLE sqliteXscience (id TEXT PRIMARY KEY, value REAL)",
    "DROP TABLE radar_observations",
    "CREATE INDEX another_names_index ON objects(name)",
    "ALTER TABLE rings ADD COLUMN new_measurement REAL",
    "PRAGMA user_version=999",
])
def test_schema_changes_never_reuse_a_prior_identity(catalogue, change):
    with sqlite3.connect(catalogue) as conn:
        conn.execute(change)
    value = SolarDB(catalogue).catalogue_identity()
    assert value["status"] == "unknown" and value["reason"] == "schema_changed"


def test_new_table_is_hashed_and_write_triggers_are_installed(catalogue):
    before = SolarDB(catalogue).catalogue_identity()["catalogue_id"]
    with sqlite3.connect(catalogue) as conn:
        conn.execute("CREATE TABLE extra_science (id TEXT PRIMARY KEY, value REAL)")
    assert finalize(catalogue)["catalogue_id"] != before
    with sqlite3.connect(catalogue) as conn:
        conn.execute("INSERT INTO extra_science VALUES ('measurement',1.5)")
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"


@pytest.mark.parametrize("reference", ["rings(id)", "RINGS(ID)", "RiNgS(iD)", "RINGS"])
def test_referenced_surrogate_ids_require_a_new_hash_policy(catalogue, reference):
    with sqlite3.connect(catalogue) as conn:
        conn.execute(f"CREATE TABLE ring_measurements (ring_id INTEGER REFERENCES {reference}, value REAL)")
    with pytest.raises(ValueError, match="referenced surrogate"):
        finalize(catalogue)


def test_each_hashed_table_has_all_three_write_invalidators(catalogue):
    with sqlite3.connect(catalogue) as conn:
        for table in identity.logical_tables(conn):
            triggers = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,))]
            for action in ("INSERT", "UPDATE", "DELETE"):
                assert any(f"AFTER {action}" in trigger and "DELETE FROM catalogue_identity" in trigger for trigger in triggers)


def test_dropped_invalidator_and_new_build_record_both_invalidate(catalogue):
    with sqlite3.connect(catalogue) as conn:
        name = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone()[0]
        conn.execute(f'DROP TRIGGER "{name}"')
    assert SolarDB(catalogue).catalogue_identity()["reason"] == "schema_changed"
    finalize(catalogue)
    with sqlite3.connect(catalogue) as conn:
        conn.execute("INSERT INTO build_meta(started_at,mode) VALUES ('2026-10-01T13:00:00Z','next')")
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"


def test_failed_finalization_is_atomic(catalogue, monkeypatch):
    before = SolarDB(catalogue).catalogue_identity()
    def fail(*args):
        raise ValueError("fixture failure")
    monkeypatch.setattr(identity, "source_manifest", fail)
    with sqlite3.connect(catalogue) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        with pytest.raises(ValueError, match="fixture failure"):
            identity.finalize(conn, build=BUILD)
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert SolarDB(catalogue).catalogue_identity() == before


def test_legacy_missing_metadata_and_corrupt_metadata_are_explicit_unknown(catalogue):
    with sqlite3.connect(catalogue) as conn:
        conn.execute("UPDATE catalogue_identity SET payload='[]'")
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"
    with sqlite3.connect(catalogue) as conn:
        conn.execute("DROP TABLE catalogue_identity")
    value = SolarDB(catalogue).catalogue_identity()
    assert value["status"] == "unknown" and value["reason"] == "not_recorded"
    assert value["catalogue_id"] is None and value["built_at"] is None


def test_identity_read_does_not_hash_or_scan_catalogue_data(catalogue, monkeypatch):
    monkeypatch.setattr(identity, "logical_hash", lambda conn: pytest.fail("request attempted full logical hash"))
    queries = []
    with sqlite3.connect(catalogue) as conn:
        conn.set_trace_callback(queries.append)
        assert identity.read_identity(conn)["status"] == "known"
    assert all("sqlite_master" in query or "catalogue_identity" in query or query == "PRAGMA user_version" for query in queries)


def test_rest_and_actual_mcp_dispatcher_expose_same_identity_without_paths(catalogue, monkeypatch):
    import api.main as api
    database = SolarDB(catalogue)
    monkeypatch.setattr(api, "db", database)
    monkeypatch.setattr(api.limiter, "enabled", False)
    expected = database.catalogue_identity()
    with TestClient(api.app) as client:
        response = client.get("/api/v1/catalogue")
        assert response.status_code == 200 and response.json() == expected
        assert response.headers["cache-control"] == "no-cache"
        stats = client.get("/api/v1/stats").json()
        assert "db_path" not in stats and stats["catalogue_identity"] == expected
    spec = importlib.util.spec_from_file_location("identity_mcp", ROOT / "mcp-server/server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: database)
    assert server.get_catalogue_identity() == expected
    result = asyncio.run(server.mcp.call_tool("get_catalogue_identity", {}))
    # FastMCP's actual dispatcher returns text content plus structured data.
    encoded = json.dumps(result, default=lambda value: value.model_dump() if hasattr(value, "model_dump") else str(value))
    assert expected["catalogue_id"] in encoded
    assert "private-directory-name" not in encoded and str(ROOT) not in encoded


def test_local_publish_hashes_one_consistent_readonly_snapshot(catalogue, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_artifact as publish
    if not publish.can_compress():
        pytest.skip("zstandard module or zstd CLI required")
    before = SolarDB(catalogue).catalogue_identity()
    real_compress = publish.compress
    captured = []

    def concurrent_source_change(src, dest, level=9):
        assert src != catalogue
        captured.append(src)
        with sqlite3.connect(catalogue) as conn:
            conn.execute("UPDATE objects SET name='changed after snapshot' WHERE id='planet-earth'")
        return real_compress(src, dest, level)

    monkeypatch.setattr(publish, "compress", concurrent_source_change)
    destination = publish.LocalDest(tmp_path / "exports", public_base=None)
    manifest = publish.publish(catalogue, destination, enrichment_store=None, keep_days=30, level=1, stamp="20990101")
    compressed = destination.root / manifest["artefact"]
    uncompressed = tmp_path / "exported.sqlite"
    try:
        import zstandard
        with compressed.open("rb") as source, uncompressed.open("wb") as target:
            zstandard.ZstdDecompressor().copy_stream(source, target)
    except ImportError:
        subprocess.run(["zstd", "-d", str(compressed), "-o", str(uncompressed)], check=True, capture_output=True)
    assert manifest["sqlite_sha256"] == publish.sha256_of(uncompressed)
    assert manifest["sha256"] == publish.sha256_of(compressed)
    assert manifest["catalogue_identity"] == before == SolarDB(uncompressed).catalogue_identity()
    with sqlite3.connect(uncompressed) as conn:
        assert conn.execute("SELECT name FROM objects WHERE id='planet-earth'").fetchone() == ("Earth",)
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"
    assert all(not path.exists() for path in captured)
    assert "CC BY-SA 4.0" in manifest["licence"]
    assert "public domain" not in manifest["licence"].lower()


@pytest.mark.parametrize("mutation", ["row_count", "build_time", "extra_field"])
def test_metadata_corruption_is_not_reported_as_known(catalogue, mutation):
    value = SolarDB(catalogue).catalogue_identity()
    if mutation == "row_count":
        value["row_counts"]["objects"] += 1
    elif mutation == "build_time":
        value["build"]["started_at"] = "not a date"
    else:
        value["unexpected"] = "not part of the public contract"
    with sqlite3.connect(catalogue) as conn:
        conn.execute("UPDATE catalogue_identity SET payload=?", (json.dumps(value),))
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"


def test_failed_finalization_rolls_back_new_invalidation_triggers(catalogue, monkeypatch):
    with sqlite3.connect(catalogue) as conn:
        trigger = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' LIMIT 1").fetchone()[0]
        conn.execute(f'DROP TRIGGER "{trigger}"')
        conn.commit()
        before = identity.schema_fingerprint(conn)
        conn.execute("PRAGMA journal_mode=OFF")
        def fail(*args):
            raise ValueError("fixture failure after trigger recreation")
        monkeypatch.setattr(identity, "source_manifest", fail)
        with pytest.raises(ValueError, match="trigger recreation"):
            identity.finalize(conn, build=BUILD)
        assert identity.schema_fingerprint(conn) == before
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (trigger,)).fetchone() is None


def test_finalization_repairs_preexisting_same_name_noop_invalidator(catalogue):
    import hashlib
    trigger = "identity_invalidate_" + hashlib.sha256(b"objects:UPDATE").hexdigest()[:20]
    with sqlite3.connect(catalogue) as conn:
        conn.execute(f'DROP TRIGGER "{trigger}"')
        conn.execute(f'CREATE TRIGGER "{trigger}" AFTER UPDATE ON objects BEGIN SELECT 1; END')
    finalize(catalogue)
    with sqlite3.connect(catalogue) as conn:
        conn.execute("UPDATE objects SET name='Changed Earth' WHERE id='planet-earth'")
    assert SolarDB(catalogue).catalogue_identity()["status"] == "unknown"


def test_coordinate_evidence_retains_date_precision_without_inventing_midnight(catalogue):
    pinned = SolarDB(catalogue).catalogue_identity()["source_manifest"]["pinned_catalogues"]
    bsc = next(row for row in pinned if row["name"] == "bsc5p")
    assert bsc["coordinate_evidence_retrieved_date"] == "2026-10-01"
    assert bsc["coordinate_evidence_retrieved_at"] is None
