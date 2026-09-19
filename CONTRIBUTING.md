# Contributing to solar-system-db

Thanks for taking an interest. The most useful contributions here are **data
corrections with a source**, then improvements to the refresh scripts, REST
API and MCP server. This is an astronomy project, not astrology; contributions
adding astrology features will be closed.

## Local setup

Python 3.10+.

```bash
git clone https://github.com/Wicked-Sick-Ltd/solar-system-db.git
cd solar-system-db
sudo apt-get install zstd
pip install -e '.[dev,publish]' -e ./api -e ./mcp-server
```

This is the same dependency set CI uses: `[dev]` provides pytest, httpx, and
ruff; `[publish]` plus the `zstd` CLI exercises the publish/pull tests.

The catalogue is **not** committed to git — get it one of these ways:

```bash
# Fetch the published nightly artefact (fastest; ~1.5-2.5 GB download)
MANIFEST_URL=https://download.sol.wickedsick.com/latest.json ./scripts/pull_latest.sh

# Or build a small offline one from tests/fixtures (no network, ~1 second)
python scripts/build_full.py --fresh --offline

# Or build the full catalogue online (~1.5 h; hits JPL/MPC — see docs/BUILD-HOST.md)
python scripts/build_full.py --fresh --online
```

Then point `SOLAR_DB_PATH` at whichever file you ended up with (default is
`data/solar_system.sqlite`, which is where the two commands above put it) and
run the API:

```bash
python api/main.py             # http://localhost:8003/docs
```

### Rebuilding the database

```bash
python scripts/build_full.py --fresh --offline   # offline fixtures, ~1 second
python scripts/build_full.py --fresh --online    # full: hits JPL/MPC, ~1.5 h
python scripts/verify.py                         # sanity checks; CI runs this too
```

The offline build writes the Sun, planets, dwarf planets, curated moons and
rings from `scripts/seed_major.py` and `scripts/seed_moons.py`, plus a small
fixture-backed slice of asteroids/comets/TNOs/MPC discoveries/close
approaches from `tests/fixtures/`. The online build additionally pulls the
full JPL Small-Body Database (~1.4 M asteroids, ~4 k comets), MPC discovery
circumstances for every numbered minor planet, JPL close approaches, and
every known planetary satellite.

## Before you open a PR

CI runs these, so run them locally first:

```bash
python -m ruff check --select F401 api/main.py
python scripts/verify.py
pytest mcp-server/tests/ api/tests/ -v --import-mode=importlib
```

## Proposing a data correction

Every value in the catalogue is traceable to a source, and we want to keep it
that way. When a number is wrong:

1. **Cite a primary source.** NASA/JPL (Solar System Dynamics, the Small-Body
   Database, the Planetary Fact Sheets), the IAU Minor Planet Center, or a
   peer-reviewed paper. Wikipedia is fine as a pointer to one of those, not as
   the source itself.
2. **Work out where the value comes from**, because that decides what to
   change:
   - **Curated bodies** — Sun, planets, IAU dwarf planets and candidates, the
     major moons — live in `scripts/seed_major.py` (and the full moon list in
     `scripts/seed_moons.py`). Edit the seed value and cite the source in the
     PR.
   - **Everything pulled from JPL SBDB** — asteroids, comets, TNOs, centaurs,
     and the dwarf planets' *orbital elements* — is fetched by the ingestion
     stages driven by `scripts/build_full.py` (see `scripts/ingest_sbdb.py`,
     `scripts/ingest_mpc.py`, `scripts/ingest_cad.py`). If SBDB itself is
     right and we're mapping it wrongly, fix the mapping. If SBDB is wrong,
     report it to JPL; we don't override upstream values by hand.
   - The `sources` table records which source fed which table for each object,
     so `SELECT * FROM sources WHERE object_id = '…'` tells you which case
     you're in.
3. **Don't hand-edit the catalogue file.** It isn't in git — it's rebuilt
   nightly on the build host (`llm1`) from the seeds and the ingestion
   stages, published to Cloudflare R2, and pulled down by the API host every
   15 minutes (see `docs/BUILD-HOST.md`). A hand edit only affects your own
   local copy and is silently replaced by the next pull or rebuild. For a
   seed change (`scripts/seed_major.py` / `scripts/seed_moons.py`), the PR
   only needs the code change — the next nightly build picks it up.
4. **Add or extend a check in `scripts/verify.py`** when the bug was the kind
   that could come back (a missing block, an implausible magnitude). CI fails
   on verify, which is how we keep regressions out.

Not sure which case applies? Open a *Data correction* issue with the object,
the current value, the proposed value and the source URL, and we'll route it.

## Branches, commits, PRs

- Branch from `main`; open PRs against `main`.
- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `fix(data): …`, `feat(api): …`, `chore(refresh): …`.
- Fill in the PR template, including the data-source line for any value
  change.
- CI must be green.

## Where things live

- `schema/schema.sql` — the schema, single source of truth.
- `solar_db/data_access.py` — every read query; both the API and MCP server
  go through it. Add new queries here, not in the servers.
- `solar_db/positions.py` — two-body Kepler propagation used by `/positions`.
- `api/main.py` and `mcp-server/server.py` — thin layers over `solar_db`.

## Questions and security

Questions: open an issue, or email hello@wickedsick.com. Security problems:
see [SECURITY.md](SECURITY.md) — please don't file those publicly.
