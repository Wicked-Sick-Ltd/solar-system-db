# Meteor Showers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the IAU Meteor Data Center shower database into the catalogue (schema v3), link showers to their parent comets and asteroids, and expose them through the REST API and MCP server.

**Architecture:** One new ingest module with a pure parser and a thin writer, following `scripts/ingest_mpc.py`; one new table; a build stage; data-access methods guarded by `_has_table` so v2 files keep working; two API routes and two MCP tools that mirror them. Weekly refresh rides the nightly build (the file is small; fetching it nightly is harmless).

**Tech Stack:** Python 3.12 stdlib (`csv` with `delimiter="|"`), sqlite3, FastAPI, FastMCP, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-full-catalogue-rollout-and-extensions-design.md` §3.

## Global Constraints
- Schema v3 is a strict superset of v2; `PRAGMA user_version = 3`; no v2 column changes.
- Status labels come from the file header's legend, not from code constants; `verify.py` asserts the legend still names "established" and "working".
- Tests offline only: fixture `tests/fixtures/mdc_showers.txt`.
- Source credit string everywhere: `IAU Meteor Data Center (Jenniskens et al. 2020; Hajdukova & Rudawska)`.

---

### Task 1: Schema v3 — `meteor_showers`

**Files:**
- Modify: `schema/schema.sql` (append before `PRAGMA user_version`; bump to 3)
- Test: `api/tests/test_schema.py`

**Interfaces:**
- Produces: table `meteor_showers` exactly as spec §3.2; indexes `idx_showers_code`, `idx_showers_parent`, `idx_showers_status`.

- [ ] **Step 1: Failing test** (append):
```python
def test_schema_v3_has_meteor_showers():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(meteor_showers)")}
    assert {"iau_no", "ad_no", "code", "name", "status_code", "status_label", "solar_longitude_deg",
            "ra_deg", "dec_deg", "vg_km_s", "parent_body", "parent_object_id", "source"} <= cols
    idx = {r[1] for r in conn.execute("PRAGMA index_list(meteor_showers)")}
    assert {"idx_showers_code", "idx_showers_parent", "idx_showers_status"} <= idx
```
- [ ] **Step 2: Run** `uv run pytest api/tests/test_schema.py -q` → FAIL (`user_version` 2 / no such table).
- [ ] **Step 3: Implement**: add the DDL from spec §3.2 verbatim and change `PRAGMA user_version = 2;` to `3`. Also update any existing test asserting `== 2` to `>= 2`.
- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(schema): v3 meteor_showers`.

### Task 2: `scripts/ingest_showers.py` — parser

**Files:**
- Create: `scripts/ingest_showers.py`, `tests/fixtures/mdc_showers.txt`
- Test: `api/tests/test_ingest_showers.py`

**Interfaces:**
- Produces: `MDC_URL = "https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt"`; `parse_legend(header_lines: list[str]) -> dict[int, str]`; `parse_showers(text: str) -> list[dict]` returning dicts keyed exactly as the `meteor_showers` columns (minus `parent_object_id`, `source`), numeric fields as `float|int|None`; `fetch_showers() -> str` (uses `common.fetch_text`).

- [ ] **Step 1: Fixture**: download the real file once (`curl -o tests/fixtures/mdc_showers.txt https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt`) and cut it to the full header plus about 40 data rows chosen to include: Geminids (GEM, parent `3200 Phaethon`), Perseids (PER, parent `109P/Swift-Tuttle`), eta Aquariids (ETA, parent `1P/Halley`), one row with an empty parent, one "removed" status row, one "working list" row, and one shower with two parameter sets (two `AdNo` values). Note the row count you kept in the test.
- [ ] **Step 2: Failing tests**:
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ingest_showers import parse_legend, parse_showers  # noqa: E402
FIX = (ROOT / "tests" / "fixtures" / "mdc_showers.txt").read_text()

def test_legend_names_established_and_working():
    legend = parse_legend([l for l in FIX.splitlines() if l.startswith(":") or l.startswith("#")])
    labels = " ".join(legend.values()).lower()
    assert "established" in labels and "working" in labels

