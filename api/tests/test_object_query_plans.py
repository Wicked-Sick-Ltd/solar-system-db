"""Filter semantics plus deterministic index-seek regression checks (no timing gate)."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from solar_db import SolarDB
from solar_db.data_access import UnsupportedCatalogueFilter


@pytest.fixture(params=[True, False], ids=["v2", "v1"])
def sample(tmp_path, request):
    path = tmp_path / "queries.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            (Path(__file__).resolve().parents[2] / "schema/schema.sql").read_text()
        )
        conn.execute(
            "INSERT INTO objects(id,name,object_type) VALUES ('parent','Parent','planet')"
        )
        rows = [
            ("a", "", "asteroid", None, "1999-01-01", 0, 0, 0, 0, 0),
            ("b", "B", "asteroid", None, "2000-01-01", 5, 1, 0.5, 0.05, 3),
            ("c", "C", "asteroid", "parent", None, 5, 1, 0.6, None, None),
            ("d", "D", "comet", None, "2010-01-01", None, None, None, None, None),
            ("f", "F", "moon", "parent", "2020-01-01", 10, 2, 0.1, 0.02, 1),
        ]
        for (
            identity,
            name,
            kind,
            parent,
            date,
            radius,
            axis,
            ecc,
            moid,
            condition,
        ) in rows:
            conn.execute(
                "INSERT INTO objects(id,name,object_type,parent_id,discovery_date) VALUES (?,?,?,?,?)",
                (identity, name, kind, parent, date),
            )
            conn.execute(
                "INSERT INTO physical_properties(object_id,radius_km) VALUES (?,?)",
                (identity, radius),
            )
            conn.execute(
                "INSERT INTO orbital_elements(object_id,semi_major_axis_au,eccentricity,moid_au,condition_code,orbit_class_code) VALUES (?,?,?,?,?,?)",
                (identity, axis, ecc, moid, condition, "MBA"),
            )
        conn.execute(
            "INSERT INTO objects(id,name,object_type) VALUES ('e','E','asteroid')"
        )
        conn.executemany(
            "INSERT INTO classifications VALUES (?,?)",
            [("a", "NEO"), ("b", "NEO"), ("b", "PHA"), ("f", "PHA")],
        )
        if not request.param:
            conn.execute("DROP TABLE designations")
        conn.commit()
    return SolarDB(path), request.param


def ids(db, **filters):
    return [row["id"] for row in db.find_objects(**filters)]


def test_offset_order_includes_null_related_rows_and_stable_ties(sample):
    db, _ = sample
    assert ids(db) == ["a", "b", "c", "f", "d", "e", "parent"]
    assert ids(db, offset=1, limit=2) == ["b", "c"]
    assert ids(db, offset=4, limit=2) == ["d", "e"]
    assert ids(db, min_radius_km=0, max_radius_km=5) == ["a", "b", "c"]
    assert ids(db, min_diameter_km=10) == ["b", "c", "f"]
    assert ids(db, max_radius_km=0) == ["a"]
    assert ids(db, max_eccentricity=0) == ["a"]
    assert ids(db, min_semi_major_axis_au=1, max_semi_major_axis_au=1) == ["b", "c"]
    assert ids(db, discovered_after="2000-01-01") == ["b", "f", "d"]
    assert ids(db, neo=True, pha=True) == ["b"]
    assert ids(db, neo=False, pha=False, named_only=False) == ids(db)
    assert ids(db, named_only=True) == ["b", "c", "f", "d", "e", "parent"]
    assert ids(db, parent="Parent") == ["c", "f"]
    assert ids(db, parent="absent") == []


def test_keyset_uses_exact_id_and_ignores_offset(sample):
    db, _ = sample
    assert ids(db, after="", limit=3, offset=999) == ["a", "b", "c"]
    assert ids(db, after="c", limit=3) == ["d", "e", "f"]
    assert ids(db, after="parent") == []
    assert ids(db, after="b", object_type="asteroid") == ["c", "e"]
    assert ids(db, after="a", min_radius_km=5, max_radius_km=5, neo=True) == ["b"]
    assert ids(db, after="b", named_only=True, parent="Parent") == ["c", "f"]
    walked, after = [], ""
    while page := ids(db, after=after, limit=2):
        walked.extend(page)
        after = page[-1]
    assert walked == ["a", "b", "c", "d", "e", "f", "parent"]


def test_v2_filters_and_legacy_rejection_remain_explicit(sample):
    db, v2 = sample
    if not v2:
        for filters in [
            {"orbit_class": "MBA"},
            {"max_moid_au": 0},
            {"max_condition_code": 0},
        ]:
            with pytest.raises(UnsupportedCatalogueFilter):
                db.find_objects(**filters)
        assert "orbit_class_code" not in db.find_objects()[0]
    else:
        assert ids(db, orbit_class="MBA", max_moid_au=0.05, max_condition_code=3) == [
            "a",
            "b",
            "f",
        ]
        assert ids(db, max_moid_au=0, max_condition_code=0) == ["a"]
        assert "orbit_class_code" in db.find_objects()[0]


def test_caller_text_stays_bound_not_sql(sample):
    db, v2 = sample
    assert ids(db, discovered_after="9999' OR 1=1 --") == []
    assert ids(db, parent="Parent' OR 1=1 --") == []
    assert ids(db, after="zz' OR 1=1 --") == []
    if v2:
        assert ids(db, orbit_class="MBA' OR 1=1 --") == []
    with pytest.raises(ValueError):
        db.find_objects(object_type="asteroid' OR 1=1 --")
    assert ids(db) == ["a", "b", "c", "f", "d", "e", "parent"]


def test_sqlite_nan_null_binding_compatibility(sample):
    db, _ = sample
    assert ids(db, max_radius_km=float("nan")) == ids(db)
    assert ids(db, max_radius_km=float("inf")) == ["a", "b", "c", "f"]


def test_keyset_seeks_existing_primary_index_without_sort(sample):
    db, _ = sample
    statements, connections = [], []
    original = db._conn

    @contextmanager
    def capture():
        with original() as conn:
            connections.append(conn)
            conn.set_trace_callback(statements.append)
            yield conn

    db._conn = capture
    assert ids(db, after="b", limit=2) == ["c", "d"]
    assert len(connections) == 1
    query = next(s for s in statements if "SELECT o.id," in s)
    with original() as conn:
        plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + query)]
    assert any("SEARCH o USING INDEX" in step and "(id>?)" in step for step in plan)
    assert not any("TEMP B-TREE" in step for step in plan)
    statements.clear()
    assert ids(db, object_type="planet") == ["parent"]
    query = next(s for s in statements if "SELECT o.id," in s)
    with original() as conn:
        plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + query)]
    assert any("idx_objects_type (object_type=?)" in step for step in plan)
