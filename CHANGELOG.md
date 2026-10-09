# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-09

First tagged release of the catalogue behind Public Universe (publicuniverse.net).
It covers everything on `main` from the initial commit (1 June 2026) up to
`ae20912e64d786d4cf26d57f2ec45d5323bc5471` (PR #48, 9 October 2026).

### Added

- Initial SQLite catalogue (schema v1), FastAPI REST API, FastMCP server and
  Docker/Caddy deployment stack (initial commit `ad68b49`).
- Sky positions: geocentric RA/Dec, IAU constellation, elongation and optional
  observer altitude/azimuth with rise/transit/set, via `GET /api/v1/sky/{name}`
  and the MCP `get_sky_position` tool (#5).
- Full catalogue, schema v2: all-field JPL SBDB ingest, MPC discovery
  circumstances, JPL close approaches, designations, FTS5 search and a single
  `scripts/build_full.py` builder with an offline fixture build (#6).
- Every JPL natural satellite (about 460) plus complete NASA planetary fact
  sheets and atmospheres (#7).
- Polite long-running SBDB enrichment crawler with tiered refresh, back-off and a
  separate enrichment store merged at build time with per-field provenance (#8).
- Keyset pagination and new filters on `GET /api/v1/objects`; detail routes for
  close approaches, discovery, designations and planet atmospheres; bulk
  `/close-approaches`; `/download` manifest; matching MCP tools (#9).
- Artefact publishing (zstd SQLite, SHA-256 sidecar, `latest.json` manifest) and
  an API-host pull script with verification, atomic swap and rollback (#10, #13).
- IAU Meteor Data Center meteor showers (schema v3) with parent-body links,
  REST routes and MCP tools (#16).
- Confirmed exoplanets and host systems from the NASA Exoplanet Archive
  (PSCompPars), schema v4, with measured 3D host positions for a galaxy map,
  REST routes (`/exoplanets`, `/exoplanet-hosts/{name}`, `/galaxy`) and MCP
  tools (#30).
- Observing backend, consolidated from #31 to #45 (#46):
  - offline Moon and planet night planning, `GET`/`POST /api/v1/observing/night`
    and MCP `plan_observing_night` (#33);
  - bounded starter catalogues (bright stars, double stars, Messier deep sky
    objects) with verified coordinate frames and reference epochs, via
    `/api/v1/starter-targets` (#34, #36);
  - optional checksum-pinned JPL ephemeris provider (#35);
  - selected hours and user horizon masks (#37);
  - planning for catalogue directions with provenance (#39);
  - catalogue identity and build provenance, `GET /api/v1/catalogue` and MCP
    `get_catalogue_identity` (#40);
  - algorithm identity and retained replay checks for observing results (#41);
  - exoplanet responses bound to catalogue snapshots (#42);
  - opt-in, rate-limited target discovery, `POST /api/v1/observing/discover`
    and MCP `discover_observing_targets` (#43);
  - explained selected-interval constraints (#44).
- Reproducible full-catalogue identity finalisation benchmark, with a retained
  run against the 2 October 2026 catalogue of 1,574,019 objects (#45).
- `mass_kg` on `GET /api/v1/close-approaches` rows, an additive field (#48).
- MIT licence, contributing guide, code of conduct, security policy, issue and
  PR templates (#4), and shared agent guidance files (#24).

### Changed

- The catalogue database is no longer committed to git. It is built nightly on
  the `llm1` build host, published to Cloudflare R2 and pulled by the API host
  (#11, #13, #15). The GitHub Actions nightly refresh workflow was removed by a
  direct commit (`9320bdf`) and the unused incremental refresh path retired (#19).
- MCP `find_objects` and list tools now accept the same filters and limits as
  REST (#20).
- Documentation corrected to match reality: organisation URLs, CI install path,
  live API routes and agent rules (#21); php01 runbook rewritten with the
  committed `solar-api` unit template (#17); design notes and rollout plans
  recorded (#12, #14).
- Object listing uses indexed filters and keyset pagination for faster reads
  (#38, shipped in #46).
- MCP Python SDK raised to `>=1.27.1`, and all 33 MCP tools now declare
  read-only, non-destructive, idempotent annotations (#47).

### Fixed

- Dwarf planets now have orbital elements, and Pluto no longer resolves to
  asteroid (999) Zachia in SBDB (#3).
- Orbit class was stored as `spectral_type`; taxonomy and orbit class now have
  separate columns (#6).
- MPC "Name Ref." numbers no longer leak into discovery site names (#15).
- Nightly build on `llm1` failing silently because of how the secrets file was
  loaded; failures are now reported to the fleet board (#18).
- Transient JPL 502 errors no longer abort the nightly build, thanks to jittered
  exponential back-off (#25).
- The nightly build always rebuilds its image, so it runs current code rather
  than a stale image (#28).
- Packaged API console entry point repaired and CI extras aligned (#22).
- Seed leftovers cleaned up, including Hyperion being counted twice (#23).
- Catalogue filter semantics preserved across schema versions and bounds (#31).
- Sky timestamps normalised; Earth's Moon is reported unavailable instead of
  being given a fabricated position (#32).
- Nightly refresh publishing on protected `main` (#1), later superseded by the
  build host pipeline.

### Security

- Least-privilege `contents: read` permissions on the test workflow (#2).
- Local artefact writes and deletes confined to a canonical path boundary,
  rejecting traversal and symlink escapes (Semgrep High findings) (#26).
- Dynamic SQL replaced with bound parameters and fixed query maps across the
  data layer (Semgrep High findings) (#27, #29).
- API and MCP containers run as an unprivileged `app` user (#27).
- Public MCP HTTP service gains explicit Host and Origin allowlists with
  DNS-rebinding protection kept on (#47).

### Known limitations

- The public MCP endpoint (`/mcp`) is not yet reachable in production. #47 adds
  the code and optional deployment templates only; activation waits for the
  publicuniverse.net domain and Cloudflare WAF work.
- Exoplanet support (#30) is merged and wired into the nightly build, but the
  catalogue published by the `llm1` nightly on 9 October 2026 (built 05:25 BST)
  still reports schema v3 with no exoplanet tables. The build host needs to pick
  up current `main` before the downloadable database includes exoplanets.
- publicuniverse.net API and download hostnames are proposals. Current
  addresses (`api.sol.wickedsick.com`, `download.sol.wickedsick.com`) remain in
  use, and the repository keeps its `solar-system-db` name.
- Sky and position endpoints use two-body propagation, accurate to about one
  degree. Night planning is an explicitly labelled approximation, and the JPL
  provider is optional and not provisioned by default.
- Target discovery screens the packaged starter sample only (157 of 158 rows,
  plus the Moon and planets). It is not an all-sky search.
- Exoplanet IDs are derived from archive names, so an archive rename produces a
  new ID. Alias reconciliation is future work.
- `mass_kg` on close approaches will usually be empty, because few NEOs have a
  measured mass.
- Artificial satellites (CelesTrak) are designed but not implemented.

### Ops

- PRs #31 to #44 were closed as superseded by the consolidated #46. A separate
  proposal for #38 was deliberately excluded.
- The `nightly-refresh` branch contains a committed SQLite database. Do not
  merge or revive it.
- #18 notes that the shared `llm1` service-account token was written to the
  build host's user journal; rotation was left as an owner decision. #14 notes
  that the R2 token should be scoped to the single bucket.

[0.1.0]: https://github.com/Wicked-Sick-Ltd/solar-system-db/commits/ae20912e64d786d4cf26d57f2ec45d5323bc5471
