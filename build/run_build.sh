#!/bin/sh
# Entry point for solar-build.service. Sources the 1Password service-account
# token and runs the builder container under `op run`.
#
# Why a wrapper and not EnvironmentFile=: the token file on llm1 is written for
# shells (`export OP_SERVICE_ACCOUNT_TOKEN=…`, sourced by ~/.bashrc). systemd's
# EnvironmentFile= does not accept the `export` prefix — it logs
# "Ignoring invalid environment assignment '<the whole line>'" to the journal,
# token included, and starts `op` with no credentials. That is exactly how the
# 2026-09-18 and 2026-09-19 nightlies failed. `.`-sourcing accepts both
# `KEY=value` and `export KEY=value`, and the token never reaches the journal.
set -eu

OP_SA_ENV="${OP_SA_ENV:-$HOME/.config/op/op-service-account.env}"
REPO="${SOLAR_REPO_DIR:-$HOME/deploy/solar-system-db}"

if [ ! -r "$OP_SA_ENV" ]; then
    echo "run_build: token file $OP_SA_ENV is missing or unreadable" >&2
    exit 78  # EX_CONFIG
fi
# shellcheck disable=SC1090
. "$OP_SA_ENV"
export OP_SERVICE_ACCOUNT_TOKEN
if [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]; then
    echo "run_build: OP_SERVICE_ACCOUNT_TOKEN is empty after sourcing $OP_SA_ENV" >&2
    exit 78
fi

cd "$REPO"
exec /usr/bin/op run --env-file=build/.env.op -- /usr/bin/docker compose --profile build run --rm builder
