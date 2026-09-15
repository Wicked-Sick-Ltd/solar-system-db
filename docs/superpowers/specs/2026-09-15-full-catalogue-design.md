# Full catalogue — ingest everything, ship the database

**Status:** design agreed in conversation 2026-09-15 (Craig: full catalogue, DB out of git, DB offered as a download, resources not a constraint); spec awaiting review
**Repos:** `solar-system-db` (schema, pipeline, API, MCP, download), `solar-system-web` (surfacing)
**Supersedes:** the v1 "curated subset" scope in `README.md` (Out of scope for v1) and `scripts/populate_initial.py`'s H-magnitude caps.

## 1. Goal

The product is "the public solar-system data, complete, behind an API and an MCP server, and downloadable as one file". So:

- Every body JPL's Small-Body Database knows about (~1.4 M asteroids, ~4 k comets), with **every field the bulk query API exposes**, refreshed nightly.
- Every per-object detail the SBDB lookup API adds (discovery circumstances, close approaches, satellites, alternate designations, full-precision elements, radar observations), for **every** body, filled by a continuous crawler rather than a one-shot pass.
- Every named natural satellite with real orbital and physical data from JPL's satellite tables.
- Every value on the NASA planetary fact sheets, including atmospheres.
- Discovery data for every numbered minor planet from the Minor Planet Center, which becomes a first-class source.
- The whole thing published nightly as a compressed SQLite artefact with a manifest, on our own object store, free to download.

## 2. Non-goals

- Artificial satellites, meteor showers, exoplanets, stars other than the Sun.
- Sub-arcsecond ephemerides (unchanged: point at Horizons).
- Serving the 1.4 M-row listing through the website's browse pages as-is; the site stays curated-first with search and deep links (§8).
- Keeping the SQLite file in git. It leaves the repo; git holds code, schema, seed and a small test fixture.

## 3. Sources and what we take from each

| Source | Access | What we take | Cadence |
|---|---|---|---|
| SBDB query API (`sbdb_query.api`) | bulk, paged (`limit` / `limit-from`) | all ~60 fields for every asteroid and comet: identity, orbit elements + sigmas, orbit quality (`condition_code`, `data_arc`, `first_obs`, `last_obs`, `n_obs_used`, `n_del/dop_obs_used`, `rms`, `soln_date`, `producer`, `orbit_id`, `equinox`, `two_body`, `pe_used`, `sb_used`), dynamics (`moid`, `moid_jup`, `t_jup`, `tp`, `n`, `q`, `ad`), physical (`H`, `G`, `GM`, `diameter`, `diameter_sigma`, `extent`, `density`, `rot_per`, `pole`, `albedo`, `BV`, `UB`, `IR`, `spec_B`, `spec_T`, comet `M1/K1/M2/K2`), flags (`neo`, `pha`, `kind`, `class`, `prefix`) | nightly, full |
| SBDB lookup API (`sbdb.api`) | one object per request | `discovery` (who/where/when/citation), `ca_data` (close approaches, all bodies), `sat` (known satellites), `alt_des`, `radar_obs`, `vi_data` (impact-monitoring flag), full-precision `orbit` incl. `model_pars` | continuous crawler (§5.3) |
| JPL close-approach API (`cad.api`) | bulk by date window | all close approaches to any planet within ±200 y for every body: date, nominal/min/max distance, relative velocity, uncertainty | nightly |
| Minor Planet Center `NumberedMPs.txt` (+ `MPCORB.DAT` for cross-check) | one file | discovery date, site, discoverer(s) for every numbered minor planet; official naming citations where present | nightly |
| JPL satellite tables (`ssd.jpl.nasa.gov/sats/phys_par/`, `/sats/elem/`) | two pages | for every named moon: GM, mean radius, density, magnitude, albedo, mean orbital elements (a, e, i, ω, Ω, M, period) with reference epochs | weekly |
| NASA planetary fact sheets (index + per-planet + satellite sheets) | curated transcription in seed | every bulk parameter (volume, ellipticity, GM, J2, moment of inertia, V(1,0), irradiance, black-body temperature, magnetic field), every orbital parameter (synodic period, velocities, tropical period, length of day, obliquity), and the atmosphere block (pressure, temperatures, density, scale height, winds, mean molecular weight, composition) | manual, versioned in seed |

## 4. Schema v2 (`schema/schema.sql`, migration-free: rebuilt nightly)

