# Build host runbook

The catalogue is rebuilt from scratch every night on a dedicated box in the CML
datacenter and published to Ceph RGW; the public API host only ever downloads.

## Box
- Proxmox LXC/VM on `proxmox5`, VLAN 5 (`cmserver`), 4 vCPU, 8 GB RAM, 40 GB disk
  (the raw build is ~2.5 GB; keep two, plus the crawler store and scratch).
- Docker + compose. Clone the repo to `/opt/solar-system-db`.

## Object storage
- Bucket `solar-system-db` on `https://s3.wickedsick.com` under the `wizmedia` account
  (same pattern as the web app's OG cache — see the Engineering Wiki S3 runbooks).
- Scoped user `solar-system-db-publisher`: put/get/list/delete on that bucket only.
- Objects are written `public-read`; the bucket listing stays private. Public base
  URL: `https://s3.wickedsick.com/solar-system-db/`. Cloudflare can front it if egress
  cost matters.
- Retention: the publisher prunes dated artefacts older than 30 days; `latest.json`
  always points at the newest.

## Secrets
Store the publisher keys in 1Password (Shared-Secrets) and run everything through
`op run` so nothing lands on disk:

```bash
export AWS_ACCESS_KEY_ID="op://Shared-Secrets/solar-system-db-publisher/username"
export AWS_SECRET_ACCESS_KEY="op://Shared-Secrets/solar-system-db-publisher/credential"
op run -- docker compose --profile build run --rm builder
```

## Nightly build (systemd timer)
`/etc/systemd/system/solar-build.service`:
```
[Service]
Type=oneshot
WorkingDirectory=/opt/solar-system-db
EnvironmentFile=/opt/solar-system-db/.env
ExecStart=/usr/bin/op run -- /usr/bin/docker compose --profile build run --rm builder
```
`/etc/systemd/system/solar-build.timer`: `OnCalendar=*-*-* 03:00:00 UTC`, `Persistent=true`.
Budget: ~40 min bulk + ~10 min compress/upload. The build writes to the `build_scratch`
volume and only publishes after `verify.py` passes.

## Crawler (always on)
```bash
docker compose --profile build up -d crawler
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
- The publisher prints a JSON summary; pipe it to the fleet hub / Slack from the timer's
  `ExecStartPost`.
- On the API host, alert if `data/latest.json`'s `built_at` is older than 36 h.
