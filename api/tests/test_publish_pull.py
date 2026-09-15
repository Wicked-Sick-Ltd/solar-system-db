import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_artifact import LocalDest, build_manifest, can_compress, prune, publish, sha256_of  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


def _db() -> Path:
    return Path(os.environ["SOLAR_DB_PATH"])


def test_manifest_reports_counts_schema_and_licence():
    m = build_manifest(_db(), artefact_name="x.zst", url="u", size_bytes=1, sha256="s")
    assert m["schema_version"] == 2 and m["total_objects"] > 2000 and "MIT" in m["licence"]
    assert m["row_counts"]["close_approaches"] > 0 and m["counts_by_type"]["planet"] == 8


@pytest.mark.skipif(not can_compress(), reason="no zstandard module and no zstd CLI")
def test_publish_to_local_dir_and_prune(tmp_path):
    dest = LocalDest(tmp_path / "out", public_base="file://" + str(tmp_path / "out"))
    m = publish(_db(), dest, enrichment_store=None, keep_days=30, level=3, stamp="20260915")
    files = {p.name for p in (tmp_path / "out").iterdir()}
    assert files >= {"solar_system-20260915.sqlite.zst", "solar_system-20260915.sqlite.zst.sha256", "solar_system-20260915.json", "latest.json"}
    latest = json.loads((tmp_path / "out" / "latest.json").read_text())
    assert latest["sha256"] == m["sha256"] == sha256_of(tmp_path / "out" / "solar_system-20260915.sqlite.zst")
    assert latest["size_bytes"] < latest["uncompressed_bytes"]
    # an old artefact gets pruned, latest.json never does
    (tmp_path / "out" / "solar_system-20200101.sqlite.zst").write_bytes(b"old")
    removed = prune(dest, 30, datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert removed == ["solar_system-20200101.sqlite.zst"] and (tmp_path / "out" / "latest.json").exists()


@pytest.mark.skipif(shutil.which("zstd") is None or not can_compress(), reason="zstd CLI not installed")
def test_pull_latest_installs_verified_artefact(tmp_path):
    out = tmp_path / "out"
    dest = LocalDest(out, public_base="file://" + str(out))
    publish(_db(), dest, enrichment_store=None, keep_days=30, level=3, stamp="20260915")
    data = tmp_path / "data"
    env = {**os.environ, "MANIFEST_URL": "file://" + str(out / "latest.json"), "DATA_DIR": str(data),
           "RESTART_CMD": "echo restarted > " + str(tmp_path / "restart.txt")}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (data / "solar_system.sqlite").exists() and (data / "latest.json").exists() and (tmp_path / "restart.txt").exists()
    assert "integrity ok" in r.stdout
    # second run is a no-op
    r2 = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh")], env=env, capture_output=True, text=True)
    assert "already at" in r2.stdout
