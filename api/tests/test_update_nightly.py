from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import common
import update_nightly


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    with (ROOT / "schema" / "schema.sql").open() as f:
        db.executescript(f.read())
    yield db
    db.close()


def test_refresh_stale_elements_includes_dwarf_object_types(conn, monkeypatch):
    conn.executemany(
        """
        INSERT INTO objects (id, name, designation, object_type)
        VALUES (?, ?, ?, ?)
        """,
        [
            ("dwarf-pluto", "Pluto", "134340", "dwarf_planet"),
            ("dwarf-sedna", "Sedna", "90377", "dwarf_planet_candidate"),
            ("ast-ceres", "Ceres", "1", "asteroid"),
            ("planet-earth", "Earth", "399", "planet"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO orbital_elements (object_id, updated_at)
        VALUES (?, '2000-01-01 00:00:00')
        """,
        [
            ("dwarf-pluto",),
            ("dwarf-sedna",),
            ("ast-ceres",),
            ("planet-earth",),
        ],
    )

    monkeypatch.setattr(update_nightly.time, "time", lambda: 10_000_000_000)
    monkeypatch.setattr(update_nightly.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        common,
        "fetch_json",
        lambda *_args, **_kwargs: {
            "orbit": {
                "epoch": "2460000.5",
                "elements": [
                    {"name": "a", "value": "39.5"},
                    {"name": "e", "value": "0.25"},
                    {"name": "i", "value": "17.1"},
                    {"name": "om", "value": "110.3"},
                    {"name": "w", "value": "113.8"},
                    {"name": "ma", "value": "14.5"},
                    {"name": "per", "value": "90560"},
                    {"name": "q", "value": "29.7"},
                    {"name": "ad", "value": "49.3"},
                ],
            }
        },
    )

    refreshed = update_nightly.refresh_stale_elements(conn, days_stale=90, batch_size=10)

    assert refreshed == 3
    refreshed_ids = {
        row["object_id"]
        for row in conn.execute(
            "SELECT object_id FROM sources WHERE source_name='JPL SBDB (nightly refresh)'"
        )
    }
    assert refreshed_ids == {"dwarf-pluto", "dwarf-sedna", "ast-ceres"}
