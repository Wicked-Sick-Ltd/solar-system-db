# Disposable catalogue finalization benchmark

`scripts/benchmark_catalogue_finalization.py` measures the current logical identity
finalizer against a **frozen, standalone local SQLite artifact**. It does not
fetch data, ingest sources, publish an artifact, update an API or modify the
input. Do not point it at a live builder or API database: SQLite WAL, shared-memory
and rollback-journal sidecars are refused, and the read-only backup connection
uses immutable mode specifically for a closed artifact.

From the checked-out source root and its installed development interpreter:

```sh
PYTHONPATH="$PWD" python scripts/benchmark_catalogue_finalization.py /path/to/verified.sqlite \
  --scratch-dir /path/to/disposable-space \
  --output /path/to/new-report.json \
  --timeout-seconds 1800
```

An optional `--expected-sha256` binds the input to a previously checked
**uncompressed SQLite** checksum. A download manifest's compressed `.zst`
checksum is a different value: verify compressed bytes and decompression
separately before running this tool. Keep the public manifest with the resulting
report if it supplies the artifact's population and acquisition context.

The preflight checks regular-file size (at most 8 GiB), absent sidecars, available
scratch space, the table/column assumptions used by current source provenance,
and unsupported foreign keys to excluded surrogate IDs. Legacy `user_version=3`
is accepted when its actual tables satisfy those assumptions. Missing optional
exoplanet or starter tables stay absent; the harness does not infer empty
populations or add them.

The harness makes one SQLite backup through the read-only source connection.
Three sequential runs each copy that snapshot and launch a fresh interpreter.
Only the copies receive identity tables, invalidation triggers, durable journal
settings and finalization writes. Source SHA-256, byte size, modification time,
device and inode must match before/after; all three logical IDs and row counts
must agree. Reports are created exclusively, never overwrite an existing file,
and successful reports contain no absolute input/scratch paths. Temporary copies
are removed on success and Python exceptions, including a child timeout. A hard
process kill or power loss can leave the disposable scratch directory; it does
not turn the input into a writable database.

Each backup/child has a 1–3600-second timeout (default 1800); the three runs are
sequential, not a load test. The source-size bound also bounds hashing/copy input.
The free-space allowance covers a snapshot, current run and a source-sized
sort/journal allowance plus 256 MiB. It is not an operating-system disk quota or
a promise that every future schema fits this allowance. SQLite temporary work
is directed into the disposable workspace. Child finalization performs no
subprocess work; the standard subprocess timeout kills and waits for that child.

## Measurement interpretation

- `seconds` and `finalize_cpu_seconds` surround `finalize()` only. They exclude
  source checksum/preflight, backup, copy, interpreter/import startup, and the
  subsequent stored-metadata validation. CPU is process CPU time, not machine
  utilization or elapsed latency.
- `process_peak_rss_bytes` is the whole fresh child's process high-water mark,
  including interpreter/imports and metadata verification. macOS reports bytes;
  Linux reports KiB, normalized to bytes. It is not incremental finalizer memory,
  summed fleet memory, filesystem cache usage, or GPU memory.
- OS caches are **not flushed**. Source checksums and backup already read the
  artifact. The results are sequential local measurements, not cold-cache,
  public request latency, field p75 or a production capacity guarantee.
- Logical row counts come from actual finalization and include only the tables
  present under `catalogue-logical-v1`. This is the cost of identity finalization,
  not full ingestion, compression, publication or every read-query workload.
- The report retains Git revision plus exact harness/finalizer file hashes,
  Python/SQLite versions, OS/architecture, input/schema hashes and per-run values.
  The content hash is not a signature or an astronomical accuracy certificate.

Tests use tiny disposable legacy-schema catalogues, three real fresh child
processes, incompatible source/sidecar rejection, checksum mismatch, failure
cleanup, changed-source detection and refusal to overwrite a report hard-linked
to the source. Large-artifact results require a separately retained actual run;
passing these tests does not establish full-catalogue performance.
