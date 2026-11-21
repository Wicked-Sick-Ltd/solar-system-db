<!-- PORTFOLIO_STATE:START company=Wicked Sick hash=e8c78c23 last_sync=2026-11-19T10:05Z -->
## Portfolio context (auto-generated — do not edit between the markers)

Repo company: **Wicked Sick**. Full inventory at `wicked-repo-inventory/PORTFOLIO_STATE.md` (canonical in Notion → Engineering Inventory).

**Secrets:** API credentials live in 1Password (Wicked Sick account), never in committed files. Map env vars to `op://vault/item/field` references in a committed `.env.op` (paths only, no secret values) and run via `op run --env-file=.env.op -- <cmd>` so secrets exist only for that process. Don't hardcode keys or commit real values.

**Reusable drop-ins** (use these before building from scratch):
- `acsendr-billing` — Provider-agnostic subscription billing with idempotent webhooks, a two-sided revenue-share ledger, payouts, and a reconciliation invariant that proves every penny is allocated.
- `acsendr-conformance-suite` — A language-neutral behavioural contract with a thin runner, so a rewrite in another stack can be held to the same assertions instead of hand-translating them and letting them drift.
- `acsendr-connector-economics` — A registry of third-party data connectors recording what each one actually costs to ship — verification regime, lead time, money, and blockers.
- `acsendr-elicitation` — Elicit a person's decision-making identity — behavioural-event questionnaire, public-domain Big Five, and reconciliation against their own written record.
- `acsendr-intake-contract` — One normalised document shape every ingest source produces, plus a deterministic quality gate that runs before anything is paid for.
- `acsendr-mailbox-import` — Mailbox and transcript ingestion with sent-vs-received voice attribution — mbox, .eml, IMAP and VTT/SRT, no OAuth app and no vendor review.

**Other Wicked Sick repos:**
- `HubSpot Order Management` — Bi-directional sync between HubSpot and order systems — scoped, not yet built.
- `pfSense P2P Link` — Point-to-point ISP link with routed /26 block (188.213.95.128/26 over 100.66.66.0/30 transit).
- `Self-hosted UniFi Controller` — UniFi Network Controller running on Proxmox instead of cloud.
- `wicked-amiga/amiga-projects` — —
- `wicked-amiga/battle-isle-remake` — —
- `wicked-amiga/deuteros-app` — Deuteros mobile port (Godot 4) — strategy & licensing stage
- `wicked-amiga/deuteros-parallel` — Parallel Godot port of Deuteros (1991), companion to Deuteros Resurrected
- `wicked-amiga/supremacy-remake` — Source-derived Supremacy reconstruction, extraction tools, and original-CPU validation evidence
- _... +122 more — see /Github/PORTFOLIO_STATE.md_
<!-- PORTFOLIO_STATE:END -->

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
(`pip install -e '.[dev,publish]'` plus nested `./mcp-server` and `./api`,
and the `zstd` CLI so publish/pull tests run):

```bash
python -m ruff check --select F401 api/main.py
python scripts/verify.py
pytest mcp-server/tests/ api/tests/ --import-mode=importlib
```

That is the path to document and to run before opening a PR. `[dev]` provides
pytest, httpx, and ruff; `[all]` is still not what CI installs.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):
`fix(data): …`, `feat(api): …`, `docs: …`, `chore(refresh): …`.

Branch from `main`; open PRs against `main`. Human-facing setup and data-
correction rules live in [CONTRIBUTING.md](CONTRIBUTING.md).

## REST vs MCP

REST (`api/main.py`) and MCP (`mcp-server/server.py`) both use `solar_db`,
but they are not a generated contract and can diverge. Do not claim they
cannot drift.

## Cloud agent environment

Cursor Cloud Agents bootstrap this repo with `bash .cursor/install.sh`
(`.cursor/environment.json`). The script is idempotent and non-interactive.
It creates `.venv`, installs the same packages as
`.github/workflows/test.yml` (`[dev,publish,observing-jpl]` plus
`./mcp-server` and `./api`), and installs the `zstd` CLI when it is missing.

Tests and the API use SQLite. No Docker database is required. When
`data/solar_system.sqlite` is absent, install builds the offline fixture
(`python scripts/build_full.py --fresh --offline --no-vacuum`). It does not
run a JPL refresh. `start` only checks that the fixture and venv exist. The
`api` terminal runs `python api/main.py` on port 8003 (`GET /healthz`).

Nightly publish to object storage reads `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` from the environment. Install prints a warning naming
each variable when it is unset, then continues. Local tests and the API do
not need them. Do not commit `.env` files or secret values.
