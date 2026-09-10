# solar-system-db

> A queryable catalogue of known solar-system objects exposed as a REST API and
> MCP server for AI agents — for astronomy, science education, sci-fi
> worldbuilding, and modelling. **Not for astrology.**

~15,500 objects: all 8 planets, every known planetary moon (named), all 5 IAU
dwarf planets + leading candidates, named asteroids, NEOs, PHAs, Jupiter
Trojans, Hildas, Centaurs, bright TNOs, every numbered/named comet, and
planetary ring systems. Sourced from NASA/JPL and the IAU Minor Planet Centre,
refreshed nightly.

## Three ways to use it

1. **Clone and query locally.** The SQLite file `data/solar_system.sqlite` is
   committed to the repo — open it with `sqlite3`, DBeaver, DuckDB, Python's
   stdlib, R, Datasette (if you self-host it), or whatever you like.
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
│   └── positions.py        two-body Kepler propagation
├── scripts/
│   ├── populate_initial.py  full rebuild from JPL/MPC
│   ├── update_nightly.py    incremental refresh (run by GitHub Actions)
│   ├── verify.py            sanity checks; CI fails if these don't pass
│   ├── pull_latest.sh       host-side: git-pull + restart services
│   ├── common.py            shared HTTP / DB helpers
│   ├── seed_major.py        curated facts for sun/planets/dwarfs/major moons
│   └── seed_moons.py        the full named-moon list
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
├── data/solar_system.sqlite  the actual catalogue (committed)
└── .github/workflows/
    ├── nightly-refresh.yml  scheduled: 03:00 UTC daily
    └── test.yml             CI on push/PR
```

## Install & rebuild

```bash
git clone https://github.com/wizzouk2/solar-system-db.git
cd solar-system-db

# Editable install of the root package (data_access + positions + scripts)
pip install -e .

# To rebuild data/solar_system.sqlite from scratch (5–10 min, hits JPL SBDB)
python scripts/populate_initial.py

# Verify the rebuild
python scripts/verify.py
```

Or with [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run python
scripts/populate_initial.py`.

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

**Minimal box requirements:** 1 CPU, 512 MB RAM, 1 GB disk. The whole stack
sits idle most of the time; the DB is 12 MB.

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

### Nightly cron coordination

```
GitHub Actions @ 03:00 UTC     →   opens/updates a PR with a refreshed
                                    data/solar_system.sqlite
After merge                    →   scripts/pull_latest.sh
                                    git pull && docker compose restart rest-api mcp-server
```

Add the host cron once:

```bash
echo "15 3 * * * cd /opt/solar-system-db && ./scripts/pull_latest.sh >> /var/log/solar-pull.log 2>&1" \
  | crontab -
```

### What's NOT included by default

- **No auth.** Public is public; both interfaces are read-only by design. The
  REST API has no write endpoints and the DB is opened with `mode=ro&immutable=1`.
- **No analytics.** If you want plausible.io or similar, drop a tag in
  `web/index.html` — that's a 2-line change.
- **No public SQL.** Datasette was considered and dropped because it exposes
  arbitrary SQL over HTTP by default. The REST API is the contract; Laravel
  or any other front-end can be built against the OpenAPI spec.

## Data sources & licensing

All upstream sources are public-domain or freely redistributable:

| Source | What we use | Licence |
|---|---|---|
| [NASA JPL Solar System Dynamics](https://ssd.jpl.nasa.gov/) | Planet/moon facts | NASA public domain |
| [JPL Small-Body Database](https://ssd-api.jpl.nasa.gov/doc/sbdb.html) | Asteroids, comets, TNOs, orbital elements | NASA public domain |
| [NASA Planetary Fact Sheets](https://nssdc.gsfc.nasa.gov/planetary/factsheet/) | Physical properties | NASA public domain |
| [IAU Minor Planet Center](https://www.minorplanetcenter.net/) | Named-asteroid + periodic-comet lists (via SBDB) | Free use with attribution |

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
sources                  provenance per (object, table, field)
build_meta               one row per refresh
```

Plus views: `v_planets`, `v_moons_by_planet`, `v_dwarf_planets`, `v_neos`,
`v_phas`, `v_comets`, `v_tnos`, `v_object_counts`.

## Out of scope for v1

- The full 1.4M asteroid catalogue (we cap at named + bright + classified).
- Artificial satellites.
- Meteor showers.
- The Oort cloud as such.
- Sub-arcsecond precision ephemerides (use JPL Horizons directly).

## TBC

- A handful of confirmed satellites with provisional designations only
  (S/2003 J 18, S/2021 N 1, …) are tracked by name but lack rich orbital /
  physical data; the curated set covers everything an end-user typically wants.
- Comet `M1` (absolute magnitude) is missing for a fraction of long-period
  comets — SBDB returns null there.

## Contributing

PRs welcome — especially for: backfilling moon orbital data, adding canned
queries to the REST API, or a dashboard / planetarium widget that consumes
the API.
