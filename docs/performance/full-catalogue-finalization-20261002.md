# Full published catalogue: identity finalization, 2 October 2026

Three sequential fresh-process finalizations of disposable copies completed in
143.892, 144.096 and 143.808 seconds. The [unaltered machine report](full-catalogue-finalization-20261002.json)
records exact values, source/code hashes and every logical table count. This
extends the smaller mixed retained-catalogue evidence with a real full
minor-planet population; it does not replace measurements of tables absent here.

| Run | Finalization elapsed, seconds | Finalization process CPU, seconds | Whole-child peak RSS, bytes |
| --- | ---: | ---: | ---: |
| 1 | 143.89176008400682 | 143.440342 | 34,095,104 |
| 2 | 144.09589633300493 | 143.230685 | 38,092,800 |
| 3 | 143.80778512501274 | 143.341364 | 37,994,496 |

The host was an Apple M3 Max, 16 logical CPUs and 68,719,476,736 bytes (64 GiB)
of RAM; Darwin arm64, Python 3.12.14 and SQLite 3.53.1. It was **not an isolated
machine**. Other team benchmark jobs were held during the run, but operating-system
and background activity were not controlled. OS caches were not flushed; source
hashing and backup read the artifact before measurement. These are local
finalization measurements, not cold-cache timings, ingestion/build duration,
public API latency, field p75, or production capacity guarantees. RSS is a
process high-water mark, not filesystem-cache or machine-wide memory use.

## Input and identity

The [retained public manifest](full-catalogue-manifest-20261002.json) came from
`https://download.sol.wickedsick.com/latest.json` and identifies the dated
artifact URL `https://download.sol.wickedsick.com/solar_system-20261002.sqlite.zst`.
It records build time `2026-10-02T04:22:28Z` and publication time
`2026-10-02T04:24:09Z`. Before the benchmark, the compressed download was verified:
979,070,977 bytes, SHA-256
`0bfb3d1cbc79f7fc8cb7d0dc827dd86e307add0f21a25d4cfde5ca16e9ecd09b`.
Decompression produced a 3,912,310,784-byte standalone SQLite artifact, with
read-only `quick_check` reporting `ok`.

The harness independently measured uncompressed SHA-256
`d7cd507318365ca2362a6fa286e15943cabde2838e804db980034c573c728b33`
before and after. Size, modification time, device and inode also matched. The
source remained byte-for-byte unchanged and received no identity table or
triggers. Disposable copies were removed after the runs.

The legacy schema reports `user_version=3`, 1,574,019 objects and 14 logical tables
under the current hash policy, including 3,092,363 close approaches and 4,530,758
designations. It contains **no exoplanet, exoplanet-host or starter-target tables**;
the finalizer did not synthesize or ingest those populations. The manifest's
1,561,095 asteroids and remaining object types describe this artifact, not a
fresh live-upstream query.

All three **disposable finalized copies** produced
`sha256:f0dd8f32bac601567c6256e130f409bb9b322b3a9e9bd685456cbdc176c0f661`
under `catalogue-logical-v1`, with identical per-table counts. Each object count
and table set also matched the read-only input preflight. This is a derived
benchmark identity: the downloaded legacy artifact, published manifest and live
API did not gain or change any identity metadata. It is not a signature or a
new scientific-data release.

## Reproduction and instrumentation

The run used clean harness revision
`e6093c315a8b71ad3f4792c46049c52b0f78fc8c` in the isolated
`public-universe-full-catalogue-benchmark` worktree. The original backend's
Python 3.12.14 development interpreter was reused; the selected working directory
and `PYTHONPATH="$PWD"` made both parent and child imports resolve to the reviewed
worktree. After the run, `catalogue_identity.__file__` and the harness module path
were explicitly checked against that worktree, and the actually imported
finalizer's SHA-256 matched the report. No original checkout source was changed.

The invocation below is equivalent to the executed absolute-interpreter command,
with its repository-relative interpreter location shown for portability. Run it
from that worktree; choose a **new** report path on repetition because reports
are never overwritten:

```sh
PYTHONPATH="$PWD" ../../solar-system-db/.venv/bin/python \
  scripts/benchmark_catalogue_finalization.py \
  /tmp/public-universe-full-catalogue/solar_system-20261002.sqlite \
  --scratch-dir /tmp/public-universe-full-catalogue \
  --output /tmp/public-universe-full-catalogue/finalization-benchmark.json \
  --timeout-seconds 1800
```

The source and harness hashes in the report identify the exact implementation.
[Harness documentation](../CATALOGUE-FINALIZATION-BENCHMARK.md) defines timer,
CPU/RSS, timeout, disk, cleanup and immutable-read assumptions. Ten tiny isolated
regressions and independent instrumentation review passed before this full run.
The report was produced only after all runs and source-integrity checks succeeded;
no network access or deployment occurs in the benchmark command.
