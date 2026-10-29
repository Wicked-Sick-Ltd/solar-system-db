# solar-system-db REST API

Read-only HTTP/JSON API over the `solar-system-db` SQLite catalogue. Many
endpoints call the same `solar_db` data-access methods the MCP server uses,
but REST and MCP are separate surfaces and can diverge; they are not a
generated 1:1 contract. A later PR is intended to pin that contract.

OpenAPI spec at `/openapi.json`; Swagger UI at `/docs`; ReDoc at `/redoc`.

## Run locally

```bash
pip install -e .
pip install -e ./api
pip install pytest httpx   # or pip install -e '.[dev]' from the repo root (CI)
python api/main.py
# → http://localhost:8003/docs
```

Or via `uvicorn` directly:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8003
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/objects` | Flexible object filter (type, parent, size, eccentricity, NEO/PHA, named-only, orbit class, MOID, …) |
| GET | `/api/v1/objects/{name_or_designation}` | Full record for one object |
| GET | `/api/v1/objects/{name_or_designation}/close-approaches` | Close approaches of one body to the planets / Moon |
| GET | `/api/v1/objects/{name_or_designation}/discovery` | Discovery circumstances |
| GET | `/api/v1/objects/{name_or_designation}/designations` | Numbers, names, provisional and alternate designations |
| GET | `/api/v1/planets/{name}/moons` | All moons of a planet/dwarf planet |
| GET | `/api/v1/planets/{name}/rings` | All known rings of a planet |
| GET | `/api/v1/planets/{name}/atmosphere` | Atmosphere from the NASA fact sheet |
| GET | `/api/v1/dwarf-planets?include_candidates=…` | IAU dwarf planets (+ candidates) |
| GET | `/api/v1/neos?min_diameter_km=…&max_diameter_km=…` | Near-Earth Objects |
| GET | `/api/v1/comets/periodic` | Numbered periodic comets |
| GET | `/api/v1/tnos` | Trans-Neptunian objects + centaurs |
| GET | `/api/v1/close-approaches?from=…&to=…` | Close approaches to a body in a date window |
| GET | `/api/v1/meteor-showers?established_only=…&active_on=…&limit=…` | IAU Meteor Data Center showers |
| GET | `/api/v1/meteor-showers/{code}` | One IAU meteor shower by code or name |
| GET | `/api/v1/search?q=…` | Fuzzy search across names/designations |
| GET | `/api/v1/positions/{name}?date=YYYY-MM-DD` | Heliocentric position (two-body Kepler) |
| GET | `/api/v1/sky/{name}?date=…&lat=…&lon=…` | Where it appears in Earth's sky: RA/Dec (J2000), constellation, hemisphere, elongation; with `lat`+`lon` also alt/az, up-after-dark and rise/transit/set |
| GET | `/api/v1/perihelion/{name}` | Next perihelion (JD) |
| GET | `/api/v1/download` | Manifest for the published whole-database artefact |
| GET | `/api/v1/object-types` | Object types and counts |
| GET | `/api/v1/sources` | Upstream data sources + timestamps |
| GET | `/api/v1/schema` | SQLite schema DDL |
| GET | `/api/v1/stats` | Catalogue stats |
| GET | `/healthz` | Liveness probe (not in the OpenAPI schema) |

### Meteor shower filters

The `/api/v1/meteor-showers` endpoint accepts three optional query parameters:

- **`active_on`** — ISO date string (YYYY-MM-DD); keeps only showers whose peak solar longitude is within ±15° of the Sun's longitude on that date (approximate; the MDC provides peak times only).
- **`established_only`** — Boolean (default false); keeps only IAU MDC status codes 1 (single established shower or group) and 6 (member of an established group), excluding code 2 ("to be established") and the working list.
- **`limit`** — Integer, clamped to max 1000 (default 500); maximum rows returned.

Many `submitted_on` values are the MDC placeholder `"SD"` rather than a real
date, and `reference` may contain raw HTML anchors (`<A href='…'>…</A>`) as
supplied by the source document — neither is cleaned up by this API.

## Rate limits and API keys

Catalogue routes are tiered. Send the key in the **`X-PU-Api-Key`** request
header (not a query parameter, so it does not land in access logs). Every
response on `/api/` includes **`X-PU-Key-Tier`**: `anonymous`, `free`, or
`institution`. Cloudflare's edge exempts a request that merely carries
`X-PU-Api-Key`. Because that exemption is presence-only, this app treats a
missing, blank, unknown, or revoked key as anonymous traffic: the per-IP
anonymous limit applies, the tier header is `anonymous`, and the call is not
rejected with 401. Unknown and revoked keys are also counted against a
separate per-IP invalid-key limit so a client cannot spray guesses.

| Tier | Who | Default budget |
| --- | --- | --- |
| `anonymous` | No key, or a missing / invalid / revoked key | 60/minute and 1,000/day **per IP** |
| `free` | Valid free key | 300/minute and 10,000/day **per key** |
| `institution` | Valid institution key | 5,000/minute and **100,000/day per key** |

The institution daily ceiling is `PU_INSTITUTION_PER_DAY` (default `100000`),
pending a final decision. Cloudflare Pro still caps every caller, keyed or
not, at 300 requests per minute per IP. The application budgets above that
are reachable only after an edge allowlist. Egress CIDRs for that allowlist
are collected on https://publicuniverse.net/api-access. A 429 includes
`Retry-After`, a `Link` header, and
`api_access_url` pointing at `PU_API_ACCESS_URL`
(default `https://publicuniverse.net/api-access`).

