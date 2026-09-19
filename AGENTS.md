# Agent notes for solar-system-db

This catalogue is **astronomy, not astrology**. Do not add horoscopes, houses,
transits, natal charts, aspects, or other astrology features.

## Catalogue is not in git

`data/solar_system.sqlite` is generated and unpublished from version control.
Do not commit it. Local data comes from:

- curated seeds: `scripts/seed_major.py` and `scripts/seed_moons.py`
- ingest stages under `scripts/ingest_*.py`, orchestrated by `scripts/build_full.py`
- or the published nightly artefact via `scripts/pull_latest.sh`

CI builds a small offline fixture when no catalogue file is present
(`python scripts/verify.py` does that).

## Checks CI runs

From the repo root, after the same install as `.github/workflows/test.yml`
(`pip install -e .`, then nested `./mcp-server` and `./api`, then
`pytest` and `httpx`):

```bash
python scripts/verify.py
pytest mcp-server/tests/ api/tests/ --import-mode=importlib
```

That is the path to document and to run before opening a PR. Optional extras
such as `[all]` are not what CI installs.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):
`fix(data): …`, `feat(api): …`, `docs: …`, `chore(refresh): …`.

Branch from `main`; open PRs against `main`. Human-facing setup and data-
correction rules live in [CONTRIBUTING.md](CONTRIBUTING.md).

## REST vs MCP

REST (`api/main.py`) and MCP (`mcp-server/server.py`) both use `solar_db`,
but they are not a generated contract and can diverge. Do not claim they
cannot drift.
