# Full catalogue: rollout deltas, meteor showers, artificial satellites

**Status:** design agreed in conversation 2026-09-16 (Craig: build on llm1 now; publish to Cloudflare R2; add meteor showers and artificial satellites; written plan then execute). Addendum to `2026-09-15-full-catalogue-design.md`, which stays the source of truth for schema v2, the SBDB/MPC/CAD pipeline, the crawler and the API surface.
**Repos:** `solar-system-db` (this), `solar-system-web` (surfacing, later PRs).

## 1. Where things stand (verified 2026-09-16)

- Code for the 2026-09-15 spec is merged: db PRs #6, #7, #8, #9, #10 and web PRs #32, #33, #35. 105 offline tests pass. PR #11 (retire the committed DB) was merged and reverted the same evening because nothing had been published yet.
- The full online build was measured once, on the Mac Pro, on 2026-09-15: 1,568,700 objects in 28 minutes, 2.73 GB raw, 659 MB zstd. JPL reports 1,564,353 asteroids and 4,076 comets on 2026-09-16 and accepts all 75 query fields.
- Nothing is rolled out. No build host runs the nightly, no bucket exists, no publisher credentials exist in 1Password, the crawler has never started. The committed `data/solar_system.sqlite` is v1 (`user_version 0`, 39,154 objects) and frozen since 2026-09-05; the live site has served stale data since then.
- Two 2026-09-15 assumptions do not hold: the intended build host (`proxmox5`) has been offline since 2026-09-10; the public API host is `php01` running `solar-api` as a systemd unit from `/home/wizzo/solar-system-db` on a June checkout, not the compose stack that `pull_latest.sh` restarts by default.
- The Ceph RGW at `s3.wickedsick.com` was provisioned on 2026-06-01 by Project Phalanx Insider A (findings 2026-09-07) and sits behind Cloudflare bot protection. It is not used here.

## 2. Rollout deltas (Phase A)

