# "In the sky" — sky position for solar-system objects

**Status:** design approved in principle 2026-09-15 (Craig), spec awaiting review
**Repos:** `solar-system-db` (calculation + API), `solar-system-web` (panel)

## Goal

For any object whose orbit we can propagate, tell a visitor where it is in the sky
right now: right ascension and declination, which constellation it sits in, and
which hemisphere it favours. With the visitor's permission, use their location to
add the precise view from where they stand: altitude and azimuth, whether it is
above the horizon after dark, and rise / transit / set times for the day.

The default panel needs nothing from the visitor. The observer view is opt-in,
one click, and remembered only in the visitor's browser.

## Non-goals (this iteration)

- Accounts or server-side saved locations. Craig has flagged accounts as a likely
  follow-up; nothing here should make that harder, and nothing here depends on it.
- Apparent magnitude / brightness. Needs per-object phase functions; later.
- Arc-second precision. We are two-body throughout (same as `/positions`); no
  perturbations, nutation, aberration or light-time. Good to roughly a degree
  for the planets, which is enough to name the constellation and say whether
  something is up. The accuracy note says so on the page.
- Moons get their parent's sky position (they sit within a fraction of a degree of
  it at this precision). The Sun is special-cased (geocentric = minus Earth).

## Backend — `solar-system-db`

### New module `solar_db/sky.py` (pure functions, no I/O)

| Function | Does |
|---|---|
| `geocentric_equatorial(target_xyz, earth_xyz)` | Subtract, rotate ecliptic → equatorial with the J2000 mean obliquity (23.4392911°), return RA (deg, hours, h:m:s), Dec (deg, d:m:s), Earth distance (AU). |
| `elongation_deg(target_xyz, earth_xyz)` | Angle Sun–Earth–target. Under 20° reads as "lost in the Sun's glare". |
| `hemisphere(dec_deg)` | `northern` / `southern` / `equatorial` (|Dec| ≤ 15°), plus a `visible_from` sentence. |
| `constellation(ra_deg, dec_deg)` | Roman (1987) boundary lookup: precess J2000 → B1875, walk the 357-segment table. Table vendored as `solar_db/data/constellation_boundaries.tsv` from CDS catalogue VI/42 (public domain), with the 88 names. |
| `topocentric(ra_deg, dec_deg, lat, lon, jd)` | Local sidereal time (GMST from JD) → hour angle → altitude, azimuth. |
| `rise_transit_set(ra_deg, dec_deg, lat, lon, date)` | Standard h₀ = −0.5667° geometric-horizon solution; returns UTC ISO times or flags `circumpolar` / `never_rises`. |
| `sun_altitude(earth_xyz, lat, lon, jd)` | Reuses the above with the Sun's geocentric vector; `is_dark` = Sun below −6° (civil twilight). |

Earth's heliocentric vector comes from the existing `compute_heliocentric_position`
on `planet-earth`'s stored elements; the endpoint fetches both element sets and calls
the pure functions. Nothing in `positions.py` changes.

### Endpoint

`GET /api/v1/sky/{name_or_designation}?date=&lat=&lon=`

- `date`: ISO date or datetime, default now (UTC). `lat` ∈ [−90, 90], `lon` ∈ [−180, 180],
  supplied together or not at all (else 422). Rate limit 60/min, same as `/positions`.
- 404 when the object (or, for a moon, its parent) has no propagatable elements.

```json
{
  "name": "Mars", "designation": null,
  "input_datetime": "2026-09-15T21:00:00Z", "jd": 2461299.375,
  "ra_deg": 187.42, "ra_hours": 12.495, "ra_hms": "12h 29m 41s",
  "dec_deg": -2.31, "dec_dms": "-02° 18′ 36″",
  "distance_from_earth_au": 2.41, "distance_from_sun_au": 1.62, "elongation_deg": 18.7,
  "constellation": {"abbr": "Vir", "name": "Virgo"},
  "hemisphere": "equatorial",
  "visible_from": "Visible from both hemispheres.",
  "observer": null,
  "frame": "equatorial J2000, geocentric; alt/az topocentric; two-body propagation",
  "accuracy_note": "Two-body approximation, good to about a degree for the planets — enough to find the constellation and know whether it is up."
}
```

With `lat`/`lon`, `observer` becomes:

