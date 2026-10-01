"""Offline source, ingestion and REST/MCP checks for the bounded starter sample."""

import asyncio
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from solar_db import SolarDB
from solar_db.starter_catalogues import load_starter_catalogues, normalise, sexagesimal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ingest_starter_catalogues import write_starter_catalogues  # noqa: E402


@pytest.fixture
def catalogue(tmp_path):
    path = tmp_path / "starter.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript((ROOT / "schema/schema.sql").read_text())
        counts = write_starter_catalogues(conn)
        assert counts == write_starter_catalogues(conn)
        assert counts == dict(bright_star=50, double_star=23, deep_sky=108)
        assert conn.execute("SELECT count(*) FROM objects").fetchone()[0] == 0
    return SolarDB(path)


def test_raw_source_provenance_and_measurements(catalogue):
    sirius = catalogue.get_starter_target("bsc5p:hr2491")
    assert sirius["source_data"]["alt_name"] == "9Alp CMa"
    assert sirius["magnitude"] == -1.46
    assert sirius["components"] == "AB" and sirius["separation_arcsec"] == 11.2
    assert sirius["separation_epoch"] is None and sirius["position_angle_deg"] is None
    assert "reference epoch" in sirius["coordinate_epoch"]
    assert sirius["astrometry"]["observation_epoch_jyear"] is None
    assert sirius["provenance"]["upstream_sha256"]
    andromeda = catalogue.get_starter_target("openngc:NGC0224")
    assert andromeda["aliases"] == ["M 31"]
    assert andromeda["provenance"]["license"] == "CC-BY-SA-4.0"
    assert andromeda["source_data"]["Sources"]
    assert andromeda["ra_deg"] == pytest.approx(
        sexagesimal(andromeda["source_data"]["RA"], ra=True)
    )
    assert catalogue.get_starter_target("M 31") is None
    assert catalogue.get_starter_target("openngc:M102") is None
    assert catalogue.get_starter_target("openngc:M040") is None


def test_null_zero_and_pair_identity(catalogue):
    capella = catalogue.get_starter_target("bsc5p:hr1708")
    assert capella["source_data"]["m_sep"] == "0"
    assert "double_star" not in capella["families"]
    assert capella["separation_arcsec"] is None  # Not a resolved pair in this sample.
    beta_cen = catalogue.get_starter_target("bsc5p:hr5267")
    assert beta_cen["source_data"]["m_sep"] == "1.3"
    assert beta_cen["components"] is None and "double_star" not in beta_cen["families"]
    rows, _ = load_starter_catalogues()
    original = next(r["source_data"] for r in rows if r["source"] == "openngc")
    assert normalise("openngc", dict(original, **{"V-Mag": ""}))["magnitude"] is None
    assert all(r["source_data"] for r in rows)


def test_pagination_is_stable_and_search_is_literal(catalogue):
    first = catalogue.list_starter_targets(limit=100)
    rest = catalogue.list_starter_targets(limit=100, offset=100)
    assert len({r["id"] for r in first["results"] + rest["results"]}) == 158
    assert first["total"] == 158
    assert catalogue.list_starter_targets(q="%")["results"] == []
    assert (
        catalogue.list_starter_targets(q="M 31")["results"][0]["id"]
        == "openngc:NGC0224"
    )
    assert catalogue.list_starter_targets(family="double_star")["total"] == 23


@pytest.mark.parametrize(
    "args",
    [
        dict(family="galaxy"),
        dict(limit=True),
        dict(limit=1.5),
        dict(offset=-1),
        dict(q=[]),
        dict(limit=201),
    ],
)
def test_invalid_filters_fail(catalogue, args):
    with pytest.raises(ValueError):
        catalogue.list_starter_targets(**args)


@pytest.mark.parametrize(
    "raw,ra",
    [
        ("24:00:00", True),
        ("12:60:00", True),
        ("+90:00:01", False),
        ("-91:00:00", False),
        ("NaN", False),
    ],
)
def test_invalid_coordinates_fail(raw, ra):
    with pytest.raises(ValueError):
        sexagesimal(raw, ra=ra)


def test_bad_snapshot_leaves_previous_data(catalogue, monkeypatch):
    import ingest_starter_catalogues

    def fail():
        raise ValueError("source hash mismatch")

    monkeypatch.setattr(ingest_starter_catalogues, "load_starter_catalogues", fail)
    with sqlite3.connect(catalogue.db_path) as conn, pytest.raises(ValueError):
        write_starter_catalogues(conn)
    assert catalogue.list_starter_targets()["total"] == 158


def test_malformed_measurements_fail():
    rows, _ = load_starter_catalogues()
    raw = dict(next(r for r in rows if r["source"] == "bsc5p")["source_data"])
    for field, value in [("ra", "NaN"), ("m_sep", "-1"), ("vmag", "3")]:
        with pytest.raises(ValueError):
            normalise("bsc5p", dict(raw, **{field: value}))


def test_old_catalogue_unavailable(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE old (id TEXT)")
    db = SolarDB(path)
    assert db.list_starter_targets()["available"] is False
    assert db.get_starter_target("bsc5p:hr2491") is None


def test_rest_mcp_contract(catalogue, monkeypatch):
    monkeypatch.setenv("SOLAR_DB_PATH", str(catalogue.db_path))
    from api import main as api

    monkeypatch.setattr(api, "db", catalogue)
    monkeypatch.setattr(api.limiter, "enabled", False)
    spec = importlib.util.spec_from_file_location(
        "starter_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "db", lambda: catalogue)
    client = TestClient(api.app)
    response = client.get(
        "/api/v1/starter-targets", params={"family": "double_star", "limit": 3}
    )
    assert response.status_code == 200
    assert response.json() == server.list_starter_targets(family="double_star", limit=3)
    detail = client.get("/api/v1/starter-targets/openngc:NGC0224")
    assert detail.status_code == 200 and detail.json() == server.get_starter_target(
        "openngc:NGC0224"
    )
    assert detail.json()["astrometry"]["frame"] == "ICRS"
    assert detail.json()["provenance"]["astrometry_evidence"]["matched_records"] == 107
    assert client.get("/api/v1/starter-targets/M31").status_code == 404
    assert client.get("/api/v1/starter-targets?family=wrong").status_code == 422
    assert client.get("/api/v1/starter-targets?limit=true").status_code == 422
    with pytest.raises(ToolError):
        asyncio.run(server.mcp.call_tool("list_starter_targets", {"limit": True}))


def test_partial_schema_and_uningested_tables_unavailable(tmp_path):
    path = tmp_path / "partial.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE starter_targets (id TEXT, payload TEXT)")
    db = SolarDB(path)
    assert db.list_starter_targets()["available"] is False
    assert db.get_starter_target("bsc5p:hr2491") is None
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE starter_sources (source TEXT, payload TEXT)")
    assert db.list_starter_targets()["available"] is False
