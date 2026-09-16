"""Publish a built catalogue: compress, checksum, manifest, upload, prune.

    python scripts/publish_artifact.py --db /data/solar_system.sqlite --bucket solar-system-db \
        --endpoint https://s3.wickedsick.com --public-base https://s3.wickedsick.com/solar-system-db \
        [--enrichment-store /data/enrichment.sqlite] [--keep-days 30]
    python scripts/publish_artifact.py --db build.sqlite --dest ./out     # local directory (tests, dry runs)

Artefacts: solar_system-YYYYMMDD.sqlite.zst, .sha256, latest.json (+ a dated
copy of the manifest). Credentials come from the environment (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY, injected via `op run`), never from files in the repo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

LICENCE = ("Compilation: MIT (Wicked Sick Ltd). Underlying data: NASA/JPL (public domain), "
           "IAU Minor Planet Center (free use with attribution), CDS VI/42 (public domain). "
           "Please credit the sources; see /api/v1/sources.")
SCHEMA_DOC = "https://github.com/Wicked-Sick-Ltd/solar-system-db/blob/main/schema/schema.sql"


def compress(src: Path, dst: Path, level: int = 9) -> int:
    """zstd-compress src → dst using the zstandard module, or the zstd CLI if the
    module is not installed (CI, ad-hoc boxes)."""
    try:
        import zstandard  # optional extra [publish]
    except ImportError:
        zstandard = None
    if zstandard is not None:
        cctx = zstandard.ZstdCompressor(level=level, threads=-1)
        with src.open("rb") as fin, dst.open("wb") as fout:
            cctx.copy_stream(fin, fout, size=src.stat().st_size)
    elif shutil.which("zstd"):
        import subprocess
        subprocess.run(["zstd", "-q", "-f", f"-{level}", "-T0", "-o", str(dst), str(src)], check=True)
    else:
        raise RuntimeError("Neither the zstandard module nor the zstd CLI is available.")
    return dst.stat().st_size


def can_compress() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return shutil.which("zstd") is not None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(db_path: Path, *, artefact_name: str, url: str, size_bytes: int, sha256: str,
                   enrichment_store: Path | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts = {r["object_type"]: r["n"] for r in conn.execute("SELECT * FROM v_object_counts")}
    meta = conn.execute("SELECT * FROM build_meta ORDER BY id DESC LIMIT 1").fetchone()
    schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
    # Row counts per table. A v1 file lacks the v2 tables; report those as absent
    # rather than failing the publish, but let any other SQLite error surface.
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tables = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("objects", "orbital_elements", "close_approaches", "discoveries", "designations", "atmospheres", "rings")
        if t in present
    }
    coverage: dict[str, Any] | None = None
    if enrichment_store and Path(enrichment_store).exists():
        from enrich_crawler import coverage as _cov, open_store
        coverage = _cov(open_store(enrichment_store))
    return {
        "artefact": artefact_name,
        "url": url,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "format": "sqlite3, zstd-compressed",
        "uncompressed_bytes": db_path.stat().st_size,
        "built_at": (meta["finished_at"] if meta else None) or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "build_id": meta["id"] if meta else None,
        "build_mode": meta["mode"] if meta else None,
        "schema_version": schema_version,
        "schema_url": SCHEMA_DOC,
        "total_objects": sum(counts.values()),
        "counts_by_type": counts,
        "row_counts": tables,
        "enrichment_coverage": coverage,
        "licence": LICENCE,
        "how_to_open": "zstd -d solar_system-YYYYMMDD.sqlite.zst && sqlite3 solar_system-YYYYMMDD.sqlite",
    }


class LocalDest:
    def __init__(self, root: Path, public_base: str | None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base or root.resolve().as_uri()

    def put(self, path: Path, key: str, content_type: str) -> str:
        shutil.copyfile(path, self.root / key)
        return f"{self.public_base}/{key}"

    def put_text(self, text: str, key: str) -> str:
        (self.root / key).write_text(text)
        return f"{self.public_base}/{key}"

    def list_keys(self) -> list[str]:
        return [p.name for p in self.root.iterdir() if p.is_file()]

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class S3Dest:
    def __init__(self, bucket: str, endpoint: str, public_base: str, *,
                 acl: str | None = "public-read", region: str | None = None) -> None:
        import boto3  # optional extra [publish]
        self.bucket, self.public_base, self.acl = bucket, public_base.rstrip("/"), acl
        kw = {"endpoint_url": endpoint}
        if region:
            kw["region_name"] = region
        self.s3 = boto3.client("s3", **kw)

    def _extra(self, **base) -> dict:
        if self.acl:
            base["ACL"] = self.acl
        return base

    def put(self, path: Path, key: str, content_type: str) -> str:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs=self._extra(ContentType=content_type))
        return f"{self.public_base}/{key}"

    def put_text(self, text: str, key: str) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="application/json",
                           CacheControl="max-age=300", **self._extra())
        return f"{self.public_base}/{key}"

    def list_keys(self) -> list[str]:
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": "solar_system-"}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            out += [o["Key"] for o in r.get("Contents", [])]
            token = r.get("NextContinuationToken")
            if not token:
                return out

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def prune(dest, keep_days: int, today: datetime) -> list[str]:
    """Delete dated artefacts older than keep_days (never latest.json)."""
    cutoff = (today - timedelta(days=keep_days)).strftime("%Y%m%d")
    removed = []
    for key in dest.list_keys():
        if key.startswith("solar_system-") and key[13:21].isdigit() and key[13:21] < cutoff:
            dest.delete(key)
            removed.append(key)
    return removed


def publish(db: Path, dest, *, enrichment_store: Path | None, keep_days: int, level: int, stamp: str | None = None) -> dict[str, Any]:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d")
    name = f"solar_system-{stamp}.sqlite.zst"
    work = db.parent / name
    t0 = time.time()
    size = compress(db, work, level=level)
    digest = sha256_of(work)
    url = dest.put(work, name, "application/zstd")
    dest.put_text(f"{digest}  {name}\n", name + ".sha256")
    manifest = build_manifest(db, artefact_name=name, url=url, size_bytes=size, sha256=digest, enrichment_store=enrichment_store)
    manifest["published_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest["compress_seconds"] = round(time.time() - t0, 1)
    text = json.dumps(manifest, indent=1)
    dest.put_text(text, f"solar_system-{stamp}.json")
    dest.put_text(text, "latest.json")
    manifest["pruned"] = prune(dest, keep_days, datetime.now(timezone.utc))
    work.unlink(missing_ok=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--db", required=True)
    p.add_argument("--enrichment-store", default=None)
    p.add_argument("--dest", help="local directory instead of S3")
    p.add_argument("--bucket")
    p.add_argument("--endpoint", default=os.environ.get("S3_ENDPOINT"))
    p.add_argument("--public-base", default=os.environ.get("S3_PUBLIC_BASE"))
    p.add_argument("--no-acl", action="store_true", default=os.environ.get("S3_NO_ACL") == "1",
                   help="do not send object ACLs (required for Cloudflare R2)")
    p.add_argument("--region", default=os.environ.get("S3_REGION"), help="S3 region name ('auto' for R2)")
    p.add_argument("--keep-days", type=int, default=30)
    p.add_argument("--level", type=int, default=9)
    p.add_argument("--stamp", default=None, help="override YYYYMMDD (tests)")
    args = p.parse_args(argv)
    if args.dest:
        dest = LocalDest(Path(args.dest), args.public_base)
    elif args.bucket and args.endpoint and args.public_base:
        dest = S3Dest(args.bucket, args.endpoint, args.public_base,
                      acl=None if args.no_acl else "public-read", region=args.region)
    else:
        p.error("give --dest DIR, or --bucket + --endpoint + --public-base")
    m = publish(Path(args.db), dest, enrichment_store=Path(args.enrichment_store) if args.enrichment_store else None,
                keep_days=args.keep_days, level=args.level, stamp=args.stamp)
    print(json.dumps({k: m[k] for k in ("artefact", "url", "size_bytes", "uncompressed_bytes", "sha256", "total_objects", "pruned")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
