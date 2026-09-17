# solar-system-db

> A queryable catalogue of known solar-system objects exposed as a REST API and
> MCP server for AI agents — for astronomy, science education, sci-fi
> worldbuilding, and modelling. **Not for astrology.**

About 1.57 million objects at the first full build (16 September 2026) — live
figure at `/api/v1/stats`: every body in JPL's Small-Body Database (asteroids,
comets, TNOs, Centaurs, NEOs, PHAs), all 8 planets, every known planetary
moon, all 5 IAU dwarf planets, and planetary ring systems. Sourced from
NASA/JPL and the IAU Minor Planet Centre, refreshed nightly on the build host
(`llm1`) and published to Cloudflare R2.

## Three ways to use it

1. **Download and query locally.** The whole catalogue is one SQLite file,
   published nightly (see *Download the whole database* below) — open it with
   `sqlite3`, DBeaver, DuckDB, Python's stdlib, R, Datasette (if you self-host
   it), or whatever you like.
2. **Run the MCP server locally.** A FastMCP server exposes the catalogue as
   typed tools for Claude Desktop, Cursor, Continue, and any other MCP-aware
   AI client (stdio or streamable HTTP).
3. **Query a public instance.** When self-hosted, the bundled Caddy + FastAPI
   + MCP-HTTP stack gives you a public REST/JSON API with OpenAPI docs at
   `/docs` and an MCP HTTP endpoint at `/mcp`. **No direct SQL access is
   exposed publicly** — the REST API is the contract.

## What's in the box

```
solar-system-db/
├── README.md               this file
├── pyproject.toml          one Python project; extras [mcp] / [api] / [all]
├── schema/schema.sql       the SQLite schema (single source of truth)
├── solar_db/               shared data-access layer (used by MCP + REST)
│   ├── data_access.py      read-only SQLite wrapper, all query methods
│   ├── positions.py        two-body Kepler propagation
│   ├── sky.py              RA/Dec, constellation, alt/az, rise/set from those positions
│   └── data/               vendored IAU constellation boundaries (CDS VI/42)
├── scripts/
│   ├── build_full.py        full rebuild from JPL/MPC/NASA (--offline uses tests/fixtures)
│   ├── ingest_*.py           per-source ingestion: SBDB, MPC discoveries, JPL CAD,
│   │                         satellites, fact sheets, crawler enrichment
│   ├── enrich_crawler.py    perpetual polite crawler filling per-object SBDB detail
│   ├── publish_artifact.py  compress + upload to R2, write latest.json
│   ├── verify.py            sanity checks; CI's "Verify DB" step; builds the offline
│   │                         fixture and re-verifies when no catalogue file is present
│   ├── pull_latest.sh       API-host side: download + verify + swap + restart
│   └── common.py            shared HTTP / DB helpers
├── build/                  build-host container + systemd units + mcp_notify.py
├── mcp-server/             FastMCP server
│   ├── server.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── tests/test_smoke.py
├── api/                    FastAPI REST front-end
│   ├── main.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── tests/test_smoke.py
├── docker-compose.yml      brings up rest-api + mcp-server + caddy
├── Caddyfile               reverse proxy (TLS, gzip, CORS)
├── web/index.html          public landing page
├── .env.example            copy → .env, set PUBLIC_HOSTNAME
├── data/                    populated by scripts/pull_latest.sh from the published
│                             artefact (or by scripts/build_full.py); not in git
└── .github/workflows/
    └── test.yml             CI on push/PR (nightly-refresh.yml is retired —
                              the real nightly build runs on llm1, see docs/BUILD-HOST.md)
```

## Install & build

```bash
git clone https://github.com/wizzouk2/solar-system-db.git
cd solar-system-db

# Editable install of the root package (data_access + positions + scripts)
pip install -e .

# To rebuild the whole catalogue from scratch (~1.4 M bodies; see docs/BUILD-HOST.md)
python scripts/build_full.py --fresh --online

# Verify the rebuild
python scripts/verify.py
```

