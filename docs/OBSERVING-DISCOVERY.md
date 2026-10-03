# Opt-in, bounded target discovery

`POST /api/v1/observing/discover` and MCP `discover_observing_targets` accept a
site, local night, selected hours and equipment preferences without requiring
visitors to know target IDs. This screens the **reviewed packaged starter sample**:
157 source-frame-supported rows out of 158, plus the Moon and seven planets.
M45's unsupported frame remains excluded. This is not an all-sky catalogue,
visibility prediction, telescope pointing service or detection score.

## Inputs and privacy

REST allows 5 discovery requests per minute per IP. MCP HTTP applies that same
`OBSERVING_DISCOVER_LIMIT` from `solar_db/http_quotas.py` to
`discover_observing_targets`. The two-slot planner cap is separate and is not
a substitute for this quota.

The REST route accepts only a JSON object, at most 16 KiB, with no query string.
Duplicate/unknown fields, non-finite values, boolean numeric values, malformed
dates and invalid windows/horizons fail before a worker starts. The request is
explicit and private (`Cache-Control: no-store`), never saved or cached by this
service. Coordinates are normalized to two decimals using the existing planner;
no location lookup, weather request or external service call occurs. No equipment
profile names, notes, aperture records or account data are accepted.

Geometry fields have exactly the existing [night planner](OBSERVING.md)
semantics: required `date`, `timezone`, `lat`, `lon`; optional `min_altitude_deg`
(0–90, default 20), `sun_altitude_deg` (-6/-12/-18, default -12),
`min_moon_separation_deg` (0–180, default 0), paired `window_start_utc` and
`window_end_utc`, and `horizon_mask`. Selected UTC seconds lie inside the local
noon-to-noon night; IANA timezone/DST and bundled IERS coverage still apply.
A mask has 2–72 distinct circular points, each with `azimuth_deg` and
`min_altitude_deg`. Unknown terrain is not a measured zero-degree horizon.

Additional fields:

| Field | Meaning |
| --- | --- |
| `equipment_mode` | Required `naked_eye`, `binocular` or `telescope`; changes the explained editorial family ordering below. |
| `preference` | `balanced` (default), `wide_field`, `stars`, `deep_sky` or `solar_system`; explicit family/field preference takes precedence over mode. |
| `true_field_deg` | Optional actual/estimated true field, 0.01–180 degrees. Omitted is unknown; no field is inferred from an equipment label. |
| `max_catalogue_v_magnitude` | Optional explicit source V-band cutoff, -30–30. This is **not** an inferred visual limiting magnitude. Values above the cut and unknown/non-V values are excluded and counted separately, including solar-system targets without supplied photometry. |
| `shortlist_limit` | Integer 1–8, default 6; bounds selected refinements, not a promise of that many successful candidates. |

`targets` is deliberately not accepted. Direct known-target planning continues
to use `/observing/night` unchanged. Neither request automatically uploads saved
profiles or accesses browser geolocation.

## Screen, order, then refine

One vectorized provider call calculates apparent geometry for the bounded pool
at instants no more than 20 minutes apart within the selected interval, including
its endpoints and at least a midpoint. It uses exactly the same verified source
frames, angular-only proper motion and provider as the existing night engine.
The screen tests darkness, the maximum of baseline and circular supplied horizon,
30-degree solar separation, and the requested Moon separation while the Moon's
geometric centre is above zero, independently of target terrain.

An object passes the coarse screen only if at least one sampled instant meets
all constraints. **Short, grazing or terrain-gap windows between samples can be
missed.** A zero-result screen cannot establish that the night has no targets.
The result always declares `incomplete_between_samples: true`; sampled matches
are counted as instants, never silently converted to continuous duration.

Ordering is deterministic and explicit:

1. The requested family, or known angular containment for `wide_field`.
2. Equipment-mode preference: naked eye puts Moon then bright-star sample first;
   binocular puts known deep-sky extent fitting the supplied field first, then
   other deep-sky targets, Moon, stars and remaining planets; telescope puts
   solar-system targets then historical double-star entries, other deep-sky and
   other stars first.
3. More matching sampled instants, then exact stable ID.

These are editorial preferences, not detection or resolution judgements.
There is no automatic magnitude cut for naked-eye mode, no inferred aperture,
weather, sky brightness, observer skill or success probability. This bright-star
sample is already source-selected at V magnitude ≤2; this does not certify that
any particular star is visible at a requested site. Historical double-star
membership never promises resolvable components or a current companion position.

