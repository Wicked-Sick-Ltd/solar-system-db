#!/usr/bin/env bash
# Idempotent, non-interactive bootstrap for Cursor Cloud Agents.
# Matches .github/workflows/test.yml: zstd, editable install of the root
# package with the dev/publish/observing-jpl extras, plus ./mcp-server and ./api.
# The catalogue is SQLite. This does not refresh from JPL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export DEBIAN_FRONTEND=noninteractive
export PIP_DISABLE_PIP_VERSION_CHECK=1

apt_packages=()
if ! command -v zstd >/dev/null 2>&1; then
  apt_packages+=(zstd)
fi
if ! /usr/bin/python3 -c 'import ensurepip' >/dev/null 2>&1; then
  apt_packages+=(python3-venv)
fi
if ((${#apt_packages[@]})); then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends "${apt_packages[@]}"
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  /usr/bin/python3 -m venv "$ROOT/.venv"
fi

# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e '.[dev,publish,observing-jpl]' -e ./mcp-server -e ./api

# Login shells do not source this script. A symlink of the venv interpreter
# loses its prefix, so `python` is a wrapper that execs the real binary.
# pytest and ruff are console scripts whose shebang already points at it.
if sudo -n true 2>/dev/null; then
  wrapper="$(mktemp)"
  printf '#!/bin/sh\nexec %q "$@"\n' "$ROOT/.venv/bin/python" >"$wrapper"
  chmod 755 "$wrapper"
  sudo mv "$wrapper" /usr/local/bin/python
  sudo ln -sfn "$ROOT/.venv/bin/pytest" /usr/local/bin/pytest
  sudo ln -sfn "$ROOT/.venv/bin/ruff" /usr/local/bin/ruff
else
  echo "WARNING: passwordless sudo is unavailable, so python, pytest, and ruff were not linked into /usr/local/bin. Activate .venv before using them."
fi

mkdir -p "$ROOT/data"
if [[ ! -f "$ROOT/data/solar_system.sqlite" ]]; then
  echo "Building the offline fixture catalogue at data/solar_system.sqlite (no JPL refresh)."
  SSDB_BUILD_PATH="$ROOT/data/solar_system.sqlite" SSDB_NO_PUBLISH=1 \
    python scripts/build_full.py --fresh --offline --no-vacuum
else
  echo "Catalogue already present at data/solar_system.sqlite; leaving it in place."
fi

warn_missing() {
  local name="$1"
  local purpose="$2"
  if [[ -z "${!name:-}" ]]; then
    echo "WARNING: ${name} is not set. ${purpose}"
  fi
}

warn_missing AWS_ACCESS_KEY_ID "Nightly publish to object storage needs it. Local tests and the API do not."
warn_missing AWS_SECRET_ACCESS_KEY "Nightly publish to object storage needs it. Local tests and the API do not."

echo "Install complete."
