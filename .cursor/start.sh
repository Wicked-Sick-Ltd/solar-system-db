#!/usr/bin/env bash
# Per-boot check. The API process is the `api` terminal (see environment.json).
# Dependency installs and the offline catalogue stay in install.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Missing .venv. Run: bash .cursor/install.sh" >&2
  exit 1
fi

if [[ ! -f "$ROOT/data/solar_system.sqlite" ]]; then
  echo "Missing data/solar_system.sqlite. Run: bash .cursor/install.sh" >&2
  exit 1
fi

echo "Catalogue fixture is present. The api terminal serves it on port 8003."
