#!/usr/bin/env bash
# FastAPI catalogue server for the Cloud Agent `api` terminal.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8003}"

if [[ ! -f "$ROOT/data/solar_system.sqlite" ]]; then
  echo "Missing data/solar_system.sqlite. Run: bash .cursor/install.sh" >&2
  exit 1
fi

if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${API_PORT}/healthz', timeout=2)" >/dev/null 2>&1; then
  echo "API already responding on port ${API_PORT}."
  exec sleep infinity
fi

exec python api/main.py