def test_parse_geminids():
    rows = parse_showers(FIX)
    gem = [r for r in rows if r["code"] == "GEM"]
    assert gem and gem[0]["iau_no"] == 4 and gem[0]["name"].lower().startswith("geminids")
    assert 250 < gem[0]["solar_longitude_deg"] < 265 and 30 < gem[0]["vg_km_s"] < 40
    assert "Phaethon" in gem[0]["parent_body"]

def test_multiple_parameter_sets_keep_distinct_ad_no():
    rows = parse_showers(FIX)
    keys = [(r["iau_no"], r["ad_no"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert any(sum(1 for k in keys if k[0] == iau) > 1 for iau, _ in keys)

def test_empty_fields_become_none():
    rows = parse_showers(FIX)
    assert any(r["parent_body"] is None for r in rows)
    assert all(isinstance(r["status_code"], int) for r in rows)
```
Adjust the GEM ranges only if the fixture's values sit outside them (the MDC gives LaSun ≈ 262°, Vg ≈ 34 km/s).
- [ ] **Step 3: Run** → FAIL (module missing).
- [ ] **Step 4: Implement**:
```python
"""IAU Meteor Data Center shower list: pipe-delimited, quoted, header legend for status codes."""
from __future__ import annotations
import csv, io, re, sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fetch_text  # noqa: E402

MDC_URL = "https://www.ta3.sk/IAUC22DB/MDC2007/Etc/streamfulldata.txt"
SOURCE_NAME = "IAU Meteor Data Center (Jenniskens et al. 2020; Hajdukova & Rudawska)"
# column order in the data rows (verified against the 2022-02-28 header)
COLS = ["lp", "iau_no", "ad_no", "code", "name", "activity", "status_code", "solar_longitude_deg", "ra_deg", "dec_deg",
        "dra_deg_per_day", "ddec_deg_per_day", "vg_km_s", "a_au", "q_au", "e", "peri_deg", "node_deg", "incl_deg",
        "n_members", "shower_group", "cg", "parent_body", "technique", "reference", "submitted_on"]
_LEGEND = re.compile(r"^[:#]\s*(-?\d+)\s*[-=:]\s*(.+?)\s*$")

def _num(v: str, kind=float):
    v = (v or "").strip()
    if v in ("", "-", "--", "?"):
        return None
    try:
        return kind(v)
    except ValueError:
        return None

def parse_legend(header_lines: list[str]) -> dict[int, str]:
    legend: dict[int, str] = {}
    in_status = False
    for line in header_lines:
        low = line.lower()
        if "status" in low and ("s =" in low or "s:" in low or "(s)" in low):
            in_status = True
            continue
        if in_status:
            m = _LEGEND.match(line)
            if m:
                legend[int(m.group(1))] = m.group(2)
            elif legend:
                in_status = False
    return legend

def parse_showers(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    header = [l for l in lines if l.startswith(":") or l.startswith("#")]
    legend = parse_legend(header)
    data = [l for l in lines if l and not (l.startswith(":") or l.startswith("#"))]
    out = []
    for rec in csv.reader(io.StringIO("\n".join(data)), delimiter="|", quotechar='"', skipinitialspace=True):
        if len(rec) < len(COLS):
            continue
        row = {k: (v.strip() or None) for k, v in zip(COLS, rec)}
        for k in ("iau_no", "ad_no", "status_code", "n_members"):
            row[k] = _num(row[k], int)
        for k in ("solar_longitude_deg", "ra_deg", "dec_deg", "dra_deg_per_day", "ddec_deg_per_day", "vg_km_s",
                  "a_au", "q_au", "e", "peri_deg", "node_deg", "incl_deg"):
            row[k] = _num(row[k])
        row.pop("lp", None); row.pop("cg", None)
        row["status_label"] = legend.get(row["status_code"]) if row["status_code"] is not None else None
        if row["iau_no"] is None or not row["code"]:
            continue
        out.append(row)
    return out

def fetch_showers(timeout: int = 120) -> str:
    return fetch_text(MDC_URL, timeout=timeout)
```
If the real header positions differ from `COLS` (check the two ruler lines in the fixture), fix `COLS` here and say so in the commit message; the tests are the arbiter.
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `feat(ingest): IAU MDC meteor-shower parser with header-derived status legend`.

### Task 3: Writer, parent resolution, build stage, verify

**Files:**
- Modify: `scripts/ingest_showers.py` (add `resolve_parent`, `write_showers`), `scripts/build_full.py` (stage + `--skip-showers`), `scripts/verify.py`
- Test: `api/tests/test_ingest_showers.py` (extend)

**Interfaces:**
- Produces: `resolve_parent(conn, parent_body: str | None) -> str | None`; `write_showers(conn, rows: list[dict]) -> dict[str, int]` returning `{"showers": n, "parents_resolved": m, "parents_unresolved": k}`; `build_full.stage_showers(conn, *, offline: bool) -> dict[str, int]`.

- [ ] **Step 1: Failing tests** (append; reuse the `_db()` helper pattern from `test_ingest_mpc_cad.py`, which loads the SBDB asteroid fixture — Phaethon `3200` is a numbered asteroid; add `1P/Halley` by also writing the comet fixture):
```python
from ingest_showers import resolve_parent, write_showers  # noqa: E402
from ingest_sbdb import load_fixture as load_sbdb, map_all, write_mapped  # noqa: E402
import sqlite3

def _db():
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema" / "schema.sql").read_text())
    conn.execute("INSERT INTO objects (id, name, object_type) VALUES ('sun', 'Sun', 'star')")
    for fx in ("sbdb_asteroids.json", "sbdb_comets.json"):
        fields, data = load_sbdb(ROOT / "tests" / "fixtures" / fx)
        write_mapped(conn, map_all(fields, data))
    return conn

def test_resolve_parent_by_comet_designation_and_asteroid_number():
    conn = _db()
    assert resolve_parent(conn, "1P/Halley") is not None
    assert resolve_parent(conn, "3200 Phaethon") == resolve_parent(conn, "3200")
    assert resolve_parent(conn, None) is None and resolve_parent(conn, "unknown body") is None

def test_write_showers_counts_and_links():
    conn = _db()
    counts = write_showers(conn, parse_showers(FIX))
    assert counts["showers"] >= 30 and counts["parents_resolved"] >= 2
    row = conn.execute("SELECT parent_object_id FROM meteor_showers WHERE code='ETA' LIMIT 1").fetchone()
    assert row and row[0] is not None
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE table_name='meteor_showers'").fetchone()[0] == 1
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** in `ingest_showers.py`:
```python
_NUM = re.compile(r"^\s*\(?(\d+)\)?")
_COMET = re.compile(r"\b(\d+[PDCIX])(?:/|\b)")

def resolve_parent(conn, parent_body: str | None) -> str | None:
    if not parent_body:
        return None
    m = _COMET.search(parent_body)
    if m:
        r = conn.execute("SELECT object_id FROM designations WHERE designation = ? LIMIT 1", (m.group(1),)).fetchone()
        if r: return r[0]
        r = conn.execute("SELECT id FROM objects WHERE designation LIKE ? AND object_type='comet' LIMIT 1", (m.group(1) + "/%",)).fetchone()
        if r: return r[0]
    m = _NUM.match(parent_body)
    if m:
        r = conn.execute("SELECT object_id FROM designations WHERE designation = ? AND kind='number' LIMIT 1", (m.group(1),)).fetchone()
        if r: return r[0]
    name = parent_body.split("/")[-1].strip()
    r = conn.execute("SELECT id FROM objects WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)).fetchone()
    return r[0] if r else None

def write_showers(conn, rows: list[dict[str, Any]]) -> dict[str, int]:
    n = res = unres = 0
    cols = ["iau_no", "ad_no", "code", "name", "activity", "status_code", "status_label", "solar_longitude_deg",
            "ra_deg", "dec_deg", "dra_deg_per_day", "ddec_deg_per_day", "vg_km_s", "a_au", "q_au", "e", "peri_deg",
            "node_deg", "incl_deg", "n_members", "shower_group", "parent_body", "parent_object_id", "technique",
            "reference", "submitted_on", "source"]
    sql = f"INSERT OR REPLACE INTO meteor_showers ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})"
    for r in rows:
        pid = resolve_parent(conn, r.get("parent_body"))
        res += pid is not None; unres += pid is None and bool(r.get("parent_body"))
        r = {**r, "parent_object_id": pid, "source": SOURCE_NAME}
        conn.execute(sql, [r.get(c) for c in cols]); n += 1
    from common import add_source
    if n:
        add_source(conn, object_id=None, table_name="meteor_showers", source_name=SOURCE_NAME, source_url=MDC_URL)
    conn.commit()
    return {"showers": n, "parents_resolved": res, "parents_unresolved": unres}
```
In `build_full.py`: `import ingest_showers`; 
```python
def stage_showers(conn, *, offline: bool) -> dict[str, int]:
    text = (FIXTURES / "mdc_showers.txt").read_text() if offline else ingest_showers.fetch_showers()
    return ingest_showers.write_showers(conn, ingest_showers.parse_showers(text))
```
add `p.add_argument("--skip-showers", action="store_true")` and call the stage after CAD (before enrichment) guarded by the flag, printing `Stage 6b: meteor showers`. In `verify.py` (when `user_version >= 3`): `meteor_showers` count ≥ 30 offline / ≥ 100 online (use the existing offline/online detection the file uses for row floors, or `build_meta.mode`), no duplicate `(iau_no, ad_no)`, at least one `status_label` containing "stablished".
- [ ] **Step 4: Run whole suite** → PASS (conftest builds offline DB, so `mdc_showers.txt` must exist). **Step 5: Commit** `feat(build): meteor showers stage with parent-body resolution; verify floors`.

### Task 4: Data access, API routes, MCP tools

**Files:**
- Modify: `solar_db/data_access.py`, `api/main.py`, `mcp-server/server.py`
- Test: `api/tests/test_api_v2_surface.py` (extend), `mcp-server/tests/test_mcp_smoke.py` (extend)

**Interfaces:**
- Produces: `SolarDB.list_meteor_showers(*, established_only: bool = False, active_on: str | None = None, limit: int = 500) -> list[dict]`; `SolarDB.get_meteor_shower(code_or_name: str) -> dict | None` (all parameter sets under `parameter_sets`, parent object summary under `parent`); `get_object()` gains `meteor_showers: list[dict]`; routes `GET /api/v1/meteor-showers`, `GET /api/v1/meteor-showers/{code}`; MCP tools `list_meteor_showers`, `get_meteor_shower`.

- [ ] **Step 1: Failing API tests** (append to `test_api_v2_surface.py`, which already has a `client`):
```python
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

def test_parent_object_lists_its_showers(client):
    d = client.get("/api/v1/objects/3200").json()
    assert any(s["code"] == "GEM" for s in d["meteor_showers"])
```
and an MCP smoke test calling `get_meteor_shower("Perseids")` expecting `code == "PER"`.
- [ ] **Step 2: Run** → FAIL (404s). **Step 3: Implement**. Solar longitude of a date for `active_on`: reuse `solar_db/positions.py`'s Earth heliocentric longitude if exposed, else the low-precision formula `L = (280.460 + 0.9856474 * n) % 360` with `n` = days since J2000 (accuracy ~1°, fine for ±15°); the shower is active if the circular difference between `L` and `solar_longitude_deg` is ≤ 15. In `data_access`, group rows by `iau_no` for `get_meteor_shower` (match `code` case-insensitively or `name` NOCASE), and attach `parent` = `{id, name, designation, object_type}` when `parent_object_id` is set. All methods return `[]`/`None` when `_has_table(conn, "meteor_showers")` is false. Routes return `{"items": [...], "count": n}` for the list to match `/api/v1/objects`. MCP tools call the same methods with docstrings that name the source.
- [ ] **Step 4: Run all tests** → PASS. **Step 5: Commit** `feat(api): meteor showers list/detail, parent links, MCP tools`.

### Task 5: Docs and README

**Files:** `README.md` (Schema overview + sources table + endpoints list), `api/README.md`, `mcp-server/README.md`.
- [ ] Add `meteor_showers` to the schema overview; add the IAU MDC row to the sources table with the citation string; add the two endpoints and two tools with one example each (`/api/v1/meteor-showers/GEM`). Commit `docs: meteor showers`.

## Self-review
- Spec §3.1 source/legend → Task 2; §3.2 schema → Task 1; §3.3 build/API/MCP/verify → Tasks 3, 4; web card is the later web-repo PR (out of this plan by design).
- Placeholders: none. Fixture composition is specified (which showers, which statuses).
- Types: `parse_showers -> list[dict]` consumed by `write_showers(conn, rows)` in Task 3 and `stage_showers`; `resolve_parent(conn, str|None) -> str|None` consistent.
