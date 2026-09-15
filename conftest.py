"""Build the offline fixture database once per session and point every test at it.

Runs before the api / mcp-server modules are imported (they create SolarDB()
at import time), so SOLAR_DB_PATH must be set here, not in a fixture.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def pytest_configure(config):
    if os.environ.get("SOLAR_TEST_DB_READY"):
        return
    build_dir = Path(tempfile.mkdtemp(prefix="solar-test-db-"))
    db_path = build_dir / "solar_system.sqlite"
    env = {**os.environ, "SSDB_BUILD_PATH": str(db_path), "SSDB_NO_PUBLISH": "1"}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_full.py"), "--fresh", "--offline", "--no-vacuum"],
                   check=True, env=env, cwd=ROOT, stdout=subprocess.DEVNULL)
    os.environ["SOLAR_DB_PATH"] = str(db_path)
    os.environ["SOLAR_TEST_DB_READY"] = "1"
