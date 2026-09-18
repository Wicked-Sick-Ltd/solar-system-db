# php01 — the API host

php01 (`192.168.5.101`, Laravel Forge server; SSH as `wizzo`) serves
`api.sol.wickedsick.com`. It runs the FastAPI app as a **systemd unit**
(`solar-api`, not Docker) and refreshes its catalogue every 15 minutes with
`scripts/pull_latest.sh` from the manifest the llm1 build host publishes to R2.

## Layout (current, since 2026-09-18)

| What | Where |
|---|---|
| Code checkout (main, read-only, `git pull --ff-only` only) | `/home/wizzo/solar-system-db` |
| Live catalogue + manifest (`DATA_DIR`) | `/home/wizzo/solar-data/solar_system.sqlite`, `/home/wizzo/solar-data/latest.json` |
| Pull env file (sourced by cron) | `/home/wizzo/.config/solar-pull.env` (template: `solar-pull.env.example`) |
| Pull log | `/home/wizzo/solar-pull.log` (not `/var/log`: cron runs as the unprivileged `wizzo`) |
| systemd unit | `/etc/systemd/system/solar-api.service` (template: `solar-api.service` in this directory) |
| API bind | `127.0.0.1:8003`, fronted by Forge's nginx |
| Pre-cutover backups (untracked, safe to delete once happy) | `/home/wizzo/solar-system-db/data/solar_system.sqlite.pre-v2-2026091{7,8}` |

Nothing under `~/solar-system-db/data/` is live any more. `data/` is git-ignored
and only holds those backups.

## Cron (exactly one line)

```
*/15 * * * * cd /home/wizzo/solar-system-db && set -a && . /home/wizzo/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1
```

Install or repair it idempotently (this never adds a second copy):

```bash
LINE='*/15 * * * * cd /home/wizzo/solar-system-db && set -a && . /home/wizzo/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1'
( crontab -l 2>/dev/null | grep -vF 'pull_latest.sh'; echo "$LINE" ) | crontab -
crontab -l | grep -c pull_latest.sh    # must print 1
```

Each run logs its resolved restart command first; it must read
`sudo systemctl stop solar-api && sudo systemctl start solar-api`, which proves
the env file parsed. Two of every log line at the same timestamp means the cron
line is duplicated — run the block above.

## Env file

`~/.config/solar-pull.env` must contain:

```
MANIFEST_URL=https://download.sol.wickedsick.com/latest.json
DATA_DIR=/home/wizzo/solar-data
RESTART_CMD='sudo systemctl stop solar-api && sudo systemctl start solar-api'
```

`RESTART_CMD` is stop+start, not `restart`: the sudoers grant's `restart` line is
malformed (`/usr/bin systemctl restart solar-api`, missing the slash) and does not
match. The grant does cover, without a password: `tee /etc/systemd/system/solar-api.service`,
`systemctl daemon-reload`, `enable|disable|start|stop solar-api`, and
`is-active|is-failed|status *`.

## Routine operations

```bash
# health
systemctl is-active solar-api && curl -s http://127.0.0.1:8003/api/v1/stats | head -c 200
tail -3 ~/solar-pull.log

# force a re-pull of whatever the manifest points at
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --force

# roll back to a dated artefact (the publisher keeps 30 days)
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --version 20260917

# update code (no data lives in the checkout, so this is always safe)
cd ~/solar-system-db && git pull --ff-only origin main && .venv/bin/pip install -q -e '.[api]' \
  && sudo systemctl stop solar-api && sudo systemctl start solar-api
```

## Restore the unit file

If `systemctl start solar-api` says *"Unit solar-api.service has a bad unit file
setting"* or `systemd-analyze verify` complains, the unit is missing or empty.
Rewrite it from the committed template — a plain redirect, never a pipe whose
left side reads the same file (see the 2026-09-18 note below):

```bash
cd ~/solar-system-db && git pull -q --ff-only origin main
sudo tee /etc/systemd/system/solar-api.service < deploy/php01/solar-api.service >/dev/null
systemd-analyze verify /etc/systemd/system/solar-api.service && echo UNIT_OK
sudo systemctl daemon-reload && sudo systemctl enable solar-api && sudo systemctl start solar-api
sleep 3; systemctl is-active solar-api; curl -s http://127.0.0.1:8003/api/v1/stats | head -c 120
```

If you change the unit on the box, change `deploy/php01/solar-api.service` in the
same breath. The template is the only copy that survives a bad write.

## History — do not re-run these

Both blocks below have been executed. They are kept so the shape of the box is
explicable, not as instructions. Re-running the first one on 2026-09-18 pulled a
pre-#15 `main`, made a second 2.9 GB backup and duplicated the cron line.

### 2026-09-17 — v1→v2 cutover (committed 41 MB DB → pulled 2.75 GB catalogue)

```bash
cd ~/solar-system-db && git pull --ff-only origin main && .venv/bin/pip install -q -e '.[api]'
cp data/solar_system.sqlite data/solar_system.sqlite.pre-v2-$(date +%Y%m%d)
cp deploy/php01/solar-pull.env.example ~/.config/solar-pull.env
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --dry-run
# + the cron line above, appended with `( crontab -l; echo … ) | crontab -`
```

At that point `DATA_DIR` was `/home/wizzo/solar-system-db/data`, so the live
catalogue landed on the *tracked* path `data/solar_system.sqlite`.

### 2026-09-18 — move the data out of the checkout (after PR #15 untracked the DB)

Because the live file sat on a tracked path, `git pull` after #15 would have
aborted, and `git checkout -- data/…` / `git stash` / `git reset --hard` would have
replaced the catalogue with the stale committed blob. The migration was:

```bash
mkdir -p /home/wizzo/solar-data
cd ~/solar-system-db
mv data/solar_system.sqlite /home/wizzo/solar-data/solar_system.sqlite   # same filesystem: the running API keeps its open inode
mv data/latest.json /home/wizzo/solar-data/latest.json
git checkout -- data/solar_system.sqlite    # restore the stale blob so the pull can delete it cleanly
git pull --ff-only origin main
sed -i 's|^DATA_DIR=.*|DATA_DIR=/home/wizzo/solar-data|' ~/.config/solar-pull.env
# rewrite SOLAR_DB_PATH in the unit → see "Restore the unit file"
sudo systemctl daemon-reload && sudo systemctl stop solar-api && sudo systemctl start solar-api
```

**Incident.** The original runbook rewrote the unit with
`printf '%s\n' "$(sudo systemctl cat solar-api | sed …)" | sudo tee /etc/systemd/system/solar-api.service`.
Bash starts both sides of a pipeline at once, so `tee` truncated the unit file
before `systemctl cat` had read it; the result was a one-byte file, `stop`
succeeded, `start` failed with "bad unit file setting", and the API was down
from 11:27 to 11:38 UTC until the unit was re-typed by hand. Hence the committed
template and the redirect-only restore above. Never read a file on the left of a
pipe that writes the same file on the right.
