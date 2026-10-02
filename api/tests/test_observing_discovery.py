"""Bounded opt-in discovery: truthful sampling, modes, provenance and isolation."""

import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import threading
from unittest.mock import Mock
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from solar_db.observing import discovery as module
from solar_db.observing.inputs import PlanningError

BASE = dict(
    date="2026-10-01",
    timezone="Europe/London",
    lat=51.5,
    lon=-0.12,
    equipment_mode="naked_eye",
)
ROOT = Path(__file__).resolve().parents[2]


def star(target="bsc5p:hr1", **updates):
    return dict(
        id=target,
        name="A <source> star",
        aliases=["Alias"],
        families=["bright_star"],
        magnitude=0.0,
        magnitude_band="V",
        magnitude_flag=None,
        magnitude_code=None,
        major_axis_arcmin=None,
        minor_axis_arcmin=None,
        **updates,
    )


def deep(target="openngc:NGC1", extent=60):
    row = star(target)
    row.update(families=["deep_sky"], major_axis_arcmin=extent, magnitude=-1.0)
    return row


class FakeProvider:
    metadata = {"provider": "test-model"}

    def __init__(self, tweak=lambda body, index, value: value):
        self.tweak = tweak
        self.calls = []

    def positions(self, times, bodies):
        self.calls.append((list(times), bodies))
        result = {
            "sun_altitude_deg": np.full(len(times), -20.0),
            "moon_altitude_deg": np.full(len(times), 30.0),
        }
        for body in bodies:
            rows = [
                self.tweak(
                    body,
                    index,
                    dict(
                        altitude_deg=40.0,
                        azimuth_deg=0.0,
                        sun_separation_deg=90.0,
                        moon_separation_deg=60.0,
                        distance_au=None,
                    ),
                )
                for index in range(len(times))
            ]
            result[body] = {key: [row[key] for row in rows] for key in rows[0]}
        return result


@pytest.fixture
def fake_plan(monkeypatch):
    calls = []

    def plan(request, provider):
        calls.append(request)
        return {
            "method": {"provider": "test-model"},
            "targets": [
                {
                    "id": target,
                    "status": "windows_found",
                    "windows": [
                        {
                            "start_utc": request.window_start_utc,
                            "end_utc": request.window_end_utc,
                        }
                    ],
                }
                for target in request.targets
            ],
        }

    monkeypatch.setattr(module, "_plan", plan)
    return calls


def calculate(options=None, records=None, provider=None):
    request, choices = module.parse_discovery({**BASE, **(options or {})})
    return module._discover(
        request, choices, provider or FakeProvider(), records or {}, {}
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("equipment_mode", None),
        ("equipment_mode", "camera"),
        ("preference", []),
        ("shortlist_limit", True),
        ("shortlist_limit", 1.5),
        ("shortlist_limit", "2"),
        ("shortlist_limit", 0),
        ("shortlist_limit", 9),
        ("true_field_deg", 0),
        ("true_field_deg", True),
        ("true_field_deg", float("nan")),
        ("max_catalogue_v_magnitude", False),
        ("max_catalogue_v_magnitude", 31),
        ("lat", True),
        ("date", "2026-02-31"),
        ("targets", "sun"),
    ],
)
def test_invalid_inputs_fail_before_worker(field, value, monkeypatch):
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **k: pytest.fail("No worker for invalid input"),
    )
    with pytest.raises(PlanningError):
        module.discover_targets(**{**BASE, field: value})


