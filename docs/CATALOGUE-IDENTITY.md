# Catalogue identity and build provenance

New builds expose a stored identity through `SolarDB.catalogue_identity()`,
`GET /api/v1/catalogue`, the MCP `get_catalogue_identity` tool, and the
`catalogue_identity` member of `/api/v1/stats` and download manifests. The REST
identity response uses `Cache-Control: no-cache` so a client can revalidate the
current snapshot. No local catalogue path is exposed (`stats.db_path` was removed).
Existing database files remain readable: an identity that was never recorded is
explicitly `status: "unknown"`, with null IDs and `reason: "not_recorded"`.
Clients must preserve that uncertainty rather than deriving an identity from a
filename, object count or request time.

## Three different checksums

- `catalogue_id` (`sha256:<64 hex characters>`) identifies logical catalogue data
  under `hash_policy: "catalogue-logical-v1"`. Identical offline inputs produce
  the same ID despite retrieval timestamps, SQLite page layout and insertion IDs.
- `build_identifier` identifies that logical ID together with its recorded build,
  source manifest and SQL schema fingerprint. Two builds can share a catalogue ID
  while recording different retrieval times, builder code or options.
- Download `sha256` still checks the compressed artifact bytes. New
  `sqlite_sha256` checks the exact uncompressed SQLite file **in that artifact**,
  which need not be byte-identical to the builder's original database file.

`built_at` is the recorded UTC-aware build finish time, not publication time or
an observation epoch. `build` contains `started_at`, `finished_at` and `mode`.
`source_manifest_sha256` checks the canonical source manifest; `schema_sha256`
checks the SQL schema and SQLite `user_version`. `row_counts` reports the tables included in the logical
hash. These hashes detect differences; they are not signatures or proof that
upstream data is correct.

## Logical hash policy v1

The implementation is `solar_db/catalogue_identity.py`. It streams tables in
binary name order and rows in retained primary-key order followed by remaining
column values with binary collation and SQLite storage-type tie breakers.
Column names and declared types, table names, duplicate rows and row counts are
included. Values are type tagged: null, decimal integer text, hexadecimal IEEE
float text, exact Unicode text and base64 blobs. Nonfinite numbers are refused.
Records use sorted-key, compact UTF-8 JSON plus a newline. Stored JSON columns
are **exact text**: whitespace or key-order changes affect the logical ID.

Scientific primary IDs and foreign-key relationships remain included, notably
meteor IAU numbers and parameter-set numbers. Only these operational fields are
excluded:

- `objects.created_at/updated_at`; `updated_at` on orbital elements, physical and
  visual properties and discoveries.
- `retrieved_at` on sources, impact monitoring, exoplanets and exoplanet hosts.
- Unreferenced `id` insertion counters on sources, rings, close approaches and
  radar observations. A future foreign key referencing one refuses finalization
  until the policy is reconsidered; it is not silently stripped.
- Build history, enrichment crawler bookkeeping, the identity table, SQLite
  internal tables and the known derived full-text search tables.

All other physical tables, including newly added tables, are included. A changed
policy requires a new hash-policy version. Acquisition timestamps excluded from
the logical ID still contribute to the separate build identifier through the
source manifest's complete recorded-retrieval digest.

## Provenance coverage and limits

The manifest records source groups, record counts, earliest/latest stored
retrieval markers, pinned starter-catalogue hashes and coordinate evidence,
retained offline input hashes, relative builder source-file hashes, Git revision
when available, and non-secret build options. Absolute filesystem paths,
environment variables and credentials are not recorded. The source-tree digest
also captures uncommitted builder code; Git revision alone is not treated as
proof of a clean build. The enrichment option records whether a store was
requested, without publishing its path.

An unavailable upstream version is `null`. Date-only coordinate-evidence retrieval
markers remain in `coordinate_evidence_retrieved_date`; the corresponding
`coordinate_evidence_retrieved_at` is null rather than an invented midnight. Existing curated seeds sometimes use
an ingestion timestamp as their retrieval marker; this does not invent an
original upstream download date. Not every online upstream response is retained,
so the manifest does not claim that an arbitrary historical live build can be
recreated solely from this repository. The starter sources retain their own
licences and original-source evidence; the MIT software licence does not replace
source-specific data terms, including OpenNGC's CC BY-SA 4.0 attribution.

## Finalization and invalidation

The builder finalizes after all data stages and SQLite maintenance, in one
`BEGIN IMMEDIATE` transaction, restoring a rollback journal and full synchronous
writes after bulk ingestion. The logical hash, schema fingerprint, manifest
and metadata row are committed together. Every hashed table and `build_meta`
has INSERT, UPDATE and DELETE invalidation triggers, recreated in the builder-owned
namespace at finalization so an old same-name no-op cannot be accepted. A subsequent write removes
the identity; an added/dropped table, column, index or trigger changes the schema
fingerprint. Old, changed, corrupt or unsupported metadata returns `unknown`
with a reason and null identifiers. Serving requests reads only small metadata
and the schema; it never scans catalogue data to manufacture an identity.

This protects ordinary builder and SQL maintenance workflows. An actor able to
rewrite the database and forge its metadata is outside this unsigned integrity
contract. Read-only API access remains the operational boundary.

Publishing uses SQLite's backup API through a read-only source connection to
make a consistent disposable snapshot, including committed WAL content. It
reads metadata, hashes and compresses that same copy even if the builder's
source changes concurrently. It never checkpoints or edits the retained source.
The temporary snapshot is removed after success or failure. Daily artifact URLs
and retention rules are unchanged: an immutable content identifier does **not**
mean that every historical file has a permanent URL or is still hosted.

## Offline verification

Run the repository CI checks from an installed development environment:

```sh
python -m ruff check --select F401 api/main.py
python scripts/verify.py
pytest mcp-server/tests/ api/tests/ --import-mode=importlib
```

`scripts/verify.py` explicitly recomputes a known logical ID and row counts,
which can take time on a full catalogue; legacy files keep an informational
unknown result. Request handlers never run that expensive verification.

`api/tests/test_catalogue_identity.py` builds disposable fixture catalogues with
different Python hash seeds, checks stable logical IDs, invalidation, schema and
legacy handling, provenance changes, actual REST/MCP response parity, and local
export checksums under a concurrent source edit. These tests never publish to a
remote destination or mutate a retained catalogue. The identity-specific test
also prevents name/alternate classification from depending on set iteration in
satellite ingestion.
