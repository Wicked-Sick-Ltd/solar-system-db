# Full catalogue — PR 1: schema v2, ingest mappers, offline build, fixtures

> **Archived:** this plan has landed; do not execute.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land schema v2 and a new builder that can produce a complete, correct database from fixtures offline (and from JPL/MPC online), so every later PR builds on real structure; fix the orbit-class-in-`spectral_type` bug.

**Architecture:** `schema/schema.sql` v2 is a strict superset of v1 (`PRAGMA user_version = 2`), so new code keeps working against the old committed file until the RGW pipeline replaces it (PR 4). Ingest is split by source into `scripts/ingest_*.py` modules with pure `map_*` functions (row → table dicts) and thin `write_*` functions (executemany). `scripts/build_full.py` orchestrates stages; `--offline` reads `tests/fixtures/`. A root `conftest.py` builds the offline DB once per test session.

**Tech Stack:** Python 3.10+, stdlib sqlite3 (FTS5 available in CPython builds), requests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-full-catalogue-design.md` §3–§5.4

## Global Constraints
- v2 must not drop or rename any v1 column (`spectral_type` stays; it now holds the SMASS taxonomy, never the orbit class).
- Ids: keep `ast-<spkid>[-<slug>]`, `comet-<slug>`, curated `planet-*` / `dwarf-*` / `moon-*`. SBDB rows whose spkid is a curated body (seed `spkid`) write to the curated id; curated physical/visual values win.
- Bulk writes use `executemany` in 50 k-row transactions; `PRAGMA synchronous=OFF, journal_mode=OFF` during build only.
- No network in tests (`--offline`).
- Do not edit `.github/workflows/*` (token lacks `workflow` scope); `update_nightly.py` becomes a no-op shim so the existing Action stays harmless.

---

### Task 1: Schema v2
**Files:** Modify `schema/schema.sql`. Test: `tests/test_schema.py`.
- [ ] Add to `orbital_elements`: `perihelion_time_jd, moid_au, moid_jupiter_au, tisserand_jupiter, condition_code, data_arc_days, first_obs, last_obs, n_obs_used, n_delay_obs_used, n_doppler_obs_used, rms_arcsec, solution_date, orbit_id, producer, orbit_source, equinox, two_body, pe_used, sb_used, orbit_class_code, orbit_class_name, sigma_e, sigma_a, sigma_q, sigma_i, sigma_om, sigma_w, sigma_ma, sigma_tp, sigma_per, sigma_n, sigma_ad`.
- [ ] Add to `physical_properties`: `gm_km3_s2, slope_g, extent_km, pole_ra_dec, diameter_sigma_km, volume_km3, ellipticity, moment_of_inertia, j2, magnetic_field, length_of_day_hours, synodic_period_days, mean_orbital_velocity_km_s, min_orbital_velocity_km_s, max_orbital_velocity_km_s, tropical_period_days, mean_temperature_k, black_body_temperature_k, solar_irradiance_w_m2`.
- [ ] Add to `visual_properties`: `colour_u_b, colour_i_r, spectral_type_tholen, magnitude_v10, comet_m1, comet_k1, comet_m2, comet_k2, nongrav_a1, nongrav_a2, nongrav_a3, nongrav_dt`.
- [ ] New tables `close_approaches`, `discoveries`, `designations`, `atmospheres`, `radar_observations`, `impact_monitoring`, `enrichment_state`; FTS5 `objects_fts(id UNINDEXED, name, designation, alt)`; indexes on H, a, orbit_class_code, moid_au, radius_km; `PRAGMA user_version = 2`.
- [ ] Test: applying the schema to `:memory:` yields `user_version == 2`, every listed column exists, FTS table exists.

### Task 2: `scripts/ingest_sbdb.py`
**Interfaces:** `FIELDS: list[str]` (the 75), `map_row(fields, row, *, curated: dict[int,str]) -> Mapped` where `Mapped = dict(object=..., orbital=..., physical=..., visual=..., classifications=[...], designations=[...], is_curated=bool)`; `iter_bulk(kind: 'a'|'c', page=50000) -> Iterator[list[dict]]` (online, `limit`/`limit-from`); `load_fixture(path) -> list[dict]`; `write_mapped(conn, mapped_iter) -> Counter`.
- [ ] Tests (`tests/test_ingest_sbdb.py`): Ceres row → `orbit_class_code == 'MBA'`, `spectral_type == 'C'`, `spectral_type_tholen == 'G'`, `gm_km3_s2 == 62.6284`, `moid_au == 1.58107`, `condition_code == 0`, `radius_km == 469.7`, designations contains number `1` and provisional `A801 AA`; Pluto row (spkid 20134340) maps to id `dwarf-pluto` with `is_curated=True` and no physical block; a comet row → id `comet-…`, epoch = tp, `comet_m1` set, `orbit_class_code` in {JFc, HTC, …}; `write_mapped` over the fixture writes 1527 asteroids + 400 comets, zero rows with orbit class in `spectral_type`.
- [ ] Implement; commit `feat(ingest): SBDB bulk mapper with all 75 fields`.

### Task 3: `scripts/ingest_mpc.py` and `scripts/ingest_cad.py`
- [ ] MPC: `parse_line(line) -> dict|None` (regex on the fixed-width numbered list: number, name, YYYY MM DD, site, discoverers); `write_discoveries(conn, records)` resolving `number → object_id` via `designations(kind='number')`. Test: line for `(1) Ceres` → `{"number":1,"name":"Ceres","discovered_on":"1801-01-01","site":"Palermo","discoverer":"Piazzi, G."}`; fixture load writes ≥ 1 500 discoveries and sets `objects.discoverer/discovery_date` where null.
- [ ] CAD: `map_row(fields,row) -> dict` (des, orbit_id, jd, cd→ISO, dist, dist_min, dist_max, v_rel, v_inf, t_sigma_f, body, h); `write_close_approaches(conn, rows)` resolving `des → object_id` via `designations` (number or provisional), skipping unknown bodies and counting them. Test: fixture writes > 0 rows; a row for a known fixture body resolves.
- [ ] Commit `feat(ingest): MPC discoveries and JPL close approaches`.

### Task 4: `scripts/ingest_seed.py` + `scripts/build_full.py` + shim + verify
- [ ] Move `write_major_body`/`populate_major_bodies`/rings/moons from `populate_initial.py` into `ingest_seed.py` unchanged; `populate_initial.py` becomes a 5-line shim calling `build_full.main(["--fresh", "--offline"] if --skip-net else …)`; `update_nightly.py` prints "retired: builds now run on the build host (see docs/superpowers/specs/2026-09-15-full-catalogue-design.md)" and exits 0.
- [ ] `build_full.py --fresh --offline|--online [--page 50000] [--skip-cad] [--skip-mpc]`: stages seed → SBDB → designations/FTS → MPC → CAD → enrichment_state tiering → ANALYZE/VACUUM → verify → publish. Records `build_meta` (mode 'full-offline'/'full').
- [ ] `verify.py`: when `user_version >= 2` also check: no `spectral_type` value in the orbit-class code set; every asteroid/comet has `orbit_class_code`; `designations` non-empty; `objects_fts` row count == objects; dwarf planets all have `semi_major_axis_au`.
- [ ] Root `conftest.py`: session fixture builds the offline DB into `tmp_path_factory` and sets `SOLAR_DB_PATH` **before** `api.main` / `mcp-server` import (use `pytest_configure`).
- [ ] Update existing tests that assumed the old data (e.g. `test_get_object_halley_comet`, sky Horizons tests) — they must pass on the fixture DB (Halley 1P is in the comet fixture; planets from seed; Pluto from fixture routed to `dwarf-pluto`).
- [ ] Commit `feat(build): build_full.py with offline fixture mode; retire populate/update scripts`.

### Task 5: `data_access.get_object` nested blocks
- [ ] Add `discovery`, `designations`, `close_approaches` (next 10 by date + `close_approach_count`), `atmosphere`, `radar_observations`, `impact_monitoring` to `get_object`, each guarded by a cached `has_table()` so v1 files still work. Test on the fixture DB: Ceres has a discovery and ≥ 2 designations; a PHA has close approaches.
- [ ] Commit `feat(api): nested discovery/designations/close-approach blocks on get_object`.

## Self-review
Spec §4 columns ✔ T1; §3 SBDB/MPC/CAD ✔ T2/T3; §5.2 stages 1,4,5,6,8 ✔ T4 (satellites/fact sheets = PR 3, enrichment merge = PR 5, publish = PR 4); §5.4 fixtures ✔ T4; bug fix ✔ T2 + verify. No placeholders; names consistent (`map_row`, `write_mapped`, `write_discoveries`, `write_close_approaches`).
