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
    assert m["schema_version"] >= 2 and m["total_objects"] > 2000 and "MIT" in m["licence"]
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


@pytest.mark.skipif(shutil.which("zstd") is None or not can_compress(), reason="zstd CLI not installed")
def test_pull_latest_rejects_traversal_and_records_only_after_restart(tmp_path):
    out = tmp_path / "out"
    dest = LocalDest(out, public_base="file://" + str(out))
    publish(_db(), dest, enrichment_store=None, keep_days=30, level=3, stamp="20260915")
    bad = json.loads((out / "latest.json").read_text())
    bad["artefact"] = "../../etc/passwd"
    (out / "evil.json").write_text(json.dumps(bad))
    data = tmp_path / "data"
    env = {**os.environ, "MANIFEST_URL": "file://" + str(out / "evil.json"), "DATA_DIR": str(data), "RESTART_CMD": "true"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh")], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and "refusing" in r.stdout
    env = {**os.environ, "MANIFEST_URL": "file://" + str(out / "latest.json"), "DATA_DIR": str(data), "RESTART_CMD": "false"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh")], env=env, capture_output=True, text=True)
    assert r.returncode != 0 and (data / "solar_system.sqlite").exists() and not (data / "latest.json").exists()


@pytest.mark.skipif(shutil.which("zstd") is None or not can_compress(), reason="zstd CLI not installed")
def test_pull_dry_run_downloads_nothing(tmp_path):
    out = tmp_path / "out"
    dest = LocalDest(out, public_base="file://" + str(out))
    publish(_db(), dest, enrichment_store=None, keep_days=30, level=3, stamp="20260915")
    data = tmp_path / "data"
    env = {**os.environ, "MANIFEST_URL": "file://" + str(out / "latest.json"), "DATA_DIR": str(data),
           "RESTART_CMD": "true"}
    r = subprocess.run(["bash", str(ROOT / "scripts" / "pull_latest.sh"), "--dry-run"], env=env,
                        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would download" in r.stdout
    assert not (data / "solar_system.sqlite").exists()


def test_s3dest_no_acl_omits_acl_from_extra_args(monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_artifact as pa

    calls = {}
    class FakeS3:
        def upload_file(self, path, bucket, key, ExtraArgs=None):
            calls["upload"] = ExtraArgs
        def put_object(self, **kw):
            calls["put"] = kw
    class FakeBoto:
        @staticmethod
        def client(name, **kw):
            calls["client_kw"] = kw
            return FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", FakeBoto)

    dest = pa.S3Dest("b", "https://acct.r2.cloudflarestorage.com", "https://download.example", acl=None, region="auto")
    dest.put(ROOT / "README.md", "x.zst", "application/zstd")
    dest.put_text("{}", "latest.json")
    assert "ACL" not in calls["upload"] and "ACL" not in calls["put"]
    assert calls["client_kw"]["region_name"] == "auto"


def test_php01_env_example_restart_cmd_is_quoted_not_executed(tmp_path):
    # The env file is sourced with `set -a; . file; set +a` on php01. If
    # RESTART_CMD's value is unquoted, sourcing it EXECUTES the shell command
    # instead of assigning it, leaving RESTART_CMD unset. Run from a throwaway
    # cwd so nothing in the file can act on a real service.
    env_file = ROOT / "deploy" / "php01" / "solar-pull.env.example"
    r = subprocess.run(
        ["bash", "-c", f'set -a; . {env_file}; set +a; printf "%s" "$RESTART_CMD"'],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == "sudo systemctl stop solar-api && sudo systemctl start solar-api"
