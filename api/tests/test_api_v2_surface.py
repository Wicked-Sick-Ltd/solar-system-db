import json
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _db():
    conn = sqlite3.connect(os.environ["SOLAR_DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def test_fts_search_finds_provisional_designation_and_prefix(client):
    r = client.get("/api/v1/search", params={"q": "A801"}).json()
    assert r["results"] and r["results"][0]["name"] == "Ceres"
    r = client.get("/api/v1/search", params={"q": "apoph"}).json()
    assert any(x["name"] == "Apophis" for x in r["results"])


def test_keyset_pagination_walks_everything_once(client):
    seen, after, pages = set(), "", 0
    while True:
        r = client.get("/api/v1/objects", params={"type": "asteroid", "limit": 500, "after": after}).json()
        ids = [x["id"] for x in r["results"]]
        assert not (set(ids) & seen)
        seen.update(ids)
        pages += 1
        if r["next_after"] is None:
            break
        after = r["next_after"]
    total = _db().execute("SELECT COUNT(*) FROM objects WHERE object_type='asteroid'").fetchone()[0]
    assert len(seen) == total and pages >= 2


def test_new_filters(client):
    r = client.get("/api/v1/objects", params={"orbit_class": "APO", "max_moid_au": 0.05, "max_condition_code": 3, "limit": 20}).json()
    assert r["results"] and all(x["orbit_class_code"] == "APO" and x["moid_au"] <= 0.05 for x in r["results"])
    r = client.get("/api/v1/objects", params={"min_diameter_km": 500, "limit": 50}).json()
    assert all(x["radius_km"] >= 250 for x in r["results"])


def test_object_detail_routes(client):
    assert client.get("/api/v1/objects/Vesta/discovery").json()["site"] == "Bremen"
    des = client.get("/api/v1/objects/Vesta/designations").json()["results"]
    assert {d["designation"] for d in des} >= {"4", "Vesta"}
    atm = client.get("/api/v1/planets/Mars/atmosphere").json()
    assert atm["surface_pressure_bar"] < 0.01 and atm["composition"][0]["species"].startswith("Carbon Dioxide")
    assert client.get("/api/v1/objects/nothing-xyz/discovery").status_code == 404


def test_close_approach_routes(client):
    conn = _db()
    obj_id, iso = conn.execute("SELECT object_id, cd_iso FROM close_approaches ORDER BY cd_jd LIMIT 1").fetchone()
    r = client.get(f"/api/v1/objects/{obj_id}/close-approaches").json()
    assert r["results"] and r["results"][0]["cd_iso"] == iso
    year = iso[:4]
    r = client.get("/api/v1/close-approaches", params={"from": f"{year}-01-01", "to": f"{year}-12-31", "max_dist_au": 0.05}).json()
    assert r["results"] and r["results"][0]["dist_au"] <= r["results"][-1]["dist_au"]


def test_download_manifest_404_then_served(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SOLAR_MANIFEST_PATH", str(tmp_path / "latest.json"))
    assert client.get("/api/v1/download").status_code == 404
    (tmp_path / "latest.json").write_text(json.dumps({"url": "https://s3.wickedsick.com/solar-system-db/solar_system-20260915.sqlite.zst",
                                                       "sha256": "abc", "size_bytes": 1, "built_at": "2026-09-15T03:00:00Z"}))
    assert client.get("/api/v1/download").json()["sha256"] == "abc"


def test_negative_limits_are_clamped_everywhere(client):
    from solar_db import SolarDB
    db = SolarDB(os.environ["SOLAR_DB_PATH"])
    assert len(db.search("a", limit=-1)) <= 100
    assert len(db.find_objects(limit=-5)) == 1
    assert len(db.close_approaches_between("1900-01-01", "2200-01-01", max_dist_au=5, limit=-1)) <= 1000
    conn = _db()
    obj_id = conn.execute("SELECT object_id FROM close_approaches LIMIT 1").fetchone()[0]
    assert len(db.close_approaches_for(obj_id, limit=0)) >= 1


def test_meteor_showers_list_and_detail(client):
    r = client.get("/api/v1/meteor-showers?established_only=true")
    assert r.status_code == 200 and any(s["code"] == "GEM" for s in r.json()["items"])
    r = client.get("/api/v1/meteor-showers/GEM")
    assert r.status_code == 200
    d = r.json()
    assert d["code"] == "GEM" and d["parameter_sets"] and d["parent"] and d["parent"]["name"].lower().startswith("phaethon")


def test_active_on_filters_by_solar_longitude(client):
    dec = client.get("/api/v1/meteor-showers?active_on=2026-12-14").json()["items"]
    jun = client.get("/api/v1/meteor-showers?active_on=2026-06-14").json()["items"]
    assert any(s["code"] == "GEM" for s in dec) and not any(s["code"] == "GEM" for s in jun)


def test_active_on_rejects_garbage_date(client):
    r = client.get("/api/v1/meteor-showers?active_on=not-a-date")
    assert r.status_code == 422


def test_parent_object_lists_its_showers(client):
    d = client.get("/api/v1/objects/3200").json()
    assert any(s["code"] == "GEM" for s in d["meteor_showers"])


def test_list_meteor_showers_clamps_negative_limit():
    from solar_db import SolarDB
    db = SolarDB(os.environ["SOLAR_DB_PATH"])
    assert len(db.list_meteor_showers(limit=-1)) <= 1000
    assert len(db.list_meteor_showers(limit=0)) >= 1


def test_established_only_excludes_to_be_established(tmp_path):
    """established_only must match MDC status codes 1/6 exactly, not a
    '%stablished%' text match — status 2 ("to be established shower") is a
    substring match for that text but is NOT established."""
    from solar_db import SolarDB

    schema_sql = (Path(__file__).resolve().parents[2] / "schema" / "schema.sql").read_text()
    db_path = tmp_path / "showers_established.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql)
    conn.executemany(
        "INSERT INTO meteor_showers (iau_no, ad_no, code, name, status_code, status_label, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (99999, 0, "TBE", "Synthetic to-be-established", 2, "to be established shower", "test"),
            (99998, 0, "MEG", "Synthetic member of established group", 6, "member of the established group", "test"),
        ],
    )
    conn.commit()
    conn.close()

    db = SolarDB(db_path)
    established = db.list_meteor_showers(established_only=True)
    everything = db.list_meteor_showers(established_only=False)

    assert any(r["code"] == "MEG" for r in established)
    assert not any(r["code"] == "TBE" for r in established)
    assert any(r["code"] == "TBE" for r in everything)
    assert any(r["code"] == "MEG" for r in everything)


def test_active_on_window_applies_before_limit_not_after(tmp_path):
    """C1 regression: with ~1,400 rows and the default limit of 500, the
    active_on ±15° window has to be a SQL WHERE clause evaluated before
    LIMIT — a Python filter applied after LIMIT would silently drop any
    matching row past the limit (iau_no order), exactly what happened with
    ~1,420 live rows and a default limit of 500."""
    import datetime as dt

    from solar_db import SolarDB
    from solar_db.positions import solar_longitude_deg

    target_date = dt.date(2026, 12, 14)
    target_l = solar_longitude_deg(target_date)

    schema_sql = (Path(__file__).resolve().parents[2] / "schema" / "schema.sql").read_text()
    db_path = tmp_path / "showers_window.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql)
    rows = []
    for iau_no in range(1, 1401):
        if iau_no == 1300:
            lon = target_l
        else:
            lon = (iau_no * 137) % 360.0
            diff = abs(lon - target_l) % 360
            if min(diff, 360 - diff) <= 20:  # keep every other row well outside the window
                lon = (lon + 90) % 360.0
        rows.append((iau_no, 0, f"S{iau_no:04d}", f"Synthetic {iau_no}", 0, "working list", lon, "test"))
    conn.executemany(
        "INSERT INTO meteor_showers (iau_no, ad_no, code, name, status_code, status_label, "
        "solar_longitude_deg, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    db = SolarDB(db_path)
    result = db.list_meteor_showers(active_on=target_date.isoformat())
    assert [r["iau_no"] for r in result] == [1300]


def test_active_on_wraps_around_0_360(tmp_path):
    """A shower peaking at 355° must count as active when the queried date's
    solar longitude is 5° — an 10° circular gap, not the 350° a naive
    subtraction would compute."""
    from solar_db import SolarDB

    schema_sql = (Path(__file__).resolve().parents[2] / "schema" / "schema.sql").read_text()
    db_path = tmp_path / "showers_wrap.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql)
    conn.execute(
        "INSERT INTO meteor_showers (iau_no, ad_no, code, name, status_code, status_label, "
        "solar_longitude_deg, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (88888, 0, "WRP", "Synthetic wraparound shower", 0, "working list", 355.0, "test"),
    )
    conn.commit()
    conn.close()

    db = SolarDB(db_path)
    # Sun's ecliptic longitude ~5° falls in early April; find a date whose
    # computed solar_longitude_deg lands close to 5° so the window (peak 355°,
    # target ~5°, circular gap ~10°) is exercised without hardcoding the model's
    # exact day-of-year mapping.
    from solar_db.positions import solar_longitude_deg
    import datetime as dt
    target_date = None
    for day_offset in range(365):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=day_offset)
        l = solar_longitude_deg(d)
        diff = abs(l - 5.0) % 360
        if min(diff, 360 - diff) <= 2:
            target_date = d
            break
    assert target_date is not None, "no date found with solar longitude near 5 degrees"

    result = db.list_meteor_showers(active_on=target_date.isoformat())
    assert any(r["code"] == "WRP" for r in result)