```json
{
  "lat": 51.5, "lon": -0.12,
  "altitude_deg": -12.4, "azimuth_deg": 281.0, "is_up": false,
  "sun_altitude_deg": -21.7, "is_dark": true,
  "rise_utc": "2026-09-15T06:41:00Z", "transit_utc": "2026-09-15T12:58:00Z", "set_utc": "2026-09-15T19:14:00Z",
  "circumpolar": false, "never_rises": false
}
```

Location is used for the calculation and not logged with anything identifying (the
privacy page already says this). Round `lat`/`lon` to 0.01° in the response.

### MCP server

Add a `get_sky_position` tool mirroring the endpoint, so the same answer is available
to Claude sessions. Small; follows the pattern of the existing position tool.

### Tests (`api/tests/test_sky.py`, `tests/test_sky_math.py`)

- Mars, Jupiter and Pluto RA/Dec on a fixed date within 1.0° of JPL Horizons values
  recorded in the test.
- Sun at the March equinox ≈ RA 0h, Dec 0° (within 0.5°).
- Constellation: eight fixed points (Betelgeuse → Ori, Sirius → CMa, Polaris → UMi,
  Antares → Sco, Canopus → Car, a point on the Vir/Lib boundary, the south celestial
  pole → Oct, RA 0/Dec 0 → Psc).
- Alt/az for Greenwich at a fixed instant vs a hand-checked reference.
- Rise/set: Dec +89° at 51°N is circumpolar; Dec −60° at 51°N never rises; a
  mid-declination case returns rise < transit < set.
- Endpoint: 422 for lat without lon, 404 for an object without elements, moon
  resolves to parent, `observer` null without coordinates.

## Frontend — `solar-system-web`

### Client and DTO

- `SolarApiClient::sky(string $id, ?string $datetime = null, ?float $lat = null, ?float $lon = null): ?SkyPosition`.
  Geocentric calls cache 5 minutes (the `positions` TTL); observer calls cache 5
  minutes keyed on lat/lon rounded to 0.1°. A 404 returns null so the section simply
  does not render, which lets the web PR merge before the backend deploys.
- `SkyPosition` and `ObserverView` readonly DTOs in `App\Services\SolarApi\Data`.
- `Format::raHms()` / `Format::decDms()` are not needed (the API returns strings) but
  `Format::bearing(float $az)` → "W", "NW" compass label is.

### Panel on the object page

After "Where is it now", a section **"In the sky"**, rendered when `sky` is non-null:

- Left: RA, Dec, constellation (linked to Wikipedia), hemisphere sentence, distance from
  Earth, elongation with a one-line reading ("18.7° from the Sun — lost in the glare",
  "close to opposition — best time of year").
- Right: a Livewire child `SkyObserver` (`objectId` prop). Idle state shows a button
  **"Get precise data for my location"**. Clicking it asks the browser for geolocation
  via Alpine; a fallback form takes lat/lon by hand. `setLocation(lat, lon)` validates,
  calls the client, renders altitude, azimuth with compass label, "Up now" / "Below the
  horizon", "Dark now" / "Daylight", and rise / transit / set converted to the visitor's
  local time by a tiny inline script from the UTC ISO values.
- The chosen location is kept in `localStorage['observer_location']` and re-applied
  automatically on later visits (Alpine `x-init` calls `setLocation` if present). A
  "Forget my location" link clears it. Nothing is sent to the server except for the
  calculation itself.
- Accuracy note under the panel, taken from the API.

### Tests

- Feature: object page renders "In the sky" with the faked payload; hides it when the
  sky endpoint 404s; Saturn page shows RA/Dec/constellation strings.
- Livewire: `SkyObserver` rejects out-of-range coordinates, renders observer fields
  from the faked payload, `forget()` returns to idle.
- Unit: `Format::bearing` boundaries.

## Rollout

1. Backend PR: module, data table, endpoint, MCP tool, tests. Deploy.
2. Web PR: client, DTOs, panel, observer component, tests. Safe to merge first (null
   guard), lights up when the backend deploys.
3. Follow-ups, separately: accounts + saved location; magnitude; "best month this
   year" using the elongation curve.

## Open questions resolved

- Where is the maths? Backend (Craig, 2026-09-15).
- Default vs precise? Default RA/Dec/constellation/hemisphere; precise behind a click.
- Accounts? Follow-up, not here.
