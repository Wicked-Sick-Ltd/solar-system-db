"""scripts/verify.py must build the offline fixture and verify it when the
catalogue file is missing — this is CI's independent "Verify DB" step
(.github/workflows/test.yml), which runs on a fresh clone with no committed
DB and is not exercised by conftest.py's session-wide fixture."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_verify_builds_offline_fixture_when_db_missing(tmp_path):
    missing_db = tmp_path / "does-not-exist" / "solar_system.sqlite"
    env = {**os.environ, "SSDB_BUILD_PATH": str(missing_db)}
    env.pop("SOLAR_TEST_DB_READY", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert "does not exist" in result.stdout
    assert "building the offline fixture catalogue" in result.stdout
    assert "VERIFY OK" in result.stdout
    assert result.returncode == 0