def test_modes_have_distinct_explained_order_and_preserve_zero_negative_unknown(
    fake_plan,
):
    records = {row["id"]: row for row in [star(), deep()]}
    naked = calculate({"shortlist_limit": 2}, records)
    assert [c["id"] for c in naked["candidates"]] == ["moon", "bsc5p:hr1"]
    assert (
        naked["candidates"][0]["appearance"] is None
        and naked["candidates"][0]["brightness_status"] == "unknown"
    )
    assert naked["candidates"][1]["appearance"]["magnitude"] == 0
    binocular = calculate(
        {"equipment_mode": "binocular", "true_field_deg": 1, "shortlist_limit": 1},
        records,
    )
    assert binocular["candidates"][0]["id"] == "openngc:NGC1"
    assert binocular["candidates"][0]["appearance"]["magnitude"] == -1
    assert (
        binocular["candidates"][0]["field_context"] == "catalogue_extent_within_field"
    )
    telescope = calculate(
        {"equipment_mode": "telescope", "preference": "deep_sky", "shortlist_limit": 1},
        records,
    )
    assert telescope["candidates"][0]["id"] == "openngc:NGC1"
    assert telescope["candidates"][0]["field_context"] == "field_not_supplied"
    assert naked["candidates"][1]["aliases"] == ["Alias"]


def test_unknown_and_double_separation_are_never_invented_diameters():
    row = star()
    row.update(
        families=["bright_star", "double_star"],
        separation_arcsec=60,
        major_axis_arcmin=1,
    )
    assert module.field_context(row, 1) == "unknown_angular_extent"
    assert module.field_context(deep(extent=0), 1) == "unknown_angular_extent"
    assert module.field_context(deep(extent=61), 1) == "catalogue_extent_exceeds_field"


def test_explicit_v_cut_excludes_unknown_and_non_v_without_inferring_limits(fake_plan):
    rows = [star("bsc5p:hr1"), star("bsc5p:hr2"), star("bsc5p:hr3"), star("bsc5p:hr4")]
    rows[1]["magnitude"] = None
    rows[2]["magnitude_band"] = "B"
    rows[3]["magnitude"] = 1
    records = {row["id"]: row for row in rows}
    result = calculate({"max_catalogue_v_magnitude": 0, "shortlist_limit": 8}, records)
    assert [c["id"] for c in result["candidates"]] == ["bsc5p:hr1"]
    assert result["discovery"]["unknown_or_non_v_brightness_excluded"] == 10
    assert result["discovery"]["brightness_excluded"] == 1
    assert len(calculate({"shortlist_limit": 8}, records)["candidates"]) == 8


def test_exact_selected_window_horizon_moon_and_sun_constraints_screen_consistently(
    fake_plan,
):
    def tweak(body, index, point):
        if body == "mercury":
            point["sun_separation_deg"] = 29.99
        if body == "venus":
            point["moon_separation_deg"] = 29.99
        if body == "mars":
            point["altitude_deg"] = 29.99
        return point

    provider = FakeProvider(tweak)
    opts = dict(
        window_start_utc="2026-10-01T22:00:00Z",
        window_end_utc="2026-10-01T22:10:00Z",
        min_moon_separation_deg=30,
        shortlist_limit=8,
        horizon_mask=[
            {"azimuth_deg": 0, "min_altitude_deg": 30},
            {"azimuth_deg": 180, "min_altitude_deg": 30},
        ],
    )
    result = calculate(opts, provider=provider)
    ids = {c["id"] for c in result["candidates"]}
    assert {"mercury", "venus", "mars"}.isdisjoint(ids) and "moon" in ids
    assert len(provider.calls) == 1 and len(provider.calls[0][0]) == 3
    assert result["request"]["window_start_utc"] == opts["window_start_utc"]
    assert fake_plan[0].window_end_utc == opts["window_end_utc"]
    assert result["discovery"]["incomplete_between_samples"] is True


def test_empty_and_failed_refinement_do_not_promise_useful_windows(
    monkeypatch, fake_plan
):
    result = calculate({"min_altitude_deg": 90})
    assert result["candidates"] == [] and result["plan"] is None and not fake_plan
    assert "No refinement was performed" in result["method"]["window_note"]
    assert result["request"]["horizon_mask"] is None

    def none(request, provider):
        return {
            "method": {},
            "targets": [
                {"id": x, "windows": [], "status": "no_matching_window"}
                for x in request.targets
            ],
        }

    monkeypatch.setattr(module, "_plan", none)
    result = calculate()
    assert (
        result["candidates"] == []
        and result["discovery"]["selected_without_refined_window"] == 6
    )


