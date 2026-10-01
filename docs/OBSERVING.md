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
- `min_altitude_deg`: 0–85, default 20, geometric body centre.
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
