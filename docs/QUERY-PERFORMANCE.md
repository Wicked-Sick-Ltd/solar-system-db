# Local read-query profile and indexed object pagination

Measured on 1 October 2026 on an Apple M3 Max / 64 GiB, Python 3.12.14, SQLite
3.53.1, macOS-27.0.1-arm64-arm-64bit. This is a local laboratory comparison, not production
latency, a public percentile, or a throughput/load-test result. No live HTTP calls
or retained catalogue writes were performed.

## Workload and method

The unchanged offline build at baseline `97ad0f4` contains 2,431 solar-system
objects, 11 exoplanets in four hosts, and 158 starter targets. A temporary copy is
enlarged with exactly 50,000 synthetic objects and 5,000 synthetic hosts, each with
two planets: totals 52,431 objects / 10,011 planets / 5,004 hosts. These synthetic
measurements are profiling inputs, not astronomical data or a production census.

Synthetic objects are 98% asteroids, 1% comets and 1% TNOs. Every 17th semimajor
axis and 13th radius is null; every 11th name is empty. Values, NEO/PHA labels,
discovery dates and repeated sort keys vary deterministically. Every tenth host
has no measured distance; the others span 1–2,000 pc. Two thirds of synthetic
hosts use Transit discoveries. Existing catalogue rows and FTS aliases are
preserved; `ANALYZE` runs only on the disposable copy. The script caps enlargement
at 200,000 objects and 10,000 hosts.

Each case measures one first call with a cleared per-instance schema inventory,
then 11 repeated calls with that inventory cached. Each method opens fresh SQLite
read-only connections. **OS/filesystem caches are not evicted**: “cold” here means
cold application schema metadata, not cold disk. Raw samples, first-call times,
software versions, input/database hashes, exact source-file hashes, output hashes,
counts and EXPLAIN plans are in the four [JSON reports](performance/).
`source_revision` identifies the last committed base at measurement time;
`code_sha256` identifies the exact implementation bytes, including the reviewed
uncommitted change in the after reports.

Tracing, instruction counts and memory are measured in separate calls so they do
not distort the timing distributions. The progress callback runs every 1,000
SQLite VM instructions, giving a rounded-down work count; zero means fewer than
1,000 per connection, not zero work. `tracemalloc` measures Python allocations,
including result materialization and trace strings, **not** SQLite C allocations,
process RSS or filesystem caches. Payloads are compact read-layer JSON, excluding
REST wrappers, compression and network overhead. These are not full-route or
browser measurements.

## Demonstrated bottleneck and change

The original `find_objects` guarded every optional predicate with a parameterized
OR and selected ordering through CASE expressions. On the scaled catalogue,
EXPLAIN showed `SCAN o` plus `USE TEMP B-TREE FOR ORDER BY`, even for a bounded
keyset page or the eight planets. The changed method emits only fixed,
allowlisted active predicates with bound caller values and a fixed ID order for
keyset requests. It reuses the existing indexes; there is no schema migration.

A keyset page now uses `SEARCH o USING INDEX sqlite_autoindex_objects_1 (id>?)`
and no sorting tree. The planet filter uses `idx_objects_type (object_type=?)`.
Schema detection and selection use one connection instead of two. Offset mode
retains its original semimajor-axis/null-last/ID order; keyset mode retains exact
ID order and ignores offset, including when `after` is the empty string.

| Query | Before p50 / p95 ms | After p50 / p95 ms | Before / after VM steps (lower bound) |
| --- | ---: | ---: | ---: |
| `search_exact` | 0.442 / 0.524 | 0.441 / 0.484 | 0 / 0 |
| `search_absent` | 5.297 / 6.271 | 5.312 / 5.546 | 524,000 / 524,000 |
| `objects_planets` | 2.978 / 3.192 | 0.405 / 0.429 | 315,000 / 0 |
| `objects_offset_first` | 34.242 / 35.585 | 31.540 / 32.323 | 3,043,000 / 1,847,000 |
| `objects_offset_1000` | 36.162 / 36.509 | 34.524 / 35.487 | 3,117,000 / 1,913,000 |
| `objects_keyset_first` | 31.610 / 36.560 | 0.629 / 0.681 | 2,786,000 / 2,000 |
| `objects_keyset_middle` | 16.305 / 16.890 | 0.578 / 0.725 | 1,466,000 / 2,000 |
| `objects_filtered_keyset` | 7.552 / 7.681 | 0.941 / 0.988 | 665,000 / 19,000 |
| `starter_bright` | 1.519 / 1.674 | 1.565 / 1.664 | 0 / 0 |
| `exoplanets_first` | 3.635 / 4.103 | 3.701 / 3.800 | 222,000 / 222,000 |
| `exoplanets_filtered_total` | 1.171 / 1.215 | 1.113 / 1.336 | 64,000 / 64,000 |
| `galaxy_map` | 11.215 / 12.427 | 11.480 / 12.105 | 425,000 / 425,000 |
| `galaxy_map_distance` | 1.620 / 1.726 | 1.614 / 1.950 | 126,000 / 126,000 |