def test_deterministic_tie_order_is_independent_of_source_iteration(fake_plan):
    rows = [star("bsc5p:hr2"), star("bsc5p:hr1")]
    a = calculate(
        {"preference": "stars", "shortlist_limit": 2}, {r["id"]: r for r in rows}
    )
    b = calculate(
        {"preference": "stars", "shortlist_limit": 2},
        {r["id"]: r for r in reversed(rows)},
    )
    assert a["candidates"] == b["candidates"]
    assert [r["id"] for r in a["candidates"]] == ["bsc5p:hr1", "bsc5p:hr2"]


def test_worker_timeout_failure_output_bound_and_capacity_cleanup(monkeypatch):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "builtin")
    for behavior in ("timeout", "exit", "oversize", "malformed"):

        def run(*args, **kwargs):
            assert kwargs["timeout"] == 35 and kwargs["stderr"] is subprocess.DEVNULL
            assert "51.5" not in str(args[0])
            if behavior == "timeout":
                raise subprocess.TimeoutExpired(args[0], 35)
            if behavior == "oversize":
                kwargs["stdout"].write(b"x" * (module.MAX_OUTPUT_BYTES + 1))
            if behavior == "malformed":
                kwargs["stdout"].write(b"[]")
            return SimpleNamespace(returncode=1 if behavior == "exit" else 0)

        monkeypatch.setattr(module.subprocess, "run", run)
        with pytest.raises(PlanningError, match="unavailable") as error:
            module.discover_targets(**BASE)
        assert error.value.status == 503
        assert module.PLANNING_CAPACITY.acquire(blocking=False)
        module.PLANNING_CAPACITY.release()
    assert module.PLANNING_CAPACITY.acquire(blocking=False)
    assert module.PLANNING_CAPACITY.acquire(blocking=False)
    try:
        with pytest.raises(PlanningError, match="busy"):
            module.discover_targets(**BASE)
    finally:
        module.PLANNING_CAPACITY.release()
        module.PLANNING_CAPACITY.release()


def test_rest_json_limits_duplicate_keys_and_no_store(monkeypatch):
    import api.main as api

    monkeypatch.setattr(api.limiter, "enabled", False)

    def checked(**payload):
        module.parse_discovery(payload)
        return {"schema_version": 1}

    monkeypatch.setattr(api, "discover_targets", checked)
    with TestClient(api.app) as client:
        good = client.post("/api/v1/observing/discover", json=BASE)
        assert good.status_code == 200 and good.headers["cache-control"] == "no-store"
        for raw in (
            '{"date":"a","date":"b"}',
            '{"lat":NaN}',
            "[]",
            '{"x":' + "[" * 500 + "0" + "]" * 500 + "}",
        ):
            assert (
                client.post(
                    "/api/v1/observing/discover",
                    content=raw,
                    headers={"Content-Type": "application/json"},
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/v1/observing/discover",
                content=b" " * 16385,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post("/api/v1/observing/discover?lat=1", json=BASE).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/observing/discover", json={**BASE, "targets": "sun"}
            ).status_code
            == 422
        )


