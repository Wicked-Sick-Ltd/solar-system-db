#!/usr/bin/env bash
# scripts/pull_latest.sh — on the public API host, fetch the latest published
# catalogue from object storage and swap it in atomically.
#
#   MANIFEST_URL=https://download.sol.wickedsick.com/latest.json \
#   DATA_DIR=/home/wizzo/solar-system-db/data ./scripts/pull_latest.sh
#
# Options:   --version YYYYMMDD   pin/roll back to a dated artefact
#            --force              re-download even if the sha matches
#            --dry-run   print what would be downloaded and exit 0
# Env:       RESTART_CMD          default "docker compose restart rest-api mcp-server"
# Cron (every 15 min; the build publishes around 03:40 UTC):
#   */15 * * * * cd /home/wizzo/solar-system-db && set -a && . ~/.config/solar-pull.env && set +a && ./scripts/pull_latest.sh >> /home/wizzo/solar-pull.log 2>&1
set -euo pipefail

MANIFEST_URL="${MANIFEST_URL:?set MANIFEST_URL to the published latest.json}"
DATA_DIR="${DATA_DIR:-$(cd "$(dirname "$0")/.." && pwd)/data}"
RESTART_CMD="${RESTART_CMD:-docker compose restart rest-api mcp-server}"
VERSION=""; FORCE=0; DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac
done

log() { echo "[$(date -u +%FT%TZ)] $*"; }
need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 1; }; }
log "restart command: $RESTART_CMD"
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
# The manifest is fetched over HTTPS but not signed: never let its fields shape a path.
name="$(basename -- "$name")"
[[ "$name" =~ ^solar_system-[0-9]{8}\.sqlite\.zst$ ]] || { log "refusing unexpected artefact name: $name"; exit 1; }
case "$art_url" in "${MANIFEST_URL%/*}"/solar_system-*.sqlite.zst) ;; *) log "refusing artefact URL outside the manifest's directory: $art_url"; exit 1 ;; esac

have_sha=""
[[ -f "$DATA_DIR/latest.json" ]] && have_sha="$(json '["sha256"]' < "$DATA_DIR/latest.json" || true)"
if [[ "$FORCE" -eq 0 && "$have_sha" == "$want_sha" && -f "$DATA_DIR/solar_system.sqlite" ]]; then
  log "already at $name ($want_sha)"; exit 0
fi

if [[ "$DRY" -eq 1 ]]; then log "would download $art_url ($want_sha)"; exit 0; fi

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
log "installed $name; restarting services"
# Only record the install once the services have actually been recycled, so a
# failed restart is retried on the next cron run instead of being masked.
eval "$RESTART_CMD" || { log "restart failed — latest.json not written; will retry"; exit 1; }
printf '%s\n' "$manifest" > "$DATA_DIR/latest.json"
log "done"
