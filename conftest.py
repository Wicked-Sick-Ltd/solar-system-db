"""Build the offline fixture database once per session and point every test at it.

Runs before the api / mcp-server modules are imported (they create SolarDB()
and the fair-use gate at import time), so SOLAR_DB_PATH and the anonymous
budget must be set here, not in a fixture.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def pytest_configure(config):
    # Catalogue tests share one API process and one TestClient address. Give
    # that process a wide anonymous budget and its own key-store file. The
    # fair-use cases live in api/tests/test_fair_use.py.
    key_dir = Path(tempfile.mkdtemp(prefix="pu-api-keys-"))
    os.environ["PU_API_KEYS_PATH"] = str(key_dir / "keys.sqlite")
    os.environ["PU_ANON_PER_MINUTE"] = "1000000"
    os.environ["PU_ANON_PER_DAY"] = "1000000"
    os.environ["PU_INVALID_KEY_PER_MINUTE"] = "1000000"
    if os.environ.get("SOLAR_TEST_DB_READY"):
        return
    build_dir = Path(tempfile.mkdtemp(prefix="solar-test-db-"))
    db_path = build_dir / "solar_system.sqlite"
    env = {**os.environ, "SSDB_BUILD_PATH": str(db_path), "SSDB_NO_PUBLISH": "1"}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_full.py"), "--fresh", "--offline", "--no-vacuum"],
                   check=True, env=env, cwd=ROOT, stdout=subprocess.DEVNULL)
    os.environ["SOLAR_DB_PATH"] = str(db_path)
    os.environ["SOLAR_TEST_DB_READY"] = "1"
