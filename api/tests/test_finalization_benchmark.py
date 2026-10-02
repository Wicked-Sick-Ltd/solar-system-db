"""Benchmark instrumentation runs only on disposable tiny catalogues in tests."""

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts import benchmark_catalogue_finalization as harness


def legacy(tmp_path):
    source = tmp_path / "published.sqlite"
    with sqlite3.connect(source) as conn:
        conn.executescript("""
            PRAGMA user_version=3;
            CREATE TABLE objects(id TEXT PRIMARY KEY, name TEXT);
            INSERT INTO objects VALUES('earth','Earth'),('mars','Mars');
            CREATE TABLE sources(id INTEGER PRIMARY KEY, object_id TEXT,source_name TEXT,retrieved_at TEXT);
            INSERT INTO sources VALUES(1,'earth','synthetic-test','2026-10-02T00:00:00Z');
        """)
    return source


def test_three_fresh_interpreters_finalize_only_copies_and_preserve_legacy_source(
    tmp_path,
):
    source = legacy(tmp_path)
    original = source.read_bytes()
    report = harness.benchmark(source, tmp_path, 30)
    assert source.read_bytes() == original
    assert report["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["source_unchanged"] is True
    assert report["source_schema"]["user_version"] == 3
    assert report["source_schema"]["objects"] == 2
    assert len(report["runs"]) == 3
    assert len({row["catalogue_id"] for row in report["runs"]}) == 1
    for row in report["runs"]:
        assert row["logical_rows"] == {"objects": 2, "sources": 1}
        assert row["seconds"] > 0
        assert row["process_peak_rss_bytes"] > 1024 * 1024
        assert row["output_bytes"] > 0
    assert not list(tmp_path.glob("universe-finalization-*"))
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='catalogue_identity'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("kind", ["wal", "shm", "journal", "checksum", "schema"])
def test_refuses_nonfrozen_or_incompatible_input_before_child_work(
    tmp_path, monkeypatch, kind
):
    source = legacy(tmp_path)
    if kind in ("wal", "shm", "journal"):
        Path(str(source) + "-" + kind).write_bytes(b"")
    elif kind == "schema":
        with sqlite3.connect(source) as conn:
            conn.execute("ALTER TABLE sources RENAME COLUMN source_name TO unsupported")
    monkeypatch.setattr(
        harness, "child_run", lambda *args: pytest.fail("no child work")
    )
    with pytest.raises(ValueError):
        harness.benchmark(
            source, tmp_path, 30, "0" * 64 if kind == "checksum" else None
        )
    assert not list(tmp_path.glob("universe-finalization-*"))


def test_child_timeout_and_source_change_do_not_publish_success_or_leave_copies(
    tmp_path, monkeypatch
):
    source = legacy(tmp_path)
    original = source.read_bytes()
    monkeypatch.setattr(
        harness,
        "child_run",
        lambda *args: (_ for _ in ()).throw(subprocess.TimeoutExpired("worker", 1)),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        harness.benchmark(source, tmp_path, 1)
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("universe-finalization-*"))
    real = harness.fingerprint
    calls = []

    def changed(path):
        value = real(path)
        calls.append(1)
        if len(calls) > 1:
            value["inode"] += 1
        return value

    monkeypatch.setattr(harness, "fingerprint", changed)
    with pytest.raises(ValueError, match="Source changed"):
        harness.benchmark(source, tmp_path, 1)
    assert source.read_bytes() == original


def test_cli_refuses_existing_output_even_hardlink_to_source(tmp_path):
    source = legacy(tmp_path)
    output = tmp_path / "report.json"
    output.hardlink_to(source)
    original = source.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_catalogue_finalization.py",
            str(source),
            "--scratch-dir",
            str(tmp_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert source.read_bytes() == original
    assert "Traceback" not in result.stderr
    assert not list(tmp_path.glob("universe-finalization-*"))


@pytest.mark.parametrize("counts", [{"objects": 1, "sources": 1}, {"objects": 2}])
def test_consistently_missing_rows_or_tables_cannot_pass_repeatability(
    tmp_path, monkeypatch, counts
):
    source = legacy(tmp_path)
    original = source.read_bytes()
    monkeypatch.setattr(
        harness,
        "child_run",
        lambda *args: {
            "catalogue_id": "sha256:" + "a" * 64,
            "logical_rows": counts,
            "seconds": 0.1,
            "process_peak_rss_bytes": 1024,
        },
    )
    with pytest.raises(ValueError, match="same logical identity"):
        harness.benchmark(source, tmp_path, 30)
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("universe-finalization-*"))
