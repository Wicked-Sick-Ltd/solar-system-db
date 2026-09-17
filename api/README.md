# solar-system-db REST API

Read-only HTTP/JSON API over the `solar-system-db` SQLite catalogue. Mirrors
the MCP server's tools so REST clients and AI agents see the same data.

OpenAPI spec at `/openapi.json`; Swagger UI at `/docs`; ReDoc at `/redoc`.

## Run locally

```bash
pip install -e .
pip install -e ./api
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
| GET | `/api/v1/objects` | Flexible object filter (type, parent, size, eccentricity, NEO/PHA, named-only) |
| GET | `/api/v1/objects/{name_or_designation}` | Full record for one object |
| GET | `/api/v1/planets/{name}/moons` | All moons of a planet/dwarf planet |
| GET | `/api/v1/planets/{name}/rings` | All known rings of a planet |
| GET | `/api/v1/dwarf-planets?include_candidates=…` | IAU dwarf planets (+ candidates) |
| GET | `/api/v1/neos?min_diameter_km=…&max_diameter_km=…` | Near-Earth Objects |
| GET | `/api/v1/comets/periodic` | Numbered periodic comets |
| GET | `/api/v1/tnos` | Trans-Neptunian objects + centaurs |
| GET | `/api/v1/meteor-showers?established_only=…&active_on=…&limit=…` | IAU Meteor Data Center showers |
| GET | `/api/v1/meteor-showers/{code}` | One IAU meteor shower by code or name |
| GET | `/api/v1/search?q=…` | Fuzzy search across names/designations |
| GET | `/api/v1/positions/{name}?date=YYYY-MM-DD` | Heliocentric position (two-body Kepler) |
| GET | `/api/v1/sky/{name}?date=…&lat=…&lon=…` | Where it appears in Earth's sky: RA/Dec (J2000), constellation, hemisphere, elongation; with `lat`+`lon` also alt/az, up-after-dark and rise/transit/set |
| GET | `/api/v1/perihelion/{name}` | Next perihelion (JD) |
| GET | `/api/v1/object-types` | Object types and counts |
| GET | `/api/v1/sources` | Upstream data sources + timestamps |
| GET | `/api/v1/schema` | SQLite schema DDL |
| GET | `/api/v1/stats` | Catalogue stats |

## Rate limits

Default: **60 req/min and 1000 req/day per IP** (via `slowapi`). Cloudflare
does the heavy lifting in front of public deployments; this is defence in
depth. Adjust the `@limiter.limit("60/minute")` decorators in `api/main.py`
if you need different limits.

## Data sources

Meteor shower data is sourced from the **IAU Meteor Data Center** (Jenniskels et al. 2020; Hajdukova & Rudawska). See the root `README.md` for the full list of upstream sources and licensing.

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
