# Build host runbook

The catalogue is rebuilt from scratch every night on `llm1` and published to
Cloudflare R2; the public API host only ever downloads.

## Build host (llm1)
The build host is the server `llm1`, running under the user `wizzo` as a Docker
Compose `build` profile driven by systemd **user** units (no root, no system-wide
services). Install:

```bash
sudo mkdir -p /opt/solar-system-db /data/solar && sudo chown wizzo:wizzo /opt/solar-system-db /data/solar
git clone https://github.com/Wicked-Sick-Ltd/solar-system-db /opt/solar-system-db
mkdir -p ~/.config/systemd/user && cp /opt/solar-system-db/build/systemd/*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now solar-build.timer solar-crawler.service
loginctl enable-linger wizzo     # timers survive logout
systemctl --user list-timers solar-build.timer
```

The raw build is ~2.5 GB; keep headroom for two builds plus the crawler store and
scratch under `/data/solar` (mounted into the containers via `SOLAR_DATA_DIR`,
default `/data/solar` on llm1 vs. `./data` in dev).

## Object storage (Cloudflare R2)
- Bucket `solar-system-db` on Cloudflare R2, endpoint
  `https://4ce32b0dd5d81195ffdef6d24d1a8297.r2.cloudflarestorage.com`.
- Public base URL: `https://download.sol.wickedsick.com` (Cloudflare-fronted custom
  domain; the bucket itself is not public).
- Region is `auto` (`S3_REGION=auto`, `AWS_DEFAULT_REGION` derived from it).
- **No ACLs are ever sent** — R2 doesn't support per-object ACLs and errors on
  them. `publish_artifact.py` is invoked with `S3_NO_ACL=1` (`--no-acl`) on this
  host; do not remove that.
- 1Password item `solar-system-db-r2-publisher` (vault Shared-Secrets) holds an
  R2 API token scoped to Object Read & Write on this one bucket only.
- Retention: the publisher prunes dated artefacts older than 30 days; `latest.json`
  always points at the newest.

## Secrets
`op` on llm1 authenticates as a service account: `wizzo`'s systemd user units load
`OP_SERVICE_ACCOUNT_TOKEN` from `%h/.config/op/op-service-account.env`
(`EnvironmentFile=` in both `solar-build.service` and `solar-crawler.service`).
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
WorkingDirectory=/opt/solar-system-db
Environment=SOLAR_DATA_DIR=/data/solar
EnvironmentFile=%h/.config/op/op-service-account.env
ExecStart=/usr/bin/op run --env-file=/opt/solar-system-db/build/.env.op -- /usr/bin/docker compose --profile build run --rm builder
ExecStartPost=/bin/sh -c 'python3 /opt/solar-system-db/build/mcp_notify.py 2>/dev/null || true'
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
with JPL for our static IP range. The store `data/enrichment.sqlite` is merged into
every nightly build (`--enrichment-store`), so coverage never regresses.

## API host
`.env` needs `MANIFEST_URL`. Cron every 15 minutes:
```
*/15 * * * * cd /opt/solar-system-db && set -a && . ./.env && set +a && ./scripts/pull_latest.sh >> /var/log/solar-pull.log 2>&1
```
Rollback: `./scripts/pull_latest.sh --version 20260914`.

## Monitoring
- The publisher prints a JSON summary; `solar-build.service`'s `ExecStartPost` runs
  `build/mcp_notify.py` to relay it to the fleet hub / Slack (best-effort — piped
  through `|| true` so a missing or failing notifier never fails the build).
- On the API host, alert if `data/latest.json`'s `built_at` is older than 36 h.
