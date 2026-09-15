#!/usr/bin/env bash
# scripts/pull_latest.sh — on the public API host, fetch the latest published
# catalogue from object storage and swap it in atomically.
#
#   MANIFEST_URL=https://s3.wickedsick.com/solar-system-db/latest.json \
#   DATA_DIR=/opt/solar-system-db/data ./scripts/pull_latest.sh
#
# Options:   --version YYYYMMDD   pin/roll back to a dated artefact
#            --force              re-download even if the sha matches
# Env:       RESTART_CMD          default "docker compose restart rest-api mcp-server"
# Cron (every 15 min; the build publishes around 03:40 UTC):
#   */15 * * * * cd /opt/solar-system-db && MANIFEST_URL=… ./scripts/pull_latest.sh >> /var/log/solar-pull.log 2>&1
set -euo pipefail

MANIFEST_URL="${MANIFEST_URL:?set MANIFEST_URL to the published latest.json}"
DATA_DIR="${DATA_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data}"
RESTART_CMD="${RESTART_CMD:-docker compose restart rest-api mcp-server}"
VERSION=""; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

log() { echo "[$(date -u +%FT%TZ)] $*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
need curl; need zstd; need sha256sum || true
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

mkdir -p "$DATA_DIR"
url="$MANIFEST_URL"
if [[ -n "$VERSION" ]]; then url="${MANIFEST_URL%latest.json}solar_system-${VERSION}.json"; fi
manifest="$(curl -fsSL "$url")"
want_sha="$(printf '%s' "$manifest" | json '["sha256"]')"
art_url="$(printf '%s' "$manifest" | json '["url"]')"
name="$(printf '%s' "$manifest" | json '["artefact"]')"

have_sha=""
[[ -f "$DATA_DIR/latest.json" ]] && have_sha="$(json '["sha256"]' < "$DATA_DIR/latest.json" || true)"
if [[ "$FORCE" -eq 0 && "$have_sha" == "$want_sha" && -f "$DATA_DIR/solar_system.sqlite" ]]; then
  log "already at $name ($want_sha)"; exit 0
fi

tmp="$(mktemp -d "${DATA_DIR}/.pull.XXXXXX")"; trap 'rm -rf "$tmp"' EXIT
log "downloading $art_url"
curl -fsSL --retry 3 -o "$tmp/$name" "$art_url"
got_sha="$(sha "$tmp/$name")"
[[ "$got_sha" == "$want_sha" ]] || { log "sha mismatch: got $got_sha want $want_sha"; exit 1; }
log "verified sha256; decompressing"
zstd -q -d -o "$tmp/solar_system.sqlite" "$tmp/$name"
python3 - "$tmp/solar_system.sqlite" <<'PY'
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
assert c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
n = c.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
assert n > 1000, n
print(f"integrity ok, {n} objects")
PY
mv -f "$tmp/solar_system.sqlite" "$DATA_DIR/solar_system.sqlite"          # atomic on the same filesystem
printf '%s\n' "$manifest" > "$DATA_DIR/latest.json"
log "installed $name; restarting services"
eval "$RESTART_CMD"
log "done"
