# solar-system-db MCP server

An MCP server that exposes the `solar-system-db` SQLite catalogue as a set of
typed tools for AI agents. **For astronomy, not astrology** — no horoscopes,
houses, transits, natal charts, or aspects. Every tool here is grounded in
observational data from NASA/JPL and the IAU Minor Planet Center.

## Install

From the repo root:

```bash
pip install -e .
pip install -e ./mcp-server
```

There is no `uv.lock`; `uv sync` is not a supported path. With
[uv](https://docs.astral.sh/uv/), use `uv pip install -e .` then
`uv pip install -e ./mcp-server` to match CI.

Make sure `data/solar_system.sqlite` exists — it isn't committed to git.
From the repo root, either fetch the published nightly artefact:

```bash
MANIFEST_URL=https://download.sol.wickedsick.com/latest.json ./scripts/pull_latest.sh
```

or build a small offline one (no network, ~1 second):

```bash
python scripts/build_full.py --fresh --offline
```

or the full catalogue online (~1.5 h, hits JPL/MPC — see `docs/BUILD-HOST.md`):

```bash
python scripts/build_full.py --fresh --online
```

Point `SOLAR_DB_PATH` at whichever file you built if it isn't at the default
`data/solar_system.sqlite`.

## Run

```bash
# Local clients (Claude Desktop, Cursor, Continue): stdio transport
python mcp-server/server.py

# Remote / network clients: streamable HTTP on port 8002
python mcp-server/server.py --transport http --port 8002

# Legacy SSE
python mcp-server/server.py --transport sse
```

## Claude Desktop config

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(or the equivalent location on your OS):

```json
{
  "mcpServers": {
    "solar-system-db": {
      "command": "python",
      "args": ["/absolute/path/to/solar-system-db/mcp-server/server.py"],
      "env": {
        "SOLAR_DB_PATH": "/absolute/path/to/solar-system-db/data/solar_system.sqlite"
      }
    }
  }
}
```

Restart Claude Desktop. Type `/` in the chat to confirm the server registered.

## Tools

### Catalog (data-first)

| Tool | One-liner |
|---|---|
| `find_objects` | Flexible filter: type, parent, size, orbit class/quality, discovery date, NEO/PHA, named-only, and keyset pagination (`after`); up to 1000 rows. |
| `get_object` | Full record for one object — orbital + physical + visual + sources. |
| `list_moons` | All moons of a given planet or dwarf planet. |
| `list_dwarf_planets` | The 5 IAU dwarf planets (+ candidates with `include_candidates=True`). |
| `list_neos` | Near-Earth Objects, filterable by diameter. |
| `list_periodic_comets` | Numbered comets (P < 200 y); configurable limit up to 2000. |
| `list_tnos` | Trans-Neptunian objects + centaurs; configurable limit up to 2000. |
| `list_meteor_showers` | IAU Meteor Data Center showers, optionally filtered by establishment status or activity date. |
| `get_meteor_shower` | One IAU meteor shower by 3-letter code or name — all parameter sets, plus parent comet/asteroid. |
| `get_rings` | Known rings of a given planet. |
| `search` | Fuzzy text search across names / designations / discoverers. |

### Meteor shower parameters

The `list_meteor_showers` tool accepts three optional parameters:

- **`active_on`** — ISO date string (YYYY-MM-DD); keeps only showers whose peak solar longitude is within ±15° of the Sun's longitude on that date (approximate; the MDC provides peak times only).
- **`established_only`** — Boolean (default false); keeps only IAU MDC status codes 1 (single established shower or group) and 6 (member of an established group), excluding code 2 ("to be established") and the working list.
- **`limit`** — Integer, clamped to 1000 (default 200); maximum rows returned.

### Position / ephemeris

| Tool | One-liner |
|---|---|
| `compute_position` | Heliocentric ecliptic (x, y, z) at a given date by two-body Kepler propagation. |
| `get_sky_position` | Where it appears in Earth's sky: RA/Dec, constellation, hemisphere, elongation; with `lat`/`lon` also alt/az, up-after-dark and rise/transit/set. |
| `next_perihelion` | Next perihelion passage of a periodic body. |

### Reference / discovery

| Tool | One-liner |
|---|---|
| `list_object_types` | Object-type tally — what the catalogue contains. |
| `get_sources` | Upstream sources and last-retrieved timestamps. |
| `get_schema` | Full SQLite DDL — useful for writing your own queries. |
| `get_stats` | Total counts, last build timestamp. |

### Resources

| URI | Purpose |
|---|---|
| `solar-system://schema` | SQLite schema as a resource. |
| `solar-system://catalog-stats` | Catalogue counts + last-refresh timestamp (JSON). |

## Examples

```text
> find me all asteroids larger than 200 km radius with eccentricity < 0.1
[uses find_objects(object_type="asteroid", min_radius_km=200, max_eccentricity=0.1)]

> what moons does Saturn have?
[uses list_moons("Saturn")]

> tell me about the Perseids meteor shower
[uses get_meteor_shower("Perseids")]

> tell me everything you know about Comet Halley
[uses get_object("1P/Halley")]

> where is Pluto on 2030-01-01?
[uses compute_position("Pluto", "2030-01-01")]
```

## Data sources

Meteor shower data is sourced from the **IAU Meteor Data Center** (Jenniskens et al. 2020; Hajdukova & Rudawska). See the root `README.md` for the full list of upstream sources and licensing.

## Precision note

The position tools use two-body Kepler propagation — accurate to ~0.1% for the
major planets over decades, less accurate for highly-perturbed minor bodies.
For arcsecond precision and close-approach work, use the
[JPL Horizons API](https://ssd.jpl.nasa.gov/horizons/) directly.

## Tests

```bash
pytest mcp-server/tests/ api/tests/ --import-mode=importlib
```

Smoke tests confirm each tool family is wired up and the shared data-access
layer returns expected counts. Not 100% coverage by design — they're the
breakage-canary, not the spec.
