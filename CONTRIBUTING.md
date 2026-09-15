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
pip install -e .[all]          # data layer + API + MCP extras
pip install -e ./api -e ./mcp-server
pip install pytest httpx
```

The catalogue (`data/solar_system.sqlite`) is committed, so you can run the
API straight away:

```bash
python api/main.py             # http://localhost:8003/docs
```

### Rebuilding the database

```bash
python scripts/populate_initial.py --skip-net   # offline: curated seed only, seconds
python scripts/populate_initial.py              # full: hits JPL SBDB, 5–10 min
python scripts/verify.py                        # sanity checks; CI runs this too
```

`--skip-net` writes the Sun, planets, dwarf planets, curated moons and rings
from `scripts/seed_major.py` and `scripts/seed_moons.py` only. Everything
else (asteroids, comets, TNOs, and the dwarf planets' orbital elements) comes
from the network stages.

## Before you open a PR

CI runs these, so run them locally first:

```bash
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
     and the dwarf planets' *orbital elements* — is fetched by the stages in
     `scripts/populate_initial.py` and refreshed by
     `scripts/update_nightly.py`. If SBDB itself is right and we're mapping it
     wrongly, fix the mapping. If SBDB is wrong, report it to JPL; we don't
     override upstream values by hand.
   - The `sources` table records which source fed which table for each object,
     so `SELECT * FROM sources WHERE object_id = '…'` tells you which case
     you're in.
3. **Don't hand-edit `data/solar_system.sqlite`.** It is regenerated from the
   seeds and the network stages; a hand edit would be silently overwritten by
   the next rebuild or by the nightly-refresh workflow
   (`.github/workflows/nightly-refresh.yml`), which opens its own PR with a
   refreshed database. For a seed change, either include the regenerated
   database in your PR or say in the PR that it needs a rebuild and we'll do
   it.
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
- CI must be green. Automated nightly-refresh PRs are reviewed like any other.

## Where things live

- `schema/schema.sql` — the schema, single source of truth.
- `solar_db/data_access.py` — every read query; both the API and MCP server
  go through it. Add new queries here, not in the servers.
- `solar_db/positions.py` — two-body Kepler propagation used by `/positions`.
- `api/main.py` and `mcp-server/server.py` — thin layers over `solar_db`.

## Questions and security

Questions: open an issue, or email hello@wickedsick.com. Security problems:
see [SECURITY.md](SECURITY.md) — please don't file those publicly.