Widen: `orbital_elements` (+ `perihelion_time_jd`, `moid_au`, `moid_jupiter_au`, `tisserand_jupiter`, `condition_code`, `data_arc_days`, `first_obs`, `last_obs`, `n_obs_used`, `n_delay_obs_used`, `n_doppler_obs_used`, `rms_arcsec`, `solution_date`, `orbit_id`, `producer`, `equinox`, `two_body`, `orbit_class_code`, `orbit_class_name`, `sigma_*` for a, e, i, om, w, ma, n, per, q, ad, tp); `physical_properties` (+ `gm_km3_s2`, `slope_g`, `extent_km`, `pole_ra_dec`, `volume_km3`, `ellipticity`, `moment_of_inertia`, `j2`, `magnetic_field`, `length_of_day_hours`, `synodic_period_days`, `mean_orbital_velocity_km_s`, `min_`/`max_orbital_velocity_km_s`, `tropical_period_days`, `mean_temperature_k`, `black_body_temperature_k`, `solar_irradiance_w_m2`); `visual_properties` (+ `colour_u_b`, `colour_i_r`, `spectral_type_smass`, `spectral_type_tholen`, `magnitude_v10`, comet `m1`, `k1`, `m2`, `k2`). `spectral_type` is dropped; the orbit class moves to `orbit_class_*`.

New tables: `close_approaches` (object_id, body, cd_jd, cd_iso, dist_au, dist_min_au, dist_max_au, v_rel_km_s, v_inf_km_s, sigma_t_min, orbit_ref, source), `discoveries` (object_id, discovered_on, discoverer, site, location, citation, reference, source), `designations` (object_id, designation, kind: 'provisional'|'alternate'|'number', source), `atmospheres` (object_id, surface_pressure_bar, temperature_k, temperature_note, density_kg_m3, scale_height_km, mean_molecular_weight, wind_note, composition JSON, source), `radar_observations` (object_id, epoch, type, reference), `impact_monitoring` (object_id, flagged, source, retrieved_at), `enrichment_state` (object_id, lookup_at, lookup_status, priority). FTS5 virtual table `objects_fts(name, designation, alt_designations)` kept in sync by the build. Indexes on `absolute_magnitude_h`, `semi_major_axis_au`, `orbit_class_code`, `moid_au`, `diameter`, and the NEO/PHA classifications.

`sources` gains per-field provenance for every value that came from a different source than its row (e.g. an MPC discovery on an SBDB object).

## 5. Pipeline

### 5.1 Where it runs
A dedicated build LXC/VM in the CML datacenter (proposed: proxmox5, VLAN 5, 4 vCPU, 8 GB RAM, 40 GB disk), which is on the same network as the Ceph RGW at `s3.wickedsick.com`. It runs the nightly job from a systemd timer inside the repo's `builder` container. The public API host does not build anything any more.

### 5.2 Nightly full build (`scripts/build_full.py`, replaces `populate_initial.py` + `update_nightly.py`)
1. Fresh SQLite at a scratch path (`SSDB_BUILD_PATH`), schema applied, `PRAGMA journal_mode=OFF, synchronous=OFF` for the load.
2. Seed: Sun, planets, dwarf planets, curated moons, rings, fact-sheet bulk parameters and atmospheres.
3. JPL satellite tables → every named moon's physical and orbital rows.
4. SBDB bulk: asteroids then comets, `limit=50000`, `limit-from` paging, all fields, `full-prec=true`; rows land via `executemany` in transactions of 50 k. Idempotent ids (`asteroid-<spkid>`, `comet-<spkid>`), existing id scheme for curated bodies preserved.
5. MPC numbered file → `discoveries` and `designations` for every numbered body (joined on number).
6. Close-approach API, window −200 y … +200 y, all bodies, paged by date → `close_approaches`.
7. Merge the crawler's enrichment store (§5.3) into the fresh DB.
8. Rebuild FTS, `ANALYZE`, `VACUUM`, `verify.py` (extended: row-count floors per type, spot checks against fixtures, no orbit class in `spectral_type`).
9. Publish (§6). Total budget: under 45 minutes; the SBDB pull is ~30 requests of ~40 MB.

