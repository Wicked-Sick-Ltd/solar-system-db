"""Profiling cannot write to its source catalogue, including report-path mistakes."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "query_profiler", ROOT / "scripts/profile_read_queries.py"
)
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


def test_synthetic_copy_preserves_input_and_original_aliases(tmp_path):
    source = Path(os.environ["SOLAR_DB_PATH"])
    original_hash = profiler.digest(source)
    target = tmp_path / "copy.sqlite"
    profiler.synthetic_copy(source, target, 100, 10)
    assert profiler.digest(source) == original_hash
    with (
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as before,
        sqlite3.connect(target) as after,
    ):
        for table, delta in [
            ("objects", 100),
            ("exoplanet_hosts", 10),
            ("exoplanets", 20),
        ]:
            assert (
                after.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                == before.execute(f"SELECT count(*) FROM {table}").fetchone()[0] + delta
            )
        aliases = before.execute(
            "SELECT id,alt FROM objects_fts ORDER BY id"
        ).fetchall()
        assert (
            aliases
            == after.execute(
                "SELECT id,alt FROM objects_fts WHERE id NOT LIKE 'perf-object-%' ORDER BY id"
            ).fetchall()
        )
    report = profiler.run(target, repeats=3, objects=100)
    assert len(report["cases"]) == 13
    assert report["cases"]["objects_keyset_first"]["connections"] == 1
    assert report["cases"]["objects_keyset_first"]["result_rows"] == 50
    # Synthetic writes invalidate identity: metadata existence/payload, current
    # table capability, rows and count all remain on one read transaction.
    assert report["cases"]["exoplanets_filtered_total"]["select_statements"] == 5
    assert report["cases"]["exoplanets_filtered_total"]["connections"] == 1


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_cli_refuses_to_overwrite_input_with_report(tmp_path, alias):
    source = tmp_path / "source.sqlite"
    source.write_bytes(b"Unchanged input sentinel")
    output = source
    if alias != "same":
        output = tmp_path / "report.json"
        if alias == "symlink":
            output.symlink_to(source)
        else:
            os.link(source, output)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/profile_read_queries.py"),
            "--database",
            str(source),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert result.returncode == 2
    assert "must not overwrite" in result.stderr
    assert source.read_bytes() == b"Unchanged input sentinel"
