# php01 deploy — pull_latest.sh cron

## Migrating from the committed database (one-time, before the first `git pull` after PR #15)

php01 has been running `pull_latest.sh` with `DATA_DIR=/home/wizzo/solar-system-db/data`,
so the live 2.75 GB catalogue sits at the *tracked* path `data/solar_system.sqlite`,
locally modified relative to the stale 89 MB blob that was committed there. After
PR #15 merges, a plain `git pull --ff-only` on php01 aborts with "Your local
changes to the following files would be overwritten" — and the obvious recoveries
(`git checkout -- data/…`, `git stash`, `git reset --hard`) would replace the live
catalogue with that stale blob. Do this instead, once, as `wizzo` on php01:

```bash
mkdir -p /home/wizzo/solar-data
cd ~/solar-system-db
mv data/solar_system.sqlite /home/wizzo/solar-data/solar_system.sqlite     # rename on the same filesystem; the running API keeps its open inode
mv data/latest.json /home/wizzo/solar-data/latest.json 2>/dev/null || true
git checkout -- data/solar_system.sqlite    # restore the (stale, tracked) blob so the pull can delete it cleanly
git pull --ff-only origin main
sed -i 's|^DATA_DIR=.*|DATA_DIR=/home/wizzo/solar-data|' ~/.config/solar-pull.env
printf '%s\n' "$(sudo systemctl cat solar-api | sed '1{/^#/d}' | sed 's|SOLAR_DB_PATH=.*|SOLAR_DB_PATH=/home/wizzo/solar-data/solar_system.sqlite|')" | sudo tee /etc/systemd/system/solar-api.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl stop solar-api && sudo systemctl start solar-api
curl -s http://127.0.0.1:8003/api/v1/stats | head -c 200
```

Notes:
- `systemctl cat solar-api` prints a leading `# /etc/systemd/system/solar-api.service`
  comment line before the unit content; `sed '1{/^#/d}'` drops exactly that line
  before the `SOLAR_DB_PATH=` rewrite runs, so the unit file that gets written back
  doesn't gain a `#` where `[Service]` (or whatever line 1 of the real content is)
  belongs.
- The sudoers grant on php01 permits `tee /etc/systemd/system/solar-api.service`,
  `daemon-reload`, `stop solar-api`, and `start solar-api` without a password — the
  same grant `pull_latest.sh`'s `RESTART_CMD` already relies on, just used directly
  here for the one-time unit edit.
- After migration, `git status` in `~/solar-system-db` must be clean (no tracked
  file left locally modified) and `ls /home/wizzo/solar-data` must show both
  `solar_system.sqlite` and `latest.json`. The existing 15-minute cron keeps working
  unchanged — it sources `~/.config/solar-pull.env`, which now points `DATA_DIR` at
  the new location.

## One-time setup (original v1→v2 migration, already done)

php01 runs the API as a systemd unit (`solar-api`), not Docker. This was the
one-time setup, run as `wizzo` on php01, for the earlier v1→v2 schema migration:

```bash
cd ~/solar-system-db && git pull --ff-only origin main && .venv/bin/pip install -q -e '.[api]'
cp data/solar_system.sqlite data/solar_system.sqlite.pre-v2-$(date +%Y%m%d)
cp deploy/php01/solar-pull.env.example ~/.config/solar-pull.env   # edit if the hostname differs
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --dry-run
( crontab -l 2>/dev/null; echo '*/15 * * * * cd /home/wizzo/solar-system-db && set -a && . /home/wizzo/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1' ) | crontab -
```

The first log line shows the resolved restart command; it must read `sudo
systemctl stop solar-api && sudo systemctl start solar-api`, which proves the
env file parsed.

Note: the sudoers grant on php01 permits `systemctl stop solar-api` and
`systemctl start solar-api` but its `restart` line is malformed, hence the
stop+start `RESTART_CMD` in `solar-pull.env.example` instead of a single
`restart`.
