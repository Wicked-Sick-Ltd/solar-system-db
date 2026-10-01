# Offline night planning

`GET /api/v1/observing/night` and the MCP `plan_observing_night` tool return
geometric Moon/planet planning windows for one local noon-to-next-noon interval.
This is an explicitly labelled **Astropy builtin approximation**, not the future
precision JPL-kernel provider and not an observing guarantee. Existing catalogue
`/sky` and `/positions` remain approximate two-body interfaces. The legacy sky
endpoint now reports Earth's Moon unavailable instead of substituting Earth;
other moons explicitly label their parent-body proxy.

## Input and interval

Required: `date` (`YYYY-MM-DD`), IANA `timezone`, `lat`, `lon`. Coordinates are
validated before rounding to 0.01 degrees; elevation is explicitly zero metres.
The date is the local calendar day on which the interval starts at noon. The
next local noon is constructed in that same timezone, so DST nights can be
23 or 25 hours. Nonexistent civil dates are rejected.

Optional fields:

- `targets`: unique comma-separated names from `moon,mercury,venus,mars,jupiter,saturn,uranus,neptune`;
  default `moon,jupiter,saturn`, at most eight. The Sun and Earth are not targets.
- `min_altitude_deg`: 0–90, default 20, geometric body centre.
- `sun_altitude_deg`: -6, -12 or -18, default -12 (civil/nautical/astronomical
  twilight thresholds). These are explicit altitude constraints, not a universal
  definition of suitability for a particular instrument or target.
- `min_moon_separation_deg`: 0–180, default 0. For targets other than the Moon,
  this constraint applies only while the Moon's geometric centre is above 0°.

All windows additionally require at least 30° target/Sun separation. This
exclusion is not eye-safety advice. Windows do not model weather, terrain,
refraction, extinction, light pollution, limiting magnitude or equipment.

Malformed, repeated/unknown REST parameters or unsupported values return 422.
Unavailable ephemeris/Earth-orientation coverage returns 503, not an empty
success. MCP rejects unknown parameters and booleans used as numbers before
calculation, with the same core validation. MCP offloads calculations from its
event loop and permits at most two running/pending plans; overload returns a
503-style unavailable result immediately. Cancelled callers retain their slot
until the worker actually finishes. REST responses are `no-store` and
limited to ten requests per minute. The calculation does not write coordinates
to catalogue or account data; infrastructure access logs remain a separate policy.

## Response version 1

- `observer`: rounded latitude/longitude, IANA timezone, elevation in metres.
- `night`: requested local date, exact UTC start/end, elapsed hours.
- `constraints`: selected altitude/darkness/Moon separation and fixed solar
  exclusion, including the conditional Moon rule.
- `method`: provider/library, coordinate/refraction convention, Earth-orientation
  coverage and measured/predicted status, model limitations, sampling interval
  and **numerical** root tolerance (not a physical accuracy claim).
- `darkness`: UTC intervals and explicit state.
- `moon`: geocentric illuminated fraction (0–1), phase angle and elongation in
  degrees at the interval midpoint, plus topocentric samples across the night.
- `targets`: named body IDs, `windows_found`, `no_matching_window` or
  `unresolved_grazing`, UTC windows and bounded samples. Samples contain geometric
  altitude/azimuth, Sun/Moon separation, distance in AU, Sun altitude and Moon
  altitude. Moon-to-itself separation is exactly zero.

Common samples are five minutes apart. Crossing brackets are refined to one
second numerically. Derivative-bracketed extrema are refined before threshold
crossings so a narrow window between samples can be found. Sample-aligned,
boundary and near-tangent cases are explicitly marked unresolved; sub-second
windows are omitted. Sample times and roots are model estimates, not a promise
of second-level astronomical or horizon accuracy.

## Offline policy and scientific provenance

Astropy's builtin ephemerides are requested explicitly and consistently for the
Sun, Moon and planets; no JPL kernel is silently downloaded. `iers.auto_download`
is disabled process-wide. The bundled Earth-orientation table must cover both
ends of the night; stale prediction/conversion failures produce unavailable.
Updating the installed `astropy-iers-data` package is an operational dependency
for future dates. The broad accepted date syntax range 1900–2100 does not imply
Earth-orientation availability throughout it. No catalogue schema change or
catalogue rebuild is needed.

Primary implementation references:

