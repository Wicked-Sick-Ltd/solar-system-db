# php01 deploy — pull_latest.sh cron

php01 runs the API as a systemd unit (`solar-api`), not Docker. One-time setup,
run as `wizzo` on php01:

```bash
cd ~/solar-system-db && git pull --ff-only origin main && .venv/bin/pip install -q -e .[api]
cp data/solar_system.sqlite data/solar_system.sqlite.pre-v2-$(date +%Y%m%d)
cp deploy/php01/solar-pull.env.example ~/.config/solar-pull.env   # edit if the hostname differs
set -a; . ~/.config/solar-pull.env; set +a; ./scripts/pull_latest.sh --dry-run
( crontab -l 2>/dev/null; echo '*/15 * * * * cd /home/wizzo/solar-system-db && set -a && . /home/wizzo/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1' ) | crontab -
```

Note: the sudoers grant on php01 permits `systemctl stop solar-api` and
`systemctl start solar-api` but its `restart` line is malformed, hence the
stop+start `RESTART_CMD` in `solar-pull.env.example` instead of a single
`restart`.
