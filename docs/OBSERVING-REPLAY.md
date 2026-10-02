# Retained observing-model replay

An observing plan is not a query of the SQLite object's orbital-element tables.
Dynamic targets use the reported ephemeris; catalogue directions use the
checksum-verified packaged starter rows. Therefore a separately observed global
`catalogue_id` must **not** be attached as the snapshot used for this calculation.
Per-target `catalogue.snapshot_sha256`, upstream/evidence hashes and source credit
identify its actual inputs. A newer database and older package can legitimately
have different source versions: show that distinction, never certify a match from
a separate HTTP probe.

## Algorithm identity

New responses add optional `method.calculation` with:

- `algorithm`: `observing-source-files-sha256-v1`;
- `source_sha256`: the lowercase SHA-256 described below;
- `files`: ordered public relative package source names;
- `python_version` and `numpy_version`: actual runtime versions;
- `catalogue_scope`: `packaged-target-snapshots; SQLite catalogue not consulted`.

The fixed v1 file set includes the observing package's initialiser, catalogue,
ephemeris, horizon, identity, inputs, kernels, planner and worker modules, plus
`starter_astrometry.py` and `starter_catalogues.py`. Each exact file's SHA-256 is
placed in an ordered array of `{file, sha256}` objects. Hash the UTF-8 JSON with
sorted object keys, compact separators, and one terminating LF. Reads are bounded
to 256 KiB per explicit module. No path discovery, Git access, credentials or
absolute filesystem paths enter this metadata. The implementation reads Python
package resources, including source files in wheels.

This hashes algorithm **source files**, not an executed-code signature, a global
catalogue ID, or a universal accuracy certificate. The identity is cached for the
process lifetime. Deploy immutable package directories and restart workers;
editing Python files beneath an already-running process breaks that operational
association. Runtime/library versions, actual IERS used-column hash, JPL kernel
hash when used, and per-target source/evidence hashes remain separate. A wheel or
compatible source/environment must be retained to reproduce an earlier release;
the hash itself cannot recover missing files.

Clients accept omission for older responses as algorithm identity unreported.
When the member is present, validate all fields and preserve it in exports. Do not
invent an identity from a release name, request time, catalogue count or filename.

## Retained fixtures and verification

`api/tests/fixtures/observing-replay.json` contains actual offline model results,
not external observations. It records a 24-hour London night with the builtin
provider and a 25-hour London autumn-DST night with the actual pinned JPL provider.
Both include Moon, a proper-motion bright star and a static deep-sky target,
terrain and Moon-separation constraints. All event windows are retained, plus
three representative samples per track and lunar phase; it is not a full response
archive or an all-target accuracy test.

Run from the backend checkout with the compatible environment and retained kernel:

```sh
PYTHONPATH="$PWD" OBSERVING_JPL_KERNEL=/absolute/path/de440s.bsp \
  python scripts/verify_observing_replay.py api/tests/fixtures/observing-replay.json
# No JPL kernel is needed to verify just the retained builtin case:
PYTHONPATH="$PWD" python scripts/verify_observing_replay.py \
  api/tests/fixtures/observing-replay.json --provider builtin
```

The command never downloads a kernel, IERS data or catalogue. It reads at most
512 KiB of fixture JSON and at most four cases; the existing planner input bounds,
capacity and isolated JPL timeout remain active. Provider labels are explicit,
with no fallback. Output omits private transport errors and file paths. Exit 0
means every selected retained case matched; 1 means unavailable, different or
not comparable; 2 means an invalid fixture/provider selection.

Comparison first requires identical normalized request parameters, model/source
identity and derived local-night UTC bounds. Changes in timezone rules that alter
those bounds make the old result not comparable; timezone names alone are not
claimed to pin a tzdb release. A mismatched dependency/IERS/source identity is
reported **not comparable**, even if a few rounded outputs happen to match.
Tests explicitly skip the retained-environment replay when development dependency
identities differ; this is not reported as verified repeatability. CI still runs
independent primary-reference and algorithm regressions under its installed model.

Under matching identities, retained angles use absolute tolerance `1e-8` degrees,
distances `1e-10` AU, fractions `1e-12` and event endpoints two seconds. Sample
instants, nulls, statuses, target order and event counts remain exact. These
cross-platform numerical comparison tolerances are not observational errors or
claims of astronomical accuracy; the one-second root tolerance is also numerical.
No bit-for-bit claim is made across hardware/compiler environments.

To refresh after an intentional reviewed model/source change, recalculate each
case using its retained normalized request and explicitly selected provider, then
replace its `reference` with `solar_db.observing.replay.retained_result(plan)`.
Record the new UTC `recorded_at`, review changed identities and windows, rerun the
checker and preserve the prior committed fixture in history. Do not rewrite a
fixture merely to conceal a scientific regression. Redacted web session exports
lack observer inputs by design and are not sufficient to rerun this procedure.