On the small unscaled fixture, keyset-first p50 changes from 2.175 to 0.568 ms;
keyset-middle from 1.481 to 0.565 ms. The much larger instruction-count reduction
in the scaled case demonstrates avoiding whole-catalogue work. Unchanged queries
show ordinary timing variance and are not claimed as optimizations.

## Costs retained in the baseline

After-change scaled counts and allocations (trace SELECT counts include SQLite
FTS internal statements where emitted, not just top-level application queries):

| Query | Cold / warm SELECTs | Connections per call | Result rows | JSON bytes | Traced Python peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `search_exact` | 3 / 2 | 1 | 1 | 146 | 5,993 |
| `search_absent` | 4 / 3 | 1 | 0 | 2 | 5,539 |
| `objects_planets` | 2 / 1 | 1 | 8 | 4,191 | 13,169 |
| `objects_offset_first` | 2 / 1 | 1 | 50 | 26,568 | 51,877 |
| `objects_offset_1000` | 2 / 1 | 1 | 50 | 24,154 | 50,710 |
| `objects_keyset_first` | 2 / 1 | 1 | 50 | 28,443 | 60,614 |
| `objects_keyset_middle` | 2 / 1 | 1 | 50 | 23,686 | 49,494 |
| `objects_filtered_keyset` | 2 / 1 | 1 | 50 | 23,682 | 49,868 |
| `starter_bright` | 4 / 4 | 1 | 24 | 47,710 | 1,297,803 |
| `exoplanets_first` | 3 / 2 | 1 | 50 | 35,293 | 98,005 |
| `exoplanets_filtered_total` | 3 / 2 | 1 | 50 | 25,989 | 64,991 |
| `galaxy_map` | 4 / 3 | 1 | 4,504 | 1,224,427 | 3,690,145 |
| `galaxy_map_distance` | 4 / 3 | 1 | 274 | 74,490 | 204,699 |

Exoplanet list timings include both the bounded result query **and** its filtered
count query. Galaxy-map timings include total/mapped counts, joined map rows and
matching counts. Starter-query timings include its total count and provenance
materialization. Search still performs its existing substring/discoverer fallback
when FTS finds nothing; removing it would change search semantics. Full offset
lists still sort by orbital axis. Those measured costs remain explicit future
candidates, not silently changed behaviour in this fix.

All 13 result hashes match before/after on both database sizes, and each size's
database-byte hash is identical. An additional 1,000 seeded comparisons against
the original method across v1/v2 schemas matched, including 580 nonempty results.
Regression tests cover exact bounds, zero/null/missing joined rows, both orders,
combined flags, parent resolution, injection-like text and v1 filter rejection.
SQLite's existing NaN-as-NULL binding behaviour is preserved rather than changing
input-validation semantics as part of a performance patch. New index-plan tests
assert indexed ID seeks and no temporary sort; CI does not assert noisy timing
thresholds.

## Reproduction

Use an isolated fixture, with repository dependencies installed. Example paths
below are temporary outputs; do not point a build command at a retained database.

```bash
export PYTHONPATH="$PWD"
SSDB_BUILD_PATH=/tmp/ssdb-profile-input.sqlite SSDB_NO_PUBLISH=1 python scripts/build_full.py --fresh --offline
python scripts/profile_read_queries.py --database /tmp/ssdb-profile-input.sqlite --output /tmp/queries-fixture.json
python scripts/profile_read_queries.py --database /tmp/ssdb-profile-input.sqlite --output /tmp/queries-scaled.json --synthetic-objects 50000 --synthetic-hosts 5000
python -m pytest api/tests/test_object_query_plans.py api/tests/test_query_profiler.py -q
```

For the before comparison, use a detached worktree at `97ad0f4`, copy the profiler
script into its `scripts/` directory, and run with that worktree as `PYTHONPATH`.
Use the **same** input SQLite file and synthetic counts. The profiler opens input
read-only, enlarges only a temporary copy, verifies the original SHA256 afterwards,
and rejects output paths that alias the input (including symlinks/hardlinks).
No report contains observer coordinates, user accounts, raw result records or a
local database path. Source fixture identities and measurement hashes are public
catalogue data. Do not use this utility for live load tests.