Or with [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run python
scripts/build_full.py --fresh --offline` (fixtures, no network).

## Query locally (no server needed)

```bash
sqlite3 data/solar_system.sqlite
sqlite> SELECT name, designation FROM v_dwarf_planets;
sqlite> SELECT name FROM v_moons_by_planet WHERE planet='Jupiter' LIMIT 10;
sqlite> SELECT * FROM v_planets;
```

From Python:

```python
from solar_db import SolarDB
db = SolarDB()
db.list_moons("Saturn")
db.get_object("1P/Halley")
```

From DuckDB:

```sql
INSTALL sqlite; LOAD sqlite;
ATTACH 'data/solar_system.sqlite' AS s (TYPE sqlite);
SELECT object_type, COUNT(*) FROM s.objects GROUP BY 1;
```

## Run the MCP server

```bash
pip install -e .[mcp]
python mcp-server/server.py                       # stdio (Claude Desktop, Cursor)
python mcp-server/server.py --transport http      # streamable HTTP on :8002
```

Claude Desktop config — drop into
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "solar-system-db": {
      "command": "python",
      "args": ["/abs/path/to/solar-system-db/mcp-server/server.py"],
      "env": {
        "SOLAR_DB_PATH": "/abs/path/to/solar-system-db/data/solar_system.sqlite"
      }
    }
  }
}
```

Full tool list and examples: see [`mcp-server/README.md`](mcp-server/README.md).

## Run the REST API

```bash
pip install -e .[api]
python api/main.py
# → http://localhost:8003/docs   (Swagger UI)
# → http://localhost:8003/redoc  (ReDoc)
# → http://localhost:8003/openapi.json
```

Endpoint reference: see [`api/README.md`](api/README.md).

## Hosting it publicly

The full stack — REST API, MCP HTTP server, Caddy reverse proxy, landing page
— comes up with one command:

```bash
cp .env.example .env
$EDITOR .env                          # set PUBLIC_HOSTNAME=solar.example.com
docker compose up -d
```

That brings up:

- `solar-rest-api`  on `:8003` (internal)
- `solar-mcp-server` on `:8002` (internal)
- `solar-caddy`     on `:80/:443`, routing:
  - `/`            → landing page (`web/index.html`)
  - `/docs`, `/redoc`, `/openapi.json` → Swagger / ReDoc / spec
  - `/api/*`       → REST API
  - `/mcp/*`       → MCP HTTP endpoint

**Minimum box requirements:** 2 CPU, 4 GB RAM, 10 GB disk (the uncompressed DB
is ~2.5 GB; keep room for the swap). The **build host** is separate and much
heavier — see `docs/BUILD-HOST.md`.

### Behind Cloudflare Tunnel (Craig's pattern)

If you're using `cloudflared` to expose this without opening any inbound ports,
turn off Caddy's automatic TLS (Cloudflare handles HTTPS) by uncommenting
`auto_https off` in `Caddyfile`, then add this to your tunnel `config.yml`:

```yaml
tunnel: <your-tunnel-uuid>
credentials-file: /etc/cloudflared/<uuid>.json

ingress:
  - hostname: solar.example.com
    service: http://localhost:80
  - service: http_status:404
```

Then `cloudflared tunnel run` (or run it as a systemd service). Cloudflare
handles TLS, caching, rate limits, and DDoS protection at the edge — no
bespoke config needed.

### Build + publish flow

This is how the project's own instance runs it — `llm1` builds, `php01` serves:

```
Build host (llm1), 03:00 UTC   →  systemd timer runs build_full.py --online → verify.py
                                   → publish_artifact.py (zstd + latest.json → Cloudflare R2)
API host (php01), every 15 min →  scripts/pull_latest.sh: new sha? download, verify, swap, restart
```

Full runbooks: [`docs/BUILD-HOST.md`](docs/BUILD-HOST.md) covers the build host
(systemd user units, R2 credentials, the crawler) and
[`deploy/php01/README.md`](deploy/php01/README.md) covers the API host (cron
line, restart command). For your own deployment, set `MANIFEST_URL` in `.env`
and add a cron line following the same pattern:

```bash
echo "*/15 * * * * cd /path/to/solar-system-db && set -a && . ./.env && set +a && ./scripts/pull_latest.sh >> ~/solar-pull.log 2>&1" | crontab -
```

## Data sources & licensing

All upstream sources are public-domain or freely redistributable:

| Source | What we use | Licence |
|---|---|---|
| [NASA JPL Solar System Dynamics](https://ssd.jpl.nasa.gov/) | Planet/moon facts | NASA public domain |
| [JPL Small-Body Database](https://ssd-api.jpl.nasa.gov/doc/sbdb.html) | Asteroids, comets, TNOs, orbital elements | NASA public domain |
| [NASA Planetary Fact Sheets](https://nssdc.gsfc.nasa.gov/planetary/factsheet/) | Physical properties | NASA public domain |
| [JPL CAD API](https://ssd-api.jpl.nasa.gov/doc/cad.html) | Close approaches to planets and the Moon | NASA public domain |
| [JPL planetary satellites](https://ssd.jpl.nasa.gov/sats/elem/) | Elements + physical parameters of every known moon | NASA public domain |
| [IAU Minor Planet Center](https://www.minorplanetcenter.net/iau/lists/NumberedMPs.txt) | Discovery circumstances (date, site, discoverer) for every numbered minor planet | Free dataset, redistributable "as long as the source for the data is clearly specified" (MPC web policy) — attribution: "Data: IAU Minor Planet Center" |
| [IAU Meteor Data Center](https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt) | Meteor shower parameter sets (code, name, activity, radiant, orbital elements, parent bodies) | Free use with attribution: `IAU Meteor Data Center (Jenniskens et al. 2020; Hajdukova & Rudawska)` |
| [CDS catalogue VI/42](https://cdsarc.cds.unistra.fr/ftp/VI/42/) (Roman 1987) | Constellation boundaries for sky lookups | Public domain |

Wikipedia is referenced in `wikipedia_url` columns for human reading; it is
not used as a canonical data source.

This project is MIT-licensed.

## Schema overview

```
objects                  core: id, name, designation, object_type, parent_id, ...
orbital_elements         epoch, a, e, i, Ω, ω, M, period, q, Q
physical_properties      radius, mass, density, rotation, axial tilt, gravity
visual_properties        albedo, H magnitude, B-V, dominant_colour_hex
rings                    parent_id → planets; inner/outer radius, width, thickness
classifications          multi-label: NEO, PHA, Trojan, Hilda, MBA, ...
meteor_showers           IAU MDC shower parameter sets: code, name, activity, radiant, elements, parent body
sources                  provenance per (object, table, field)
build_meta               one row per refresh
```

Plus views: `v_planets`, `v_moons_by_planet`, `v_dwarf_planets`, `v_neos`,
`v_phas`, `v_comets`, `v_tnos`, `v_object_counts`.

## Download the whole database

Every night the build host publishes the complete catalogue as a single
zstd-compressed SQLite file with a manifest:

```bash
curl -s https://download.sol.wickedsick.com/latest.json | jq .   # size, sha256, counts, licence
curl -O "$(curl -s https://download.sol.wickedsick.com/latest.json | jq -r .url)"
zstd -d solar_system-*.sqlite.zst && sqlite3 solar_system-*.sqlite 'SELECT COUNT(*) FROM objects'
```

To use the file you just downloaded: point the API or MCP server at it with
`SOLAR_DB_PATH=$PWD/solar_system-YYYYMMDD.sqlite python api/main.py`, or open
it directly with `sqlite3 solar_system-YYYYMMDD.sqlite` — see *Query locally*
above for canned queries.

The same manifest is served at `GET /api/v1/download` and by the MCP tool
`get_download_info`. Dated artefacts are kept for 30 days.

## What's in it

Everything the public sources publish, refreshed nightly:

- every body in JPL's Small-Body Database (~1.4 M asteroids, ~4 k comets) with
  all 75 query-API fields — elements with uncertainties, orbit quality, MOIDs,
  Tisserand, physical and colour parameters, taxonomies, comet magnitude terms;
- discovery circumstances for every numbered minor planet (Minor Planet Center);
- close approaches to every planet and the Moon, ±200 years (JPL CAD);
- every known natural satellite with planetocentric elements (JPL SSD);
- the complete NASA planetary fact sheets, atmospheres included;
- every IAU Meteor Data Center meteor shower (all parameter sets, status, radiant, velocity, orbit), linked to its parent comet or asteroid where the catalogue has it;
- per-object detail from the SBDB lookup API — citations, alternate
  designations, radar observations, impact-monitoring flags, referenced
  physical parameters — filled by a perpetual polite crawler (tier 1, the
  bodies the website shows, weekly; tier 2, everything else, rolling).

## Non-goals

- Exoplanets, stars other than the Sun.
- Sub-arcsecond precision ephemerides (use JPL Horizons directly).
- Serving the 1.4 M-row listing through the website's browse pages as-is; the
  site stays curated-first with search and deep links.
- Keeping the SQLite file in git — it left the repo in this rollout; git holds
  code, schema, seed data and a small test fixture, not the catalogue itself.

**Coming next:** artificial satellites
(CelesTrak) — see
[`docs/superpowers/specs/2026-09-16-full-catalogue-rollout-and-extensions-design.md`](docs/superpowers/specs/2026-09-16-full-catalogue-rollout-and-extensions-design.md).

## TBC

- A handful of confirmed satellites with provisional designations only
  (S/2003 J 18, S/2021 N 1, …) are tracked by name but lack rich orbital /
  physical data; the curated set covers everything an end-user typically wants.
- Comet `M1` (absolute magnitude) is missing for a fraction of long-period
  comets — SBDB returns null there.

## Contributing

PRs welcome — especially for: data corrections with a primary source,
backfilling moon orbital data, adding canned queries to the REST API, or a
dashboard / planetarium widget that consumes the API. See
[CONTRIBUTING.md](CONTRIBUTING.md) for local setup, the checks CI runs, and
how to propose a data correction. For security problems please follow
[SECURITY.md](SECURITY.md) rather than opening an issue. MIT licensed
([LICENSE](LICENSE)).
