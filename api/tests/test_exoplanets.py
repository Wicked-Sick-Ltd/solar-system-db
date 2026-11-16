"""Ingest, scientific coordinate checks, and the shared REST/MCP contract."""
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solar_db import SolarDB
from solar_db.galactic import coordinates

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from ingest_exoplanets import write_exoplanets, stable_id  # noqa: E402


@pytest.fixture
def catalogue(tmp_path):
    path = tmp_path / 'exo.sqlite'
    conn = sqlite3.connect(path)
    conn.executescript((ROOT / 'schema/schema.sql').read_text())
    rows = json.loads((ROOT / 'tests/fixtures/exoplanets.json').read_text())
    yield conn, rows, SolarDB(path)
    conn.close()


def test_snapshot_idempotent_and_separate(catalogue):
    conn, rows, db = catalogue
    first = write_exoplanets(conn, rows)
    assert first == write_exoplanets(conn, list(reversed(rows)))
    assert first['hosts'] == 4
    assert len(db.get_exoplanet_host('TRAPPIST-1')['planets']) == 7
    assert db.get_exoplanet('Proxima Cen b')['source_data']['pl_bmassprov']
    assert conn.execute('SELECT count(*) FROM objects').fetchone()[0] == 0
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


def test_unknown_invalid_and_limited_distances_never_map(catalogue):
    conn, rows, db = catalogue
    samples = []
    for i, value in enumerate([None, 0, -3, 'NaN', 4]):
        row = dict(rows[0], hostname=f'Host {i}', pl_name=f'Planet {i}', sy_dist=value)
        row['sy_distlim'] = 1 if i == 4 else 0
        samples.append(row)
    write_exoplanets(conn, samples)
    result = db.galaxy_map()
    assert result['unmapped_hosts'] == 5 and result['results'] == []
    assert db.list_exoplanets()['total'] == 5


def test_snapshot_removes_withdrawn_planets_and_rejects_duplicates(catalogue):
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    with pytest.raises(ValueError):
        write_exoplanets(conn, rows + [rows[0]])
    assert db.list_exoplanets()['total'] == len(rows)
    write_exoplanets(conn, rows[1:])
    assert db.get_exoplanet(rows[0]['pl_name']) is None


def test_coordinates_known_galactic_axes():
    # ICRS Galactic centre and north pole (independent published frame landmarks).
    centre = coordinates(266.4051, -28.936175, 100)
    assert centre['x_pc'] == pytest.approx(100, abs=0.001)
    assert centre['y_pc'] == pytest.approx(0, abs=0.001)
    pole = coordinates(192.85948, 27.12825, 100)
    assert pole['z_pc'] == pytest.approx(100, abs=0.001)
    for p in (centre, pole):
        assert math.sqrt(sum(p[k]**2 for k in ('x_pc','y_pc','z_pc'))) == pytest.approx(100)
    assert centre['galactocentric_x_pc'] == pytest.approx(-8022, abs=0.1)
    assert coordinates(None, 0, 1) == {}
    assert coordinates(360, 0, 1) == {}


def test_host_selection_prefers_valid_tuple_and_keeps_provenance(catalogue):
    conn, rows, db = catalogue
    first = dict(rows[0], hostname='Same host', pl_name='First', sy_dist=None)
    second = dict(rows[0], hostname='Same host', pl_name='Second', sy_dist=2)
    write_exoplanets(conn, [first, second])
    host = db.get_exoplanet_host('Same host')
    assert host['coordinate_source_planet'] == 'Second' and host['distance_pc'] == 2
    assert host['coordinate_metadata']['galactocentric']['galcen_distance_pc'] == 8122