Known deep-sky major-axis extent is compared with supplied true field only for
angular containment. Point-source/double separation is never treated as diameter;
missing/zero extent remains unknown. Integrated galaxy/nebula magnitude is not
point-source detectability or surface brightness. Bands, flags and code are kept
with original source magnitudes. Planet/Moon brightness and angular sizes remain
unknown in this descriptor.

Only the first at most eight ordered choices receive the existing refined night
calculation, including terrain corners, numerical root refinement and explicit
grazing uncertainty. No unbounded backfill loop runs. `candidates` includes only
choices with nonempty refined windows; choices with no window remain visible in
`plan.targets` and are counted in `selected_without_refined_window`. A candidate
may retain `unresolved_grazing` when some intervals are usable but ambiguity
remains. Refined geometric windows are not an observing-success promise.

## Response contract

The exact top-level members are `schema_version` (1), `request`, `discovery`,
`candidates`, `plan`, and `method`.

- `request` echoes all normalized geometry fields, excluding target IDs. This
  binds empty responses to their site/date/window as well as successful ones.
- `discovery.options` echoes all five normalized preference fields.
- Scope counts distinguish catalogue records, unsupported records, eight
  solar-system bodies, coarse matches, attempted refinements, excluded V/unknown
  photometry, and selected choices without a refined window. `sample_count`
  and `coarse_step_seconds` describe the actual screen.
- Each candidate has `id`, `name`, source `aliases`, `preference_reasons`,
  `field_context`, `coarse_matching_samples`, `sampled_peak_altitude_deg`,
  nullable original `appearance`, `brightness_status` and `refined_status`.
  IDs are an ordered subset of `plan.targets` with nonempty windows. Appearance
  has the same seven source-only fields as existing catalogue plan metadata.
- `field_context` is `unknown_angular_extent`, `field_not_supplied`,
  `catalogue_extent_within_field` or `catalogue_extent_exceeds_field`.
  `brightness_status` is `catalogue_value` or `unknown`, not a suitability grade.
- `plan` is the unchanged full night-plan contract for selected refinements, or
  null when no coarse candidate was selected. Its samples cover the whole local
  night and its windows use the selected interval.
- `method` equals `plan.method` when a plan exists. For an empty screen it still
  carries actual provider/kernel/IERS identity. Its window note explicitly says
  the five-minute/one-second refinement configuration was **not executed**;
  discovery's twenty-minute cadence is the actual screening method.
- `discovery.source_snapshots` retains both pinned catalogue source hashes, retrieval,
  attribution and licences. This service never reads the SQLite catalogue and
  never attaches a separately observed global database ID.
- `discovery.calculation` hashes the two bounded discovery source files using
  compact sorted-key JSON manifest entries plus a trailing newline, alongside
  `discovery.planner_calculation` for the existing astronomical modules/runtime identity.
  These source hashes are reproducibility context, not execution attestation.

## Bounds, failures and validation

Both builtin and JPL discovery execute in a private child under the common
planner capacity of two and a 35-second timeout, with a 1.5 MB output bound.
There is one child per request, not one child or HTTP request per catalogue row.
MCP reserves pending/running capacity before dispatch; cancellation does not free
capacity while work remains active. JPL uses the existing verified private kernel
copy and closes the child descriptor before deletion. Neither provider falls back
to a different model on error. The packaged candidate catalogue is capped at
158 rows; increasing that bound requires deliberate review.

REST validation failures are 422, oversized bodies 413, and busy/model/worker
failures 503; each is no-store with a bounded public message. MCP validation/tool
errors and availability status preserve the same distinctions. Internal paths,
worker stderr and original coordinates are not exposed in process arguments.

Local fresh-child examples on the development machine (not production latency or
all-night guarantees): builtin London 2026-10-01, three refinements: 8.25 seconds,
413 KB JSON; checksum-pinned JPL, eight refinements, 19:00–05:00 UTC, a two-point
horizon and 30-degree Moon separation: 10.48 seconds, 984 KB. The complete request
still has the hard timeout; more complex terrain can legitimately fail unavailable.

Tests cover real builtin/JPL screen-and-refine, literal missing/zero/negative
measurements, explicit filtering, distinct equipment order, field/diameter
semantics, selected hours/terrain/Sun/Moon constraints, honest empty/refinement
failure, stable ties, worker failure/cleanup and actual REST/MCP validation and
bounded responsive dispatch. They use offline sources and no live catalogue.
