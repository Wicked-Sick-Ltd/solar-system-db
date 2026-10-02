"""Measure finalization of a frozen local artifact; never finalize the input."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

from solar_db import catalogue_identity

MAX_SOURCE_BYTES = 8 * 1024**3
RUNS = 3


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE_BYTES:
        raise ValueError("Input must be a regular SQLite artifact of at most 8 GiB")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError(
                "Input must be a frozen standalone artifact without SQLite sidecars"
            )
    return {
        "sha256": sha256(path),
        "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def compatibility(conn):
    """Check current finalizer assumptions without migrations or logical scans."""
    tables = catalogue_identity.logical_tables(conn)
    if "objects" not in tables:
        raise ValueError("Input has no objects catalogue")
    required = {
        "sources": {"source_name", "retrieved_at"},
        "exoplanets": {"source"},
        "exoplanet_hosts": {"source"},
        "meteor_showers": {"source"},
        "impact_monitoring": {"source"},
        "starter_sources": {"source", "payload"},
    }
    for table in tables:
        cols = {
            row[1]
            for row in conn.execute(
                f"PRAGMA table_info({catalogue_identity.quote(table)})"
            )
        }
        if not required.get(table, set()) <= cols:
            raise ValueError(
                "Input schema is incompatible with current source-manifest columns"
            )
        for foreign in conn.execute(
            f"PRAGMA foreign_key_list({catalogue_identity.quote(table)})"
        ):
            if foreign[2].lower() in catalogue_identity.SURROGATE_TABLES and (
                foreign[4] is None or foreign[4].lower() == "id"
            ):
                raise ValueError(
                    "Input references a surrogate excluded by the hash policy"
                )
    return {
        "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "schema_sha256": catalogue_identity.schema_fingerprint(conn),
        "logical_tables": tables,
        "objects": conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
    }


def run_worker(target):
    """Internal fresh-process entry; target is a parent-created disposable copy."""
    target = Path(target)
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(target)) as conn:
        started = time.perf_counter()
        cpu_started = time.process_time()
        identity = catalogue_identity.finalize(
            conn,
            build={
                "started_at": now,
                "finished_at": now,
                "mode": "disposable-artifact-finalization-benchmark",
            },
        )
        elapsed = time.perf_counter() - started
        cpu_elapsed = time.process_time() - cpu_started
        if catalogue_identity.read_identity(conn) != identity:
            raise ValueError("Finalized identity does not verify")
    if sys.platform == "darwin":
        rss_scale = 1
    elif sys.platform.startswith("linux"):
        rss_scale = 1024
    else:
        raise ValueError("Peak RSS unit normalization supports macOS and Linux only")
    return {
        "seconds": elapsed,
        "finalize_cpu_seconds": cpu_elapsed,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * rss_scale,
        "catalogue_id": identity["catalogue_id"],
        "hash_policy": identity["hash_policy"],
        "logical_rows": identity["row_counts"],
        "output_bytes": target.stat().st_size,
    }


def child_run(target, timeout):
    env = os.environ.copy()
    # SQLite temporary sorts belong to the disposable workspace as well.
    env["TMPDIR"] = str(target.parent)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys\nfrom scripts.benchmark_catalogue_finalization import run_worker\ndata=json.dumps(run_worker(sys.argv[1]))\nif len(data)>65536: raise ValueError('Oversized report')\nprint(data)",
            str(target),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
        check=True,
    )
    if len(result.stdout) > 65536:
        raise ValueError("Child report exceeded the bounded schema")
    return json.loads(result.stdout)


def benchmark(source, scratch, timeout=1800, expected_sha256=None):
    source, scratch = (
        Path(source).resolve(strict=True),
        Path(scratch).resolve(strict=True),
    )
    if not scratch.is_dir() or not 1 <= timeout <= 3600:
        raise ValueError("Scratch directory and timeout must be valid")
    before = fingerprint(source)
    if expected_sha256 is not None and before["sha256"] != expected_sha256:
        raise ValueError("Source checksum does not match the verified artifact")
    # A snapshot and one run copy coexist; allow another source-sized temporary
    # sort/journal allowance. The filesystem is not a process-level disk quota.
    required_bytes = before["bytes"] * 3 + 256 * 1024**2
    if shutil.disk_usage(scratch).free < required_bytes:
        raise ValueError(
            "Insufficient scratch space for snapshot, run and temporary allowance"
        )
    results = []
    with tempfile.TemporaryDirectory(
        prefix="universe-finalization-", dir=scratch
    ) as work:
        snapshot = Path(work) / "readonly-snapshot.sqlite"
        # Only frozen standalone artifacts are accepted. immutable avoids
        # auxiliary-file creation; it must not be used for a live/WAL database.
        with closing(
            sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as reader:
            shape = compatibility(reader)
            backup_start = time.monotonic()

            def progress(status, remaining, total):
                if time.monotonic() - backup_start > timeout:
                    raise TimeoutError("Read-only backup exceeded time limit")

            with closing(sqlite3.connect(snapshot)) as writer:
                reader.backup(writer, pages=1024, progress=progress)
        try:
            for index in range(RUNS):
                target = Path(work) / f"run-{index + 1}.sqlite"
                shutil.copyfile(snapshot, target)
                results.append(child_run(target, timeout))
                print(
                    json.dumps(
                        {
                            "event": "run_completed",
                            "run": index + 1,
                            "seconds": results[-1]["seconds"],
                            "process_peak_rss_bytes": results[-1][
                                "process_peak_rss_bytes"
                            ],
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                target.unlink()
        finally:
            after = fingerprint(source)
            if after != before:
                raise ValueError(
                    "Source changed during benchmark; measurements are invalid"
                )
    if len({row["catalogue_id"] for row in results}) != 1 or any(
        row["logical_rows"] != results[0]["logical_rows"]
        or row["logical_rows"].get("objects") != shape["objects"]
        or set(row["logical_rows"]) != set(shape["logical_tables"])
        for row in results
    ):
        raise ValueError(
            "Independent finalizations did not retain the same logical identity"
        )
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "schema_version": 1,
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "code_revision": revision,
        "code_sha256": {
            "solar_db/catalogue_identity.py": sha256(
                root / "solar_db/catalogue_identity.py"
            ),
            "scripts/benchmark_catalogue_finalization.py": sha256(
                Path(__file__).resolve()
            ),
        },
        "source_bytes": before["bytes"],
        "source_sha256": before["sha256"],
        "source_unchanged": True,
        "source_schema": shape,
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "sqlite_version": sqlite3.sqlite_version,
        "method": "Frozen standalone source, read-only SQLite backup; three sequential disposable copies and fresh interpreters. Elapsed measures finalize only, excluding imports/copy/verification; peak RSS covers the whole child including imports/verification. OS caches are not flushed. No downloads or source writes.",
        "limits": {
            "runs": RUNS,
            "timeout_seconds_per_backup_or_child": timeout,
            "max_source_bytes": MAX_SOURCE_BYTES,
            "scratch_allowance_bytes": required_bytes,
            "scratch_allowance_is_hard_quota": False,
        },
        "runs": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--scratch-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout-seconds", default=1800, type=int)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.resolve() == args.source.resolve():
            raise ValueError("Output must be a new report, separate from the source")
        if args.expected_sha256 is not None and not re.fullmatch(
            "[0-9a-f]{64}", args.expected_sha256
        ):
            raise ValueError(
                "Expected checksum must be 64 lowercase hexadecimal characters"
            )
        report = benchmark(
            args.source, args.scratch_dir, args.timeout_seconds, args.expected_sha256
        )
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(
            json.dumps(
                {
                    "status": "measured",
                    "runs": len(report["runs"]),
                    "source_unchanged": True,
                }
            )
        )
        return 0
    except (OSError, ValueError, sqlite3.DatabaseError, subprocess.SubprocessError):
        print(
            "Benchmark unavailable: input, compatibility, resource, timeout or source-integrity check failed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
