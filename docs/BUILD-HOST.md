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
`op` on llm1 authenticates as a service account: `wizzo`'s `solar-build.service`
systemd user unit loads `OP_SERVICE_ACCOUNT_TOKEN` from
`%h/.config/op/op-service-account.env` (`EnvironmentFile=`). `solar-crawler.service`
needs no R2 credentials — it only sets `CRAWLER_RPS` — so it does not run under
`op run` and carries no `EnvironmentFile=`.
The R2 credentials themselves never touch disk as plaintext — `build/.env.op`
holds only `op://` references and is injected at run time:

```bash
op run --env-file build/.env.op -- docker compose --profile build run --rm builder
```

## Nightly build (systemd user timer)
`build/systemd/solar-build.service` + `build/systemd/solar-build.timer` (installed
above into `~/.config/systemd/user/`):
```
[Service]
Type=oneshot
WorkingDirectory=%h/deploy/solar-system-db
Environment=SOLAR_DATA_DIR=%h/deploy/solar-data
EnvironmentFile=%h/.config/op/op-service-account.env
ExecStart=/usr/bin/op run --env-file=%h/deploy/solar-system-db/build/.env.op -- /usr/bin/docker compose --profile build run --rm builder
ExecStartPost=/bin/sh -c 'python3 %h/deploy/solar-system-db/build/mcp_notify.py || true'
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
The API host is `php01`, running `solar-api` as a systemd unit from
`/home/wizzo/solar-system-db`. The full one-time procedure and the cron line
live in `deploy/php01/README.md`. The pull logs to `/home/wizzo/solar-pull.log`
(rather than `/var/log`) because cron runs there as the unprivileged `wizzo`
user — a deliberate deviation from the spec's `/var/log` path.

Rollback: `./scripts/pull_latest.sh --version YYYYMMDD` with the env file
sourced, e.g. `set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --version 20260914`.

## Monitoring
- The publisher prints a JSON summary; `solar-build.service`'s `ExecStartPost` runs
  `build/mcp_notify.py` to relay it to the fleet hub / Slack (best-effort — piped
  through `|| true` so a missing or failing notifier never fails the build).
- On the API host, alert if `data/latest.json`'s `built_at` is older than 36 h.