def load_mcp():
    spec = importlib.util.spec_from_file_location(
        "discovery_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    return server


def test_actual_mcp_dispatcher_rejects_coercion_and_unknown_constraints(monkeypatch):
    server = load_mcp()
    monkeypatch.setattr(
        server,
        "discover_targets",
        lambda **p: pytest.fail("Invalid arguments reached planner"),
    )

    async def scenario():
        for change in (
            {"lat": True},
            {"shortlist_limit": "2"},
            {"max_cloud_percent": 0},
        ):
            with pytest.raises(Exception):
                await server.mcp.call_tool(
                    "discover_observing_targets", {**BASE, **change}
                )
        tool = next(
            t
            for t in await server.mcp.list_tools()
            if t.name == "discover_observing_targets"
        )
        assert tool.inputSchema["additionalProperties"] is False

    asyncio.run(scenario())


def test_discovery_hash_covers_only_bounded_new_sources():
    value = module.source_identity()
    assert value["files"] == ["observing/discovery.py", "observing/discovery_worker.py"]
    assert len(value["source_sha256"]) == 64


@pytest.mark.parametrize("provider", ["builtin", "jpl-de440s"])
def test_actual_bounded_catalogue_screen_and_refined_plan(provider, monkeypatch):
    if provider == "jpl-de440s" and not os.environ.get("JPL_TEST_KERNEL"):
        pytest.skip("Pinned kernel acceptance requires JPL_TEST_KERNEL")
    monkeypatch.setenv("OBSERVING_EPHEMERIS", provider)
    if provider == "jpl-de440s":
        monkeypatch.setenv("OBSERVING_JPL_KERNEL", os.environ["JPL_TEST_KERNEL"])
    result = module.discover_targets(**BASE, shortlist_limit=2)
    assert result["discovery"]["catalogue_records"] == 157
    assert result["discovery"]["unsupported_catalogue_records"] == 1
    assert len(result["candidates"]) == 2 and result["plan"] is not None
    assert result["method"] == result["plan"]["method"]
    assert result["method"]["provider"] == (
        "astropy-builtin" if provider == "builtin" else provider
    )
    assert all(t["windows"] for t in result["plan"]["targets"])
    assert result["plan"]["targets"][1]["catalogue"]["distance_au"] is None
    assert len(result["discovery"]["source_snapshots"]) == 2
    assert {x["id"] for x in result["candidates"]} == {
        x["id"] for x in result["plan"]["targets"]
    }


def test_mcp_discovery_shares_bounded_queue_and_keeps_unrelated_tools_responsive(
    monkeypatch,
):
    server = load_mcp()
    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    calls, lock = [], threading.Lock()

    def slow(**payload):
        with lock:
            index = len(calls)
            calls.append(payload)
        started[index].set()
        assert release.wait(3)
        return {"schema_version": 1}

    monkeypatch.setattr(server, "discover_targets", slow)
    db = Mock()
    db.search.return_value = []
    monkeypatch.setattr(server, "db", lambda: db)

    async def scenario():
        tasks = [
            asyncio.create_task(
                server.mcp.call_tool("discover_observing_targets", BASE)
            )
            for _ in range(2)
        ]
        try:
            for event in started:
                assert await asyncio.to_thread(event.wait, 1)
            await asyncio.wait_for(
                server.mcp.call_tool("search", {"query": "Moon"}), 0.5
            )
            assert (await server.discover_observing_targets(**BASE))["status"] == 503
            assert (
                await server.plan_observing_night(
                    **{k: v for k, v in BASE.items() if k != "equipment_mode"}
                )
            )["status"] == 503
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert (await server.discover_observing_targets(**BASE))["status"] == 503
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert len(calls) == 2 and db.search.call_count == 1
    assert server._planning_slots.acquire(blocking=False)
    assert server._planning_slots.acquire(blocking=False)
    server._planning_slots.release()
    server._planning_slots.release()


def test_below_geometric_moon_disables_separation_cut_and_polar_day_stays_inconclusive(
    fake_plan,
):
    class BelowMoon(FakeProvider):
        def positions(self, times, bodies):
            data = super().positions(times, bodies)
            data["moon_altitude_deg"][:] = -1
            return data

    def near(body, index, point):
        point["moon_separation_deg"] = 0
        return point

    result = calculate({"min_moon_separation_deg": 180}, provider=BelowMoon(near))
    assert len(result["candidates"]) == 6

    class Daylight(FakeProvider):
        def positions(self, times, bodies):
            data = super().positions(times, bodies)
            data["sun_altitude_deg"][:] = 10
            return data

    result = calculate(provider=Daylight())
    assert (
        result["plan"] is None
        and result["discovery"]["coarse_matching_candidates"] == 0
    )
    assert result["discovery"]["incomplete_between_samples"] is True
