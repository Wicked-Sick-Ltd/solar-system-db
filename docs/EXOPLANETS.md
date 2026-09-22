# Exoplanet catalogue and location contract

Implemented 2026-09-22 across `solar-system-db` and `solar-system-web`.

## Data and provenance

Source: [NASA Exoplanet Archive PSCompPars](https://exoplanetarchive.ipac.caltech.edu/docs/API_PS_columns.html),
queried via [TAP](https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html).
Acknowledge NASA Exoplanet Archive, operated by Caltech under contract with NASA,
and the PSCompPars dataset DOI [10.26133/NEA13](https://doi.org/10.26133/NEA13).
The archive composes values from different references; they are not necessarily
one self-consistent physical solution. Calculated values, minimum masses, limits,
controversial flags and uncertainties must remain distinguishable.

`source_data` preserves all requested upstream fields, including measurement
references (which may contain archive HTML; never render them as trusted HTML).
The website links to the fixed NASA overview origin and escapes catalogue text.
No planet textures, habitation claims, or unmeasured orbital orientations are generated.

IDs are SHA-256-derived from exact archive names (20 hex characters, prefixed by
`exo-`/`host-`). They survive reordering/reimports, but archive renames produce new
IDs; alias reconciliation is future work. Host grouping uses archive `hostname`,
including separately designated members of multiple-star systems. It is not a
cross-matched stellar census. Snapshot replacement removes withdrawn records.

The ingest runs transactionally. Empty, malformed and duplicate-name snapshots
are rejected. The normal fresh nightly build fails before publishing on source
failure. `--skip-exoplanets` deliberately leaves a fresh build without this data;
with a non-fresh build it retains the previous snapshot and its retrieval times.

## Coordinates

Input: archive ICRS right ascension/declination and positive `sy_dist` in parsecs.
A distance with a limit flag, invalid coordinate, or absent measurement produces
no 3D point. The planet and host records remain discoverable.

For each host, choose the first planet by name with a complete valid astrometry
tuple; otherwise the first planet by name. Keep the tuple intact rather than
combining coordinates and distances from different rows. Record the source planet,
retrieval timestamp, frame parameters and Astropy version on the host. All planet
source rows remain available to inspect differences.

- `x_pc,y_pc,z_pc`: Sun-centred Galactic Cartesian. +x toward Galactic centre,
  +y toward Galactic longitude 90 degrees, +z Galactic north.
- `galactocentric_*_pc`: Astropy Galactocentric, explicitly configured with
  centre ICRS RA 266.4051 degrees, Dec -28.936175 degrees; Sun–centre distance
  8122 pc, Sun height 20.8 pc, roll 0 degrees.
- Frontend converts `(x,y,z)` to `(x,z,-y)` to retain handedness with north up.
- One parsec = 3.261563777 light-years for displayed distances.

Positions are catalogue locations, without proper-motion propagation. Distance
uncertainties retain the archive's signed lower/upper errors. They are displayed,
not converted into a full 3D covariance model. The map's disk outline is explicitly
schematic; it is not a measured spiral-arm map or an unbiased planet census.

## API compatibility and delivery

Solar-system `objects`, counts, search and ephemerides are unchanged. The new
schema is v4; v3 databases still serve existing routes and report the exoplanet
list/map unavailable. New counts appear under `row_counts` in the download manifest.
The map returns at most 10,000 hosts with explicit truncation and coverage counts.

Deployment order (requires environment authorization):

1. Deploy backend code and dependencies, including Astropy.
2. Build/verify/publish the v4 catalogue and allow the API host to pull it.
3. Verify `/api/v1/exoplanets`, host detail and `/api/v1/galaxy`.
4. Deploy the website including its Vite build. It displays an unavailable panel
   if the backend list/map endpoints are not ready. Clear stale catalogue caches
   through the existing deployment procedure if required.

Local review can use the offline fixture build. A full exoplanet-only ingest into
an offline solar-system fixture was also checked: 6366 planets, 4775 hosts, 4747
mapped and 28 unmapped, about 13 seconds on the development Mac. This does not
represent a full online solar-system rebuild or a production deployment.

## Verification

Run the normal repository checks. New tests cover real offline archive rows,
reimports/removals, missing/invalid/limited distances, independent Galactic-axis
landmarks, host tuple selection, injection-safe filtering, pagination and REST/MCP
consistency. Website tests cover filters, missing/unavailable responses, escaped
source text, uncertainty/limit formatting, map payload delivery and coordinate
conversion. Browser review uses the complete measured-host snapshot locally.