- [Astropy solar-system ephemerides](https://docs.astropy.org/en/stable/coordinates/solarsystem.html)
- [Astropy Earth-orientation/offline policy](https://docs.astropy.org/en/stable/utils/iers.html)
- [JPL Horizons definitions and horizon limitations](https://ssd.jpl.nasa.gov/horizons/manual.html)

`api/tests/fixtures/observing-horizons.json` preserves 16 bounded official JPL
Horizons requests and raw responses: Moon positions at London/Sydney/equatorial/
polar sites, each other supported planet and the Sun at London, and four
geocentric lunar phases. Two additional minute-sampled requests independently
bracket a lunar 20° altitude crossing and a solar -12° crossing. Each request
records explicit body/site, UTC range, airless convention, selected quantities
and retrieval time. These fixed references are read offline in tests; tests
never refresh them or fetch weather/ephemerides.

Regression tolerances are 0.1° for angles, 0.1% relative distance, and 0.001
absolute illuminated fraction, with event roots inside the independent JPL
minute brackets. In the initially captured limited cases, the largest altitude
error was about 0.0024° and illuminated-fraction difference below 0.000007. These
observations are **not a global error bound**. Broader independent comparisons,
a pinned local JPL kernel/checksum and explicit supported date range are required
before describing a later provider as precision ephemerides.

Run `pytest api/tests/test_observing_engine.py --import-mode=importlib`. The suite
also exercises 23/25-hour DST boundaries, missing civil days, polar seasons,
Moon-above-horizon logic, solar exclusion, strict REST/MCP inputs, unavailable
coverage and synthetic grazing tracks whose windows lie between samples.

## Optional checksum-pinned JPL provider

The default remains `OBSERVING_EPHEMERIS=builtin`. To opt into the independently
labelled JPL provider, install `pip install -e '.[observing-jpl]'` (jplephem 2.24 is
pinned), provision the unmodified kernel separately, then configure
`OBSERVING_EPHEMERIS=jpl-de440s` and `OBSERVING_JPL_KERNEL=/absolute/path/de440s.bsp`.
These are operator settings; clients cannot select a path, URL or checksum.
No production setting is changed by installing this feature. Both REST and MCP
use the same configured provider. Unknown configuration, missing dependency,
wrong size/hash, expired IERS coverage or worker failure returns 503; there is no
silent fallback or runtime download.

The accepted artifact is [NAIF's DE440s](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp),
32,726,016 bytes, SHA256
`c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2`.
Retrieved 2026-10-01; its MD5 `3917ee56769db332790c751e2168843d` matched
[NAIF's published checksum list](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/aa_checksums.txt).
The SHA256 is calculated locally from those verified bytes, not represented as a
JPL-published SHA256. The binary is ignored by git and is not redistributed in
this repository. [NAIF's rules](https://naif.jpl.nasa.gov/naif/rules.html) permit
kernel download/use and unmodified redistribution; this data is not relicensed
under the application's MIT license. Credit NASA/JPL's Solar System Dynamics
team, NAIF and Park et al. (2021),
[The JPL Planetary and Lunar Ephemerides DE440 and DE441](https://ssd.jpl.nasa.gov/doc/de440_de441.html).

Kernel segments cover 1849-12-26 through 2150-01-22 TDB. The service also requires
one day of preceding kernel coverage for retarded light time, 1900–2100 input
bounds and valid bundled IERS coverage: the kernel's long span does not imply
Earth-orientation support for all those dates. Moon, Mercury and Venus use body
centres; Mars through Neptune use their system barycentres. DE440s does not
include independent outer-planet centre segments. This distinction appears in
the response. Topocentric apparent positions retain the same no-refraction,
sea-level and IAU/Astropy transformation conventions as the builtin contract.

Astropy's explicit `get_body(ephemeris=...)` argument alone does not select that
kernel for every subsequent coordinate transform. Therefore the complete JPL
plan runs in a disposable child process with the documented
`solar_system_ephemeris` context set to the verified local snapshot. The parent
never changes its global ephemeris state. Two shared planning slots per server
process bound simultaneous REST/MCP calculations before allocating private
kernel copies. MCP also retains its existing no-queue dispatch guard. A parent
copies and SHA256-verifies at most 32,726,017 bytes into a private temporary file;
source replacement cannot alter a running plan. It owns cleanup even if the
worker crashes or exceeds 35 seconds. Child input travels over stdin, not argv;
stderr is never returned or logged, and JSON output is capped at 1.5 MB. The
context's cached SPK descriptor is explicitly closed. Multi-process deployments
multiply the two-slot limit. Consumers should allow at least 40 seconds when this
provider is enabled.

Responses identify the kernel hash, byte size, TDB segment coverage and
Astropy/ERFA/jplephem versions. Both providers identify the effective IERS table:
`effective-iers-columns-le-f64-v1` hashes the ordered MJD, UT1_UTC, PM_x, PM_y,
UT1Flag and PolPMFlag columns. Each column is prefixed by compact JSON of its
name and unit. Numerical columns use little-endian float64 values with canonical
NaNs followed by uint8 mask bytes; flags use compact JSON strings. This identifies
the actual UT1/polar-motion/status inputs, not merely the package release label.
Dependency versions, IERS identity and kernel identity must match when reproducing
an output; one-second crossing tolerance is not an astronomical accuracy claim.

### Offline acceptance

Provision the official artifact outside tests (curl timeout/size bounds and a
SHA256 check are required), then run:

```sh
JPL_TEST_KERNEL=/absolute/path/de440s.bsp pytest api/tests/test_jpl_provider.py
```

Tests never download data. Without this explicit path, ordinary local unit runs
skip real-kernel cases; CI provisions the pinned file first and runs them.
The 16 previously recorded Horizons point/phase cases cover lunar positions at
four sites, the seven planets, the Sun and four lunar phases. Two additional
recorded minute-bracket cases independently bound the Moon-altitude and solar
twilight crossings of a complete isolated-worker plan. JPL acceptance
requires angular differences below 0.002 degrees, relative distance differences
below 0.00002 and lunar illuminated-fraction differences below 0.00002. These are
fixture-specific regression thresholds, not universal physical error bounds;
outer-planet references are centre positions and retain the documented barycentre
difference. Integrity, source replacement, no-download, common-capacity, timeout,
crash, malformed output and unavailable-IERS cases also run locally.

## Selected hours and user-entered horizons

`POST /api/v1/observing/night` accepts a JSON object with the same required
`date`, `timezone`, `lat` and `lon` and optional scalar fields as GET. The existing
GET endpoint remains compatible; structured constraints use POST or MCP.
The body is limited to 16 KiB, must use `application/json`, and must not contain
query parameters, duplicate JSON keys, unknown keys or nonfinite numbers.
The POST calculation runs off the event loop behind the common planning gate.

Two new optional fields, `window_start_utc` and `window_end_utc`, must both be
omitted/null or both be exact `YYYY-MM-DDTHH:MM:SSZ` strings. They must define an
ordered interval of at least one second wholly inside the local noon-to-noon
night. Explicit UTC times distinguish the repeated local hour at a DST change.
The response includes the effective bounds in `constraints`, including the
resolved full-night bounds when no narrower interval was supplied. Target and
darkness windows, and their status, apply to that selected interval. Chart
samples still cover the full local night; the lunar phase retains its explicit
full-night midpoint reference time.

`horizon_mask` is null/omitted for unknown, or 2–72 points of exactly
`{"azimuth_deg": 0, "min_altitude_deg": 10}`. Values must be finite JSON numbers,
with azimuth 0–360 and altitude −90–90 degrees. Points sort by azimuth; 360
canonicalizes to 0 and duplicate directions are rejected. Linear interpolation
wraps across north. A user-entered zero-degree horizon is distinct from unknown.
The required target altitude is the greater of the independent baseline and
interpolated horizon. Baseline now accepts 0–90 degrees, matching saved site
preferences; a zenith tangency is unresolved, not a finite observing window.
Target samples expose nullable `horizon_altitude_deg` and nonnull
`required_min_altitude_deg` for explaining the two constraints.

Mask corners and intersections with the baseline create nonsmooth thresholds.
The engine first refines azimuth turning points, then crossings of every mask
and baseline corner, and refines altitude thresholds separately within those
pieces. Corner bisections evaluate their candidate times in vectorized batches;
chart partition points do not themselves become final interval boundaries.
Thus a narrow obstruction or clear gap between five-minute chart
samples is not silently missed. Azimuth bands with a greater-than-90-degree
step or an endpoint above 89.5-degree altitude are conservatively unresolved
and excluded when a mask is present, because azimuth near zenith is unstable.
The existing `unresolved_grazing` status also covers this explicitly explained
geometry uncertainty. Coarse masks can still miss real obstacles; no terrain
survey or guaranteed view is claimed. Moon interference continues to use the
Moon's geometric centre above 0 degrees independently of the target's horizon
mask, and never excludes the Moon target itself.

Example (values are illustrative, not an observer's saved location):

```json
{
  "date": "2026-10-01", "timezone": "Europe/London", "lat": 51.5, "lon": -0.12,
  "targets": "moon,saturn", "min_altitude_deg": 20,
  "window_start_utc": "2026-10-01T20:00:00Z",
  "window_end_utc": "2026-10-02T04:00:00Z",
  "horizon_mask": [
    {"azimuth_deg": 0, "min_altitude_deg": 10},
    {"azimuth_deg": 90, "min_altitude_deg": 30},
    {"azimuth_deg": 180, "min_altitude_deg": 5},
    {"azimuth_deg": 270, "min_altitude_deg": 15}
  ]
}
```

### Verified catalogue directions

The same GET, bounded POST and MCP tool also accept exact case-sensitive pinned
IDs, for example `moon,bsc5p:hr2491,openngc:NGC0224`. There are at most eight
combined targets and the comma-separated input is limited to 2,048 characters.
Only the packaged, hash-verified starter sample is supported: 50 BSC5P stars and
107 OpenNGC directions with independently verified source frames. `openngc:Mel022`
is explicitly unsupported because its frame evidence is incomplete. Unknown,
duplicate or unsupported IDs fail the entire request with 422; missing/corrupt
packaged source evidence fails with 503. There is no catalogue lookup network
request, database mutation, guessed frame, automatic substitution or fallback.

The planner reads the pinned packaged sample, which can differ from an older
catalogue database. Each catalogue target adds a `catalogue` object with
`source`, `snapshot_sha256`, `upstream_sha256`, `source_url`, `retrieved_at`,
`license`, `attribution`, `license_url`, the complete `astrometry_evidence` identity, and `input_coordinates`
(RA/Dec degrees, frame, equinox, Julian reference epoch, unknown observation
epoch). It also reports `motion_model`, `proper_motion_applied`, `frame_transform`, both angular
proper-motion components, `distance_au: null`, refraction conditions and an
accuracy note. Every catalogue sample likewise has `distance_au: null`.
Solar-system targets retain their existing physical-distance contract and do
not acquire a `catalogue` object. Preserve the returned attribution and licence
when sharing derived catalogue results, including OpenNGC's CC-BY-SA-4.0 terms.

BSC5P coordinates are FK5 with equinox and reference epoch J2000.0. The planner
uses Astropy's documented `SkyCoord.apply_space_motion` without supplied distance
or radial velocity, passing the verified cosine-scaled RA component directly as
`pm_ra_cosdec` (no second cosine factor). It retains only the resulting unit-sphere
direction; internal ERFA safe-distance and induced radial-velocity conventions
are never exposed as measured physical quantities. This is an **angular-only
linear-motion estimate** with a zero-radial-velocity propagation assumption,
not a full stellar ephemeris. Astropy applies the FK5 J2000 orientation rotation
to ICRS but does not apply the empirical FK5/Hipparcos frame-spin correction;
`frame_transform` and the per-target accuracy note state this approximation.
The published orientation fixture does not certify a spin-corrected proper-motion
model. Annual parallax, measured radial motion, perspective
acceleration, component/binary orbits and their uncertainty are absent. ERFA may
emit a generic `pmsafe` safe-distance warning because distance is deliberately
missing; request-time global warning filters are not changed.

The 107 OpenNGC targets remain static ICRS catalogue directions referenced to
J2000.0; no unknown proper motion is filled with a measured zero. The shared
apparent-coordinate transforms include the selected solar-system ephemeris,
Earth orientation and observer position, with zero atmospheric pressure and no
refraction. Sun/Moon separation is evaluated in the same apparent observer frame.
The JPL calculation, including all catalogue transforms, remains inside its
isolated process; common capacity, timeout, output bounds and cleanup are unchanged.

1900–2100 is an input calculation bound, **not a scientifically guaranteed
accuracy interval** for linear stellar motion. Bundled IERS coverage restricts
actual calculable nights further. BSC source RA/Dec rounding (four decimal
degrees in this snapshot), rounded proper motions and catalogue systematics
remain; no uncertainty propagation or precision guarantee is implied. Static
extended-object centres do not predict the visibility of the object or its
outline. Numerical event tolerance is not telescope-pointing accuracy.

Offline validation includes the published ERFA v2.0.1 `t_fk52h` J2000 frame
orientation values (exact URL, source hash and comparison tolerance recorded in
`api/tests/fixtures/catalogue-erfa-reference.json`), independent tangent-vector
motion checks including high declinations, negative motions and genuine zero,
common-AltAz separation comparisons, and mixed Moon/star/deep-sky plans through
the actual checksum-pinned JPL child. These are implementation comparisons,
not an observational accuracy certification. The source-frame evidence and
catalogue selection limits are documented in
[COORDINATE-FRAME-EVIDENCE.md](COORDINATE-FRAME-EVIDENCE.md).

Primary implementation/reference sources:
- [Astropy space motion](https://docs.astropy.org/en/stable/coordinates/apply_space_motion.html)
- [ERFA v2.0.1 published validation cases](https://github.com/liberfa/erfa/blob/v2.0.1/src/t_erfa_c.c)
- [IAU SOFA astrometry documentation](https://www.iausofa.org/cookbooks)

The distinction between orientation and empirical frame spin is explicit in
[Astropy’s FK5 transform](https://github.com/astropy/astropy/blob/v8.0.1/astropy/coordinates/builtin_frames/icrs_fk5_transforms.py)
and [ERFA’s rotation-plus-spin transform](https://github.com/liberfa/erfa/blob/v2.0.1/src/fk52h.c).