Keys are stored as SHA-256 hashes (`PU_API_KEY_PEPPER` is mixed in when set).
Nothing in git is a live key.

```bash
# prints the secret once; the database keeps only the hash
PU_API_KEYS_PATH=/var/lib/solar-system-db/api_keys.sqlite \
  python api/fair_use.py add --tier free --label "example lab"
python api/fair_use.py revoke --id <id>
python api/fair_use.py list
```

`PU_API_KEYS` may be a JSON list of `{"key","tier","label","id"}` objects.
On startup those values are hashed into the database. Leave it unset in
shared environments. A bad payload is ignored and the process still starts.

Requests that hit this process on loopback **with no forwarding headers and
no key** are the co-located website (`API_BASE_URL=http://127.0.0.1:8003`).
They are not counted. php01's nginx is that loopback peer: forwarding
headers are trusted only then (plus addresses in `PU_TRUSTED_PROXIES`).
`CF-Connecting-IP` wins, then `X-Real-IP`, then the rightmost untrusted
`X-Forwarded-For` hop. An untrusted peer cannot pick its bucket by sending
those headers. Forward `CF-Connecting-IP` from Cloudflare:

```nginx
proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

Night planning stays **10/minute** and target discovery **5/minute**, per
socket address, via `slowapi`. Those strings live in `solar_db/http_quotas.py`
and the MCP HTTP server applies the same numbers to its observing tools.
MCP itself is not on these API key tiers.

| Variable | Default | Role |
| --- | --- | --- |
| `PU_API_KEYS_PATH` | `data/api_keys.sqlite` | Hash store and counters. Do not commit it. |
| `PU_API_KEY_PEPPER` | empty | Extra hash input. Set in production. Changing it invalidates existing hashes. |
| `PU_API_KEYS` | unset | Optional JSON bootstrap. No-op when unset. |
| `PU_ANON_PER_MINUTE` / `PU_ANON_PER_DAY` | 60 / 1000 | Anonymous per-IP budget. |
| `PU_FREE_PER_MINUTE` / `PU_FREE_PER_DAY` | 300 / 10000 | Free-key budget. |
| `PU_INSTITUTION_PER_MINUTE` / `PU_INSTITUTION_PER_DAY` | 5000 / **100000** | Institution budget. Daily default is pending review. |
| `PU_INVALID_KEY_PER_MINUTE` | 30 | Per-IP cap on unknown or revoked keys. |
| `PU_API_ACCESS_URL` | `https://publicuniverse.net/api-access` | Link returned with HTTP 429. |
| `PU_TRUSTED_PROXIES` | loopback only | Extra proxy addresses allowed to supply a client IP. |

## Data sources

Meteor shower data is sourced from the **IAU Meteor Data Center** (Jenniskens et al. 2020; Hajdukova & Rudawska). See the root `README.md` for the full list of upstream sources and licensing.

## Read-only by design

There are no `POST`/`PUT`/`DELETE` endpoints. The DB is opened with the
SQLite URI `mode=ro&immutable=1` flag, so a write attempt would fail even
if a route bug introduced one.

## Examples

```bash
# Saturn's moons
curl https://solar.example.com/api/v1/planets/Saturn/moons | jq

# Geminids meteor shower by 3-letter code
curl 'https://solar.example.com/api/v1/meteor-showers/GEM' | jq

# Earth's position on 2030-01-01
curl 'https://solar.example.com/api/v1/positions/Earth?date=2030-01-01' | jq

# Where is Jupiter in the sky tonight from London?
curl 'https://solar.example.com/api/v1/sky/Jupiter?date=2026-09-15T21:00:00Z&lat=51.5&lon=-0.12' | jq

# Search for Halley
curl 'https://solar.example.com/api/v1/search?q=Halley' | jq
```

## Exoplanets (schema v4)

`GET /api/v1/exoplanets` accepts `q` (literal planet/host substring),
`discovery_method` (exact archive label), `max_distance_pc` (finite, positive),
`limit` (1–1000, default 50), and `offset` (0–100000). Returns `available`,
`results`, `total`, `limit`, `offset`, `has_more`.

`GET /api/v1/exoplanets/{name}` and `/api/v1/exoplanet-hosts/{name}` accept
names or stable IDs and return 404 when missing. Planet records retain the
selected upstream columns in `source_data`, including references, asymmetric
errors and limit flags. Host detail includes its planets and coordinate metadata.

`GET /api/v1/galaxy` accepts `max_distance_pc` and `limit` (1–10000). It returns
one marker per measured host, coordinate frame metadata, total/mapped/unmapped
host counts, matching count and a `truncated` flag. All positions/distances use
parsecs. Missing or limited distances are never plotted. Older catalogues return
`available: false` for list/map endpoints. See [coordinate conventions](../docs/EXOPLANETS.md).
