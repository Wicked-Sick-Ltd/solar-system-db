# Build host runbook

The catalogue is rebuilt from scratch every night on `llm1` and published to
Cloudflare R2; the public API host only ever downloads.

## Build host (llm1)
The build host is the server `llm1`, running under the user `wizzo` as a Docker
Compose `build` profile driven by systemd **user** units (no root, no system-wide
services). Install:

```bash
mkdir -p ~/deploy/solar-data   # no sudo: the deploy clone and data live under the user's home (14 TB volume on llm1)
git clone https://github.com/Wicked-Sick-Ltd/solar-system-db ~/deploy/solar-system-db
mkdir -p ~/.config/systemd/user && cp ~/deploy/solar-system-db/build/systemd/*.{service,timer} ~/.config/systemd/user/
loginctl enable-linger wizzo     # timers survive logout; may need sudo if polkit refuses
systemctl --user daemon-reload && systemctl --user enable --now solar-build.timer solar-crawler.service
systemctl --user list-timers solar-build.timer
```

The raw build is ~2.5 GB; keep headroom for two builds plus the crawler store and
scratch under `~/deploy/solar-data` (mounted into the containers via `SOLAR_DATA_DIR`,
default `~/deploy/solar-data` on llm1 vs. `./data` in dev).

## Object storage (Cloudflare R2)
- Bucket `solar-system-db` on Cloudflare R2, endpoint
  `https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com`.
- Public base URL: `https://download.sol.wickedsick.com` (Cloudflare-fronted custom
  domain; the bucket itself is not public).
- Region is `auto` (`S3_REGION=auto`, `AWS_DEFAULT_REGION` derived from it).
- **No ACLs are ever sent** — R2 doesn't support per-object ACLs and errors on
  them. `publish_artifact.py` is invoked with `S3_NO_ACL=1` (`--no-acl`) on this
  host; do not remove that.
- 1Password item `CLOUDFLARE_SOL_R2_API_TOKEN` (vault Shared-Secrets) holds an
  R2 API token scoped to Object Read & Write on this one bucket only.
- Retention: the publisher prunes dated artefacts older than 30 days; `latest.json`
  always points at the newest.

## Secrets
`op` on llm1 authenticates as a service account. `solar-build.service` runs
`build/run_build.sh`, which `.`-sources `%h/.config/op/op-service-account.env`
(the same file `~/.bashrc` sources) and then `exec`s `op run`. It is **not**
loaded with `EnvironmentFile=`: that file is written for shells
(`export OP_SERVICE_ACCOUNT_TOKEN=…`), and systemd rejects the `export` prefix —
worse, it logs the rejected line, token included, to the journal, and `op`
then starts with no credentials. That is how the 2026-09-18 and 2026-09-19
nightlies failed within a second of starting. `solar-crawler.service` needs no
R2 credentials — it only sets `CRAWLER_RPS` — so it does not run under `op run`.
The R2 credentials themselves never touch disk as plaintext — `build/.env.op`
holds only `op://` references and is injected at run time:

```bash
op run --env-file build/.env.op -- docker compose --profile build run --rm builder
```

The builder image `COPY`s the code in (build/Dockerfile), so `run_build.sh` passes `--build`: a
nightly always runs the code in `~/deploy/solar-system-db` at 03:00. Pulling main there is
enough; no manual `docker compose build` step. (Without `--build`, 2026-09-18→20 ran an image
from 09-17.) The crawler image is rebuilt the same way only when you restart
`solar-crawler.service` after `docker compose --profile build build crawler`.

Prove the token path resolves without running a build:

```bash
sh -c '. ~/.config/op/op-service-account.env; export OP_SERVICE_ACCOUNT_TOKEN; cd ~/deploy/solar-system-db && op run --env-file build/.env.op -- sh -c "echo AWS_ACCESS_KEY_ID=\${AWS_ACCESS_KEY_ID:+resolved}"'
```

## Nightly build (systemd user timer)
`build/systemd/solar-build.service` + `build/systemd/solar-build.timer` (installed
above into `~/.config/systemd/user/`):
```
[Service]
Type=oneshot
WorkingDirectory=%h/deploy/solar-system-db
Environment=SOLAR_DATA_DIR=%h/deploy/solar-data
ExecStart=/bin/sh %h/deploy/solar-system-db/build/run_build.sh
ExecStopPost=/bin/sh -c 'python3 %h/deploy/solar-system-db/build/mcp_notify.py || true'
```
After changing a unit in the repo, re-install it — the copies under
`~/.config/systemd/user/` are plain files, not symlinks:
```bash
cp ~/deploy/solar-system-db/build/systemd/solar-build.service ~/.config/systemd/user/ \
  && systemctl --user daemon-reload && systemd-analyze --user verify ~/.config/systemd/user/solar-build.service
```
Timer: `OnCalendar=*-*-* 03:00:00 UTC`, `Persistent=true` (catches up after a missed
run), `RandomizedDelaySec=300`. Budget: ~40 min bulk + ~10 min compress/upload. The
build writes to the `build_scratch` volume and only publishes after `verify.py`
passes.

## Crawler (always on)
Runs as the `solar-crawler.service` systemd user unit (enabled alongside the timer
above), not started by hand:
```bash
systemctl --user status solar-crawler.service
```
Single-threaded at `CRAWLER_RPS` (default 1.0). Raise it only after agreeing a rate
with JPL for our static IP range. The store `~/deploy/solar-data/enrichment.sqlite` (the
host path on llm1; `/data/enrichment.sqlite` inside the container) is merged into
every nightly build (`--enrichment-store`), so coverage never regresses.

## API host
The API host is `php01`, running `solar-api` as a systemd unit from the checkout
`/home/wizzo/solar-system-db`, with the live catalogue **outside** the checkout at
`/home/wizzo/solar-data/` (`DATA_DIR`; moved there 2026-09-18 after PR #15 untracked
the database). `deploy/php01/README.md` is the runbook: layout, the single cron
line and how to repair it, the env file, the committed unit template
(`deploy/php01/solar-api.service`) and its restore procedure, and the history of
the two cutovers. The pull logs to `/home/wizzo/solar-pull.log` (rather than
`/var/log`) because cron runs there as the unprivileged `wizzo` user — a
deliberate deviation from the spec's `/var/log` path.

Rollback: `./scripts/pull_latest.sh --version YYYYMMDD` with the env file
sourced, e.g. `set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --version 20260914`.

## Monitoring
- The publisher prints a JSON summary; `solar-build.service`'s `ExecStopPost` runs
  `build/mcp_notify.py` to relay it to the fleet board (best-effort — piped
  through `|| true` so a missing or failing notifier never fails the build).
  `ExecStopPost` also runs when the build fails; systemd hands it
  `SERVICE_RESULT`/`EXIT_STATUS`, and the notifier posts a "nightly FAILED"
  notice instead of re-posting the previous summary. Two silent nights
  (2026-09-18/19) went unnoticed under the old `ExecStartPost`, which only
  runs after a successful start.
- If the artefact date on `download.sol.wickedsick.com/latest.json` is not
  yesterday's, check `systemctl --user status solar-build.service` on llm1 first.
- On the API host, alert if `/home/wizzo/solar-data/latest.json`'s `built_at` is older than 36 h.