| Spec §5.1/§6 said | Now | Why |
|---|---|---|
| Build LXC on proxmox5 | **llm1** (112 cores, 376 GB RAM, 4 TB free on `/data`; docker compose v5, `op` service account, `zstd` present). Systemd **user** timer, `docker compose --profile build`. Egress IP `192.33.132.253` for the JPL rate letter. | proxmox5 offline; llm1 idle and already provisioned. Same container, so a later move is config only. |
| Ceph RGW `s3.wickedsick.com`, `aws s3 cp`, `public-read` ACLs | **Cloudflare R2**, Wicked Sick account `4ce32b0dd5d81195ffdef6d24d1a8297` (R2 already enabled: `db-backups`, `forge-backups`, `terraform-state`). Bucket `solar-system-db`, S3 endpoint `https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com`, region `auto`. Public reads through an R2 **custom domain** `download.sol.wickedsick.com` (bucket public access on; the zone must live in the same account, which is checked in Plan A Task 1). **No ACLs**: R2 rejects `x-amz-acl`; the publisher gains `--no-acl` (env `S3_NO_ACL=1`). | Provenance concern on the RGW; R2 has free egress, no bot-fight interference, and is already in use. |
| Credentials | 1Password Shared-Secrets item `CLOUDFLARE_SOL_R2_API_TOKEN` (fields `Access Key ID`, `Secret Access Key`, `S3 API Endpoint`), R2 token scoped **Object Read & Write** on the one bucket. Created by Craig in the dashboard (the API cannot mint R2 S3 tokens). Consumed via `op run --env-file mcp/.env.op`-style file `build/.env.op` in the repo (references only, no secrets). | Same pattern as the coordination hub service. |
| API host update: `docker compose restart rest-api mcp-server` | php01: `RESTART_CMD="sudo systemctl stop solar-api && sudo systemctl start solar-api"`. The sudoers grant lists `stop` and `start` but the `restart` line is malformed (`/usr/bin systemctl restart solar-api`), so restart is not permitted. Cron every 15 min as the user `wizzo`. `MANIFEST_URL=https://download.sol.wickedsick.com/latest.json`, `DATA_DIR=/home/wizzo/solar-system-db/data`. php01 has 194 GB free and 62 GB RAM for the 2.7 GB file. | Match the host as it is. |
| php01 code | `git pull` to `main` and `.venv/bin/pip install -e .[api]` before the first pull, so `data_access` understands v2 and `/api/v1/download` reads `data/latest.json`. | The June checkout predates schema v2. |
| Retire the committed DB (PR #11) | Re-land **after** the first successful publish and pull, as the final Phase A task, with the README rewrite folded in. | Ordering was the only thing wrong with #11. |
| Crawler | Starts on llm1 at 1 rps against the published file at `/data/solar/solar_system.sqlite` (the builder installs it there after a verified publish). Craig emails JPL (`ssd-api` contact) offering the tool and asking for a rate for `192.33.132.253`; `CRAWLER_RPS` rises only after a reply. | Unchanged policy; the host changed. |
| Monitoring | Builder posts its JSON summary to the fleet hub board via `coordctl mesh-send` from `ExecStartPost`; php01 cron logs to `/var/log/solar-pull.log`; the API's `/api/v1/download` manifest `built_at` older than 36 h is the alert condition (checked by the existing spark-style timer pattern on llm1). | As spec §9, on the real hosts. |

Everything else in the 2026-09-15 spec (§3 sources, §4 schema, §5.2 stages, §5.3 crawler, §7 API/MCP, §8 web) is unchanged and already implemented.

## 3. Meteor showers (Phase B)

### 3.1 Source
IAU Meteor Data Center shower database, `https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt`: pipe-delimited, quoted fields, ~73 header comment lines then one row per **parameter set** (a shower can have several sets, `AdNo`). 48 columns; the ones we keep: `LP, IAUNo, AdNo, Code, shower name, activity, s (status), LaSun, Ra, De, dRa, dDe, Vg, a, q, e, peri, node, inc, N, Group, CG, Parent body, Ote, Reference, SD`. Status codes come from the file's own legend (established / working list / removed / pro tempore); the parser reads the legend from the header rather than hard-coding it, and `verify.py` asserts the legend still contains the words "established" and "working". Citation: Jenniskens et al. 2020, Planetary and Space Science 182; Hajdukova and Rudawska (MDC). Refresh weekly (the file changes a few times a year).

### 3.2 Schema (v3, superset of v2, `PRAGMA user_version = 3`)
```
meteor_showers (
  iau_no INTEGER NOT NULL, ad_no INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
  status_code INTEGER, status_label TEXT, activity TEXT,
  solar_longitude_deg REAL, ra_deg REAL, dec_deg REAL, dra_deg_per_day REAL, ddec_deg_per_day REAL,
  vg_km_s REAL, a_au REAL, q_au REAL, e REAL, peri_deg REAL, node_deg REAL, incl_deg REAL,
  n_members INTEGER, shower_group TEXT, parent_body TEXT, parent_object_id TEXT REFERENCES objects(id),
  technique TEXT, reference TEXT, submitted_on TEXT, source TEXT NOT NULL DEFAULT 'IAU MDC',
  PRIMARY KEY (iau_no, ad_no))
idx_showers_code (code), idx_showers_parent (parent_object_id), idx_showers_status (status_code)
```
`parent_object_id` is resolved at build time by matching `parent_body` text against `designations` (comet designation like `1P/Halley` or `109P`, asteroid number like `3200`) and `objects.name`; unmatched stays null and is counted in the build summary.

### 3.3 Build, API, MCP, web
- `scripts/ingest_showers.py`: `parse_showers(text) -> Iterator[dict]`, `resolve_parent(conn, parent_body) -> str|None`, `write_showers(conn, rows) -> dict[str,int]`; fixture `tests/fixtures/mdc_showers.txt` (the real header plus ~40 rows covering every status code and a resolvable comet, asteroid and null parent). `build_full.py` gains `stage_showers` after CAD and `--skip-showers`.
- `data_access`: `list_meteor_showers(status: str|None, established_only: bool)`, `get_meteor_shower(code_or_name)`, and `get_object` gains `meteor_showers` (showers whose parent is this object), guarded by `_has_table`.
- API: `GET /api/v1/meteor-showers?status=established&active_on=YYYY-MM-DD`, `GET /api/v1/meteor-showers/{code}`; MCP tools `list_meteor_showers`, `get_meteor_shower`. `active_on` uses solar longitude: the shower is "active" when the date's solar longitude is within ±15° of `LaSun` (documented as approximate; the MDC gives peak only).
- Web (later PR): `/meteor-showers` index, shower page with radiant and velocity, "Meteor showers" card on parent comet/asteroid pages.
- `verify.py`: `meteor_showers` row count ≥ 100 (established alone is ~110), every row has `code` and `iau_no`, no duplicate (iau_no, ad_no).

## 4. Artificial satellites (Phase C, own plan after A)

### 4.1 Source and licence
CelesTrak GP data, `https://celestrak.org/NORAD/elements/gp.php?GROUP=<g>&FORMAT=json` for the groups `active`, `stations`, `visual`, `weather`, `noaa`, `gnss`, `starlink`, `oneweb`, `geo`, `analyst`, plus the full SATCAT `https://celestrak.org/pub/satcat.csv`. CelesTrak republishes Space-Track's basic SSA data (TLE/OMM, SATCAT, decay) under USSPACECOM's blanket redistribution approval conditional on citation; our artefact credits "CelesTrak (T.S. Kelso) / 18 SDS via Space-Track.org". CelesTrak usage policy: download each file at most once per update cycle (GP every 2 h, SATCAT daily), stop on any non-200 and alert a human, single-threaded. We pull once a day inside the nightly build, and the crawler never touches CelesTrak.

### 4.2 Data model
Kept out of `objects` (the `object_type` CHECK and the id scheme are for natural bodies; the API's `find_objects` and the sky maths assume heliocentric Kepler elements, which TLEs are not). New tables, v3:
```
satellites (norad_cat_id INTEGER PRIMARY KEY, object_id_intl TEXT, name TEXT NOT NULL, object_type TEXT,
  owner TEXT, launch_date TEXT, launch_site TEXT, decay_date TEXT, period_min REAL, inclination_deg REAL,
  apogee_km REAL, perigee_km REAL, rcs TEXT, status_code TEXT, orbit_center TEXT, orbit_type TEXT,
  source TEXT NOT NULL DEFAULT 'CelesTrak SATCAT', updated_at TEXT)
satellite_elements (norad_cat_id INTEGER PRIMARY KEY REFERENCES satellites, epoch TEXT, mean_motion REAL,
  eccentricity REAL, inclination_deg REAL, ra_of_asc_node_deg REAL, arg_of_pericenter_deg REAL,
  mean_anomaly_deg REAL, bstar REAL, mean_motion_dot REAL, mean_motion_ddot REAL, element_set_no INTEGER,
  rev_at_epoch INTEGER, classification TEXT, ephemeris_type INTEGER, tle_line1 TEXT, tle_line2 TEXT,
  gp_group TEXT, source TEXT NOT NULL DEFAULT 'CelesTrak GP', updated_at TEXT)
satellite_groups (norad_cat_id INTEGER, gp_group TEXT, PRIMARY KEY (norad_cat_id, gp_group))
```
SATCAT gives every catalogued object (~100,700 numbers, including decayed); GP elements exist only for the ~30k on orbit that CelesTrak publishes in groups. `objects_fts` is not touched; a separate `satellites_fts(norad_cat_id UNINDEXED, name, object_id_intl)`.

### 4.3 Propagation and API
SGP4 via `sgp4` (Brandon Rhodes) as an optional extra `[sats]`, so `GET /api/v1/satellites/{norad}/position?at=` returns TEME → ECEF → geodetic lat/lon/alt, and `GET /api/v1/satellites/{norad}/passes?lat&lon&from&hours` reuses `solar_db/sky.py` observer maths for alt/az. Listing: `GET /api/v1/satellites?group=&owner=&active=true&after=&limit=`. MCP mirrors. Web: `/satellites` index by group, satellite page with the current ground track point and the next three passes for the visitor's saved location.

### 4.4 Non-goals
Debris conjunction (SOCRATES), classified objects, high-precision ephemerides (point at Space-Track), and any redistribution of Space-Track data we did not get from CelesTrak.

## 5. Delivery order
1. **Plan A** `docs/superpowers/plans/2026-09-16-full-catalogue-rollout.md`: R2 + publisher changes, llm1 builder, first publish, php01 pull, crawler, re-land #11 with README. Unfreezes the live data.
2. **Plan B** `docs/superpowers/plans/2026-09-16-meteor-showers.md`: schema v3 + ingest + API/MCP; web PR after.
3. **Plan C** written once A is live: artificial satellites per §4.

## 6. Decisions taken by default (Craig to override if wrong)
- Public download hostname `download.sol.wickedsick.com`.
- Nightly at 03:00 UTC on llm1; crawler always on at 1 rps until JPL replies.
- Retention 30 days of dated artefacts in R2 (unchanged).
- Meteor-shower "active" window ±15° of solar longitude.
- Satellite positions computed on request, never stored.
