"""Superseded by build_full.py (2026-09-15). Kept so old instructions still work.

    --skip-net  → build_full.py --fresh --offline
    otherwise   → build_full.py --fresh --online
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_full  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    mode = "--offline" if "--skip-net" in args else "--online"
    print(f"populate_initial.py is superseded — running build_full.py --fresh {mode}")
    sys.exit(build_full.main(["--fresh", mode]))