def test_rest_mcp_filters_detail_map_and_validation(catalogue, monkeypatch):
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    import api.main as api
    monkeypatch.setattr(api, 'db', db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    spec = importlib.util.spec_from_file_location('exo_mcp', ROOT / 'mcp-server/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, 'db', lambda: db)
    client = TestClient(api.app)
    query = dict(q='TRAPPIST', discovery_method='Transit', max_distance_pc=20, limit=2, offset=1)
    assert client.get('/api/v1/exoplanets', params=query).json() == server.list_exoplanets(**query)
    assert server.list_exoplanets(**query)['total'] == 7
    assert server.list_exoplanets(q="' OR 1=1 --")['total'] == 0
    assert server.list_exoplanets(q='%')['total'] == 0
    assert client.get('/api/v1/exoplanets/Proxima Cen b').json() == server.get_exoplanet('Proxima Cen b')
    hostid = stable_id('host', 'TRAPPIST-1')
    assert client.get('/api/v1/exoplanet-hosts/'+hostid).json() == server.get_exoplanet_host(hostid)
    assert client.get('/api/v1/galaxy?limit=1').json() == server.galaxy_map(limit=1)
    assert server.galaxy_map(limit=1)['truncated'] is True
    assert len(server.galaxy_map(max_distance_pc=2)['results']) == 1
    assert client.get('/api/v1/exoplanets/missing').status_code == 404
    for querystring in ('limit=0', 'max_distance_pc=-1', 'max_distance_pc=nan', 'max_distance_pc=inf'):
        assert client.get('/api/v1/galaxy?'+querystring).status_code == 422
    with pytest.raises(ValueError):
        server.galaxy_map(max_distance_pc=float('nan'))


def _nested(depth):
    return '[' * depth + ']' * depth


def _rest_and_mcp(db, monkeypatch):
    import api.main as api
    monkeypatch.setattr(api, 'db', db)
    monkeypatch.setattr(api.limiter, "enabled", False)
    spec = importlib.util.spec_from_file_location('exo_mcp_nested', ROOT / 'mcp-server/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, 'db', lambda: db)
    return TestClient(api.app, raise_server_exceptions=False), server


# 100000 overflows the C decoder; 2000 decodes but is too deep to serialise.
@pytest.mark.parametrize('depth', [100000, 2000])
def test_nested_source_data_reports_unavailable(catalogue, monkeypatch, depth):
    from solar_db.response_snapshot import CatalogueReadError
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    with conn:
        conn.execute('UPDATE exoplanets SET source_data=? WHERE name=?', (_nested(depth), 'Proxima Cen b'))
    client, server = _rest_and_mcp(db, monkeypatch)
    unavailable = {'detail': 'Exoplanet catalogue is temporarily unavailable.'}
    host = stable_id('host', 'Proxima Cen')
    for path in ('/api/v1/exoplanets', '/api/v1/exoplanets/Proxima Cen b', '/api/v1/exoplanet-hosts/' + host):
        response = client.get(path)
        assert response.status_code == 503, path
        assert response.json() == unavailable
    for call in (server.list_exoplanets, lambda: server.get_exoplanet('Proxima Cen b'),
                 lambda: server.get_exoplanet_host(host)):
        with pytest.raises(CatalogueReadError):
            call()
    other = client.get('/api/v1/exoplanets', params={'q': 'TRAPPIST'})
    assert other.status_code == 200 and other.json()['total'] == 7


def test_catalogue_json_depth_limit_and_blob_cells(catalogue, monkeypatch):
    from solar_db.exoplanets import MAX_JSON_DEPTH, catalogue_json
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    with conn:
        conn.execute('UPDATE exoplanets SET source_data=? WHERE name=?', (_nested(MAX_JSON_DEPTH), 'Proxima Cen b'))
        conn.execute('UPDATE exoplanets SET source_data=? WHERE name=?', (b'{"pl_name": "51 Peg b"}', '51 Peg b'))
    client, server = _rest_and_mcp(db, monkeypatch)
    assert client.get('/api/v1/exoplanets/Proxima Cen b').status_code == 200
    assert server.get_exoplanet('51 Peg b')['source_data'] == {'pl_name': '51 Peg b'}
    assert catalogue_json(json.dumps({'k': '[' * 100000})) == {'k': '[' * 100000}
    for text in (_nested(MAX_JSON_DEPTH + 1), '{"a":' * 5000 + '1' + '}' * 5000, '"' + '[' * 100000):
        with pytest.raises(ValueError):
            catalogue_json(text)


def test_nested_host_metadata_reports_unavailable(catalogue, monkeypatch):
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    host = stable_id('host', 'TRAPPIST-1')
    with conn:
        conn.execute('UPDATE exoplanet_hosts SET coordinate_metadata=? WHERE id=?', (_nested(100000), host))
    client, _ = _rest_and_mcp(db, monkeypatch)
    response = client.get('/api/v1/exoplanet-hosts/' + host)
    assert response.status_code == 503
    assert client.get('/api/v1/exoplanets/Proxima Cen b').status_code == 200


def test_nested_identity_payload_reads_as_invalid(catalogue, monkeypatch):
    conn, rows, db = catalogue
    write_exoplanets(conn, rows)
    with conn:
        conn.execute('INSERT OR REPLACE INTO catalogue_identity VALUES (1,?)', (_nested(400000),))
    client, _ = _rest_and_mcp(db, monkeypatch)
    response = client.get('/api/v1/exoplanets')
    assert response.status_code == 200
    snapshot = response.json()['catalogue_snapshot']
    assert (snapshot['status'], snapshot['reason']) == ('unknown', 'invalid_metadata')


def test_old_catalogue_reports_unavailable(catalogue):
    conn, _, db = catalogue
    conn.execute('DROP TABLE exoplanets')
    conn.execute('DROP TABLE exoplanet_hosts')
    conn.commit()
    assert db.list_exoplanets()['available'] is False
    assert db.galaxy_map()['available'] is False
    assert db.get_exoplanet('anything') is None