### 5.3 Continuous enrichment crawler (`scripts/enrich_crawler.py`)
A long-running service on the build box that walks `enrichment_state` in priority order (planets' moons → dwarf planets → PHAs → NEOs → numbered → unnumbered, then by staleness), calling the SBDB lookup API at a fixed polite rate (default 1 request/s, configurable; JPL asks for reasonable use, not a published quota) and writing results to its own SQLite store `enrichment.sqlite`. A full pass over 1.4 M bodies takes ~16 days at 1 rps; the store survives nightly rebuilds and is merged in at step 7, so completeness climbs monotonically and never regresses. If JPL rate-limits (HTTP 429/503), it backs off exponentially and logs. The crawler is the only part of the system that is not "done" after one night, and the manifest reports its coverage honestly (`enriched_objects / total_objects`).

### 5.4 Fixture for tests
`tests/fixtures/sbdb_sample.jsonl` (2 000 rows spanning every kind/class) and `tests/fixtures/cad_sample.json`, `mpc_sample.txt`, `sats_sample.html`. `build_full.py --offline` builds a complete small DB from seed + fixtures; the API/MCP tests run against it. CI needs no network.

## 6. Publishing and download

- Artefact: `solar_system-YYYYMMDD.sqlite.zst` (zstd level 9) + `solar_system-YYYYMMDD.sqlite.zst.sha256`, uploaded with `aws s3 cp` to bucket `solar-system-db` on `s3.wickedsick.com` (public-read objects, private bucket listing), plus `latest.json` (`{url, size_bytes, sha256, built_at, build_id, counts_by_type, enrichment_coverage, schema_version, licence}`) and a rolling 30-day retention.
- Host update: `scripts/pull_latest.sh` becomes "fetch `latest.json`, download if sha differs, verify, decompress to a temp file, atomic rename over the live file, `docker compose restart`". Cron every 15 min.
- Public surface: `GET /api/v1/download` returns the manifest; the landing page (`web/index.html`) gets a Download section (size, date, checksum, licence, `sqlite3` one-liner); MCP tool `get_download_info`.
- Licence text shipped with the artefact: NASA/JPL public domain, MPC free-use-with-attribution, our own compilation under MIT/CC0 (Craig to confirm which).

## 7. API and MCP changes

- `get_object` returns every new column and, as nested blocks, `discovery`, `designations`, `close_approaches` (next 10 + count), `atmosphere`, `satellites`, `radar_observations`, `impact_monitoring`.
- New endpoints: `GET /api/v1/objects/{id}/close-approaches?from&to&body`, `GET /api/v1/close-approaches?from&to&body&max_dist_au` (bulk by window), `GET /api/v1/objects/{id}/discovery`, `GET /api/v1/objects/{id}/designations`, `GET /api/v1/planets/{id}/atmosphere`, `GET /api/v1/download`.
- `find_objects`: keyset pagination (`after=<id>`, `limit ≤ 1000`), new filters (`orbit_class`, `max_moid_au`, `min_diameter_km`, `condition_code_max`, `discovered_after`), `total` reported from a cached count.
- `search` uses FTS5 with prefix matching; designation forms normalised (`2024 YR4`, `2024YR4`, `K24Y04R`).
- MCP tools mirror one-to-one; descriptions updated for scale ("1.4 million bodies; page with `after`").
- Rate limits unchanged; responses over 1 MB stream.

## 8. Website (`solar-system-web`)

- Object page cards: **Orbit quality** (condition code explained in words, data arc, observations, last observed), **Close approaches** (next five, with Earth distance in lunar distances), **Discovery** (sentence + citation), **Atmosphere** (planets), **Also known as**. Moons table gains mass, density, albedo.
- Browse pages stay curated (named / bright / classified) with a "search all 1.4 million" entry that hits FTS; deep links `/objects/asteroid-<spkid>` work for everything.
- Sitemap: curated set only (already), plus a `sitemap-index` if the curated count passes 50 k.
- The `/download` link in the footer and About page. Numbers in copy ("15,546 objects") come from `/stats`, never hard-coded.

## 9. Operations

- Build box provisioning documented in `docs/BUILD-HOST.md`; secrets (RGW keys) via `op://` references; nothing in git.
- Monitoring: the build posts a summary to the fleet hub / Slack (success, counts, duration, coverage); the API host alerts if `latest.json` is older than 36 h.
- GitHub Actions: the nightly-refresh workflow is retired; CI runs offline tests only. (Workflow edits need a token with `workflow` scope; Craig applies that change.)
- Rollback: `pull_latest.sh --version YYYYMMDD` re-pins a previous artefact.

## 10. Delivery order (each PR independently shippable)

1. Schema v2 + fixtures + offline build (`--offline`) + tests green. Fixes the spectral-type bug on day one.
2. SBDB full pull + MPC + close approaches in `build_full.py`; verify extended.
3. Satellite tables + full fact-sheet seed + atmospheres.
4. Publish/download: RGW upload, manifest, `pull_latest.sh` rewrite, `/download`, landing page. Build host provisioned.
5. Enrichment crawler service + merge step + coverage in manifest.
6. API/MCP: new endpoints, pagination, FTS search.
7. Web: cards, moons table, search-all, download links.
8. Retire the git-committed DB and the Actions nightly job; README rewrite (hosting requirements, sources table, MPC attribution).

## 11. Open questions for Craig
- Compilation licence for our own artefact: MIT (like the code) or CC0 (data-friendly)?
- RGW account for the `solar-system-db` bucket: existing `wizmedia` (as the web app's OG cache uses) or a new one?
- Crawler rate: 1 rps default; say if you'd rather start slower.
