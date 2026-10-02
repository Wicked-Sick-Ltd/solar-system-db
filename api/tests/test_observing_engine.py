"""Offline numerical/contract tests; authoritative fixtures were retrieved separately."""

from datetime import datetime
import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import numpy as np
import pytest

from solar_db.observing import PlanningError, plan_night
from solar_db.observing.ephemeris import BuiltinEphemeris
from solar_db.observing.inputs import NightInput
from solar_db.observing.planner import threshold_events, intervals

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = json.loads(
    (Path(__file__).parent / "fixtures/observing-horizons.json").read_text()
)["cases"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Tests must not fetch ephemerides, Earth orientation or references."
        )

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)


def args(**changes):
    return dict(
        date="2026-10-01", timezone="Europe/London", lat=51.5, lon=-0.12, **changes
    )


@pytest.mark.parametrize(
    "date,hours", [("2026-03-28", 23), ("2026-10-24", 25), ("2026-10-01", 24)]
)
def test_local_noon_nights_honour_dst(date, hours):
    request = NightInput.parse(date, "Europe/London", 51.5, -0.12)
    assert (request.end - request.start).total_seconds() == hours * 3600
    assert (
        request.start.astimezone(__import__("zoneinfo").ZoneInfo(request.timezone)).hour
        == 12
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("date", "2026-02-30"),
        ("date", "tomorrow"),
        ("date", []),
        ("date", "1800-01-01"),
        ("timezone", "not/a/zone"),
        ("timezone", []),
        ("timezone", "/etc/passwd"),
        ("lat", float("nan")),
        ("lat", 90.001),
        ("lat", True),
        ("lat", []),
        ("lon", float("inf")),
        ("lon", -180.1),
        ("targets", "sun"),
        ("targets", "earth"),
        ("targets", "moon,moon"),
        ("targets", ""),
        ("targets", []),
        ("targets", "x" * 101),
        ("min_altitude_deg", -1),
        ("min_altitude_deg", 91),
        ("min_altitude_deg", True),
        ("sun_altitude_deg", -10),
        ("sun_altitude_deg", float("nan")),
        ("min_moon_separation_deg", 181),
    ],
)
def test_invalid_inputs_fail_before_calculation(field, value):
    params = args()
    params[field] = value
    factory = Mock()
    with pytest.raises(PlanningError):
        plan_night(**params, provider_factory=factory)
    factory.assert_not_called()


def test_missing_civil_date_is_not_silently_normalized():
    with pytest.raises(PlanningError, match="does not exist"):
        NightInput.parse("2011-12-30", "Pacific/Apia", -13.8, -171.75)


def test_raw_coordinate_bounds_precede_rounding_and_zero_is_real():
    request = NightInput.parse("2026-10-01", "UTC", 0, 0, min_altitude_deg=0)
    assert request.lat == request.lon == request.min_altitude_deg == 0


@pytest.mark.parametrize("case", REFERENCES, ids=lambda c: c["case"])
def test_builtin_against_cited_jpl_horizons_references(case):
    request = NightInput.parse(
        case["time_utc"][:10], "UTC", case["lat"] or 0, case["lon"] or 0
    )
    engine = BuiltinEphemeris(request)
    t = datetime.fromisoformat(case["time_utc"].replace("Z", "+00:00")).timestamp()
    reference = case["reference"]
    if case["lat"] is None:
        phase = engine.lunar_phase(t)
        assert (
            abs(phase["illumination_fraction"] - reference["illumination_fraction"])
            < 0.001
        )
        assert abs(phase["elongation_deg"] - reference["elongation_deg"]) < 0.1
    elif case["body"] == "sun":
        result = engine.positions([t], ("moon",))
        assert abs(result["sun_altitude_deg"][0] - reference["altitude_deg"]) < 0.1
    else:
        result = engine.positions([t], (case["body"],))[case["body"]]
        assert abs(result["altitude_deg"][0] - reference["altitude_deg"]) < 0.1
        delta = (result["azimuth_deg"][0] - reference["azimuth_deg"] + 180) % 360 - 180
        assert abs(delta) < 0.1
        assert result["distance_au"][0] == pytest.approx(
            reference["distance_au"], rel=0.001
        )
        assert abs(result["sun_separation_deg"][0] - reference["elongation_deg"]) < 0.1


def test_grazing_window_between_samples_is_found_by_extremum_refinement():
    # All 5-minute samples fail; a 20-second window exists around second75.
    function = lambda t: 100 - (t - 75) ** 2
    grid = [0, 300, 600]
    assert all(function(t) < 0 for t in grid)
    roots, unresolved = threshold_events(function, grid)
    assert sorted(roots) == pytest.approx([65, 85], abs=1)
    assert not unresolved
    windows = intervals(roots, 0, 600, lambda t: function(t) >= 0)
    assert len(windows) == 1


def test_tangent_is_unresolved_instead_of_confident_empty_window():
    function = lambda t: -((t - 75) ** 2)
    _, unresolved = threshold_events(function, [0, 300, 600])
    assert unresolved


def test_out_of_iers_coverage_is_unavailable_not_extrapolated():
    with pytest.raises(PlanningError) as error:
        plan_night("2100-01-01", "UTC", 0, 0)
    assert error.value.status == 503


def test_actual_night_has_bounded_samples_and_independent_moon_position():
    result = plan_night(**args(targets="moon,saturn", min_moon_separation_deg=30))
    assert result["schema_version"] == 1
    assert result["night"]["duration_hours"] == 24
    assert result["method"]["provider"] == "astropy-builtin"
    assert result["method"]["root_tolerance_seconds"] == 1
    assert "not physical accuracy" in result["method"]["accuracy_note"]
    assert 0 <= result["moon"]["illumination_fraction"] <= 1
    assert len(result["moon"]["samples"]) == 289
    for target in result["targets"]:
        assert len(target["samples"]) == 289
        for point in target["samples"]:
            assert all(
                np.isfinite(v)
                for k, v in point.items()
                if k not in ("time_utc", "horizon_altitude_deg")
            )
            assert -90 <= point["altitude_deg"] <= 90
            assert 0 <= point["azimuth_deg"] < 360
            assert point["distance_au"] > 0
        for window in target["windows"]:
            assert (
                result["night"]["start_utc"]
                <= window["start_utc"]
                < window["end_utc"]
                <= result["night"]["end_utc"]
            )
    assert result["targets"][0]["samples"][0]["moon_separation_deg"] == 0


def test_rest_and_mcp_share_contract_and_report_invalid_or_unavailable(monkeypatch):
    import api.main as api

    monkeypatch.setattr(api.limiter, "enabled", False)
    fake = {"schema_version": 1, "targets": []}
    monkeypatch.setattr(api, "plan_night", lambda *a, **kw: fake)
    with TestClient(api.app) as client:
        r = client.get("/api/v1/observing/night", params=args())
        assert r.status_code == 200 and r.json() == fake
        assert r.headers["Cache-Control"] == "no-store"
        for suffix in ("&lat=0", "&lat[]=0", "&unknown=1"):
            assert (
                client.get(
                    "/api/v1/observing/night?date=2026-10-01&timezone=UTC&lat=0&lon=0"
                    + suffix
                ).status_code
                == 422
            )

        def unavailable(*a, **kw):
            raise PlanningError("Data unavailable.", 503)

        monkeypatch.setattr(api, "plan_night", unavailable)
        r = client.get("/api/v1/observing/night", params=args())
        assert r.status_code == 503 and r.json() == {"detail": "Data unavailable."}
    spec = importlib.util.spec_from_file_location(
        "observing_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "plan_night", lambda *a, **kw: fake)
    assert asyncio.run(server.plan_observing_night(**args())) == fake
    monkeypatch.setattr(server, "plan_night", unavailable)
    assert asyncio.run(server.plan_observing_night(**args())) == {
        "error": "Data unavailable.",
        "status": 503,
    }


class SyntheticProvider:
    """Exercises interval rules independently of the ephemeris implementation."""

    def __init__(self, request):
        self.start = request.start.timestamp()
        self.metadata = {"provider": "synthetic test"}

    def positions(self, timestamps, bodies):
        ts = np.array(timestamps)
        moon_alt = (self.start + 43200 - ts) / 3600
        result = {
            "sun_altitude_deg": np.full(len(ts), -20),
            "moon_altitude_deg": moon_alt,
        }
        for body in bodies:
            result[body] = {
                "altitude_deg": np.full(len(ts), 40),
                "azimuth_deg": np.full(len(ts), 180),
                "sun_separation_deg": np.full(len(ts), 90),
                "moon_separation_deg": np.full(len(ts), 0 if body == "moon" else 10),
                "distance_au": np.full(len(ts), 1),
            }
        return result

    def lunar_phase(self, timestamp):
        return {"illumination_fraction": 0.5}


def test_moon_separation_only_constrains_other_targets_while_moon_is_up():
    result = plan_night(
        **args(targets="moon,saturn", min_moon_separation_deg=30),
        provider_factory=SyntheticProvider,
    )
    moon, saturn = result["targets"]
    assert moon["windows"][0]["start_utc"] == result["night"]["start_utc"]
    assert saturn["windows"][0]["start_utc"].startswith("2026-10-01T23:00:")
    assert saturn["windows"][0]["end_utc"] == result["night"]["end_utc"]


def test_sun_exclusion_applies_even_when_dark_and_target_is_above_threshold():
    class NearSun(SyntheticProvider):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["sun_separation_deg"] = np.full(len(timestamps), 20)
            return result

    result = plan_night(**args(targets="moon,saturn"), provider_factory=NearSun)
    assert all(
        t["status"] == "no_matching_window" and t["windows"] == []
        for t in result["targets"]
    )


@pytest.mark.parametrize("date,dark", [("2026-06-21", False), ("2026-12-21", True)])
def test_polar_summer_and_winter_have_explicit_darkness_states(date, dark):
    result = plan_night(date, "Arctic/Longyearbyen", 78.22, 15.65, targets="moon")
    assert bool(result["darkness"]["intervals"]) is dark


@pytest.mark.parametrize("centre", [0, 300, 600])
def test_sample_aligned_tangencies_are_explicitly_unresolved(centre):
    _, uncertain = threshold_events(lambda t: -((t - centre) ** 2), [0, 300, 600])
    assert uncertain


def test_real_mcp_dispatcher_rejects_boolean_coordinates_before_provider(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    spec = importlib.util.spec_from_file_location(
        "strict_observing_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    planner = Mock(return_value={"schema_version": 1})
    monkeypatch.setattr(server, "plan_night", planner)
    for field in (
        "lat",
        "lon",
        "min_altitude_deg",
        "sun_altitude_deg",
        "min_moon_separation_deg",
    ):
        values = args()
        values[field] = True
        with pytest.raises(ToolError):
            asyncio.run(server.mcp.call_tool("plan_observing_night", values))
    with pytest.raises(ToolError):
        asyncio.run(
            server.mcp.call_tool(
                "plan_observing_night", {**args(), "max_cloud_percent": 0}
            )
        )
    planner.assert_not_called()
    schema = next(
        t
        for t in asyncio.run(server.mcp.list_tools())
        if t.name == "plan_observing_night"
    )
    assert schema.inputSchema["additionalProperties"] is False
    asyncio.run(
        server.mcp.call_tool(
            "plan_observing_night",
            dict(date="2026-10-01", timezone="UTC", lat=0, lon=0),
        )
    )
    planner.assert_called_once()


def test_crossing_times_fall_inside_independent_jpl_minute_brackets():
    data = json.loads(
        (Path(__file__).parent / "fixtures/observing-horizons.json").read_text()
    )
    result = plan_night(**args(targets="moon"))
    window = result["targets"][0]["windows"][0]
    for case, key in zip(data["crossings"], ("start_utc", "end_utc")):
        threshold = case["threshold_deg"]
        brackets = [
            (a, b)
            for a, b in zip(case["samples"], case["samples"][1:])
            if (a["altitude_deg"] - threshold) * (b["altitude_deg"] - threshold) < 0
        ]
        assert len(brackets) == 1
        a, b = brackets[0]
        assert a["time_utc"] <= window[key] <= b["time_utc"]


@pytest.mark.parametrize("failure", ["missing", "stale"])
def test_missing_or_stale_iers_data_returns_unavailable(monkeypatch, failure):
    from astropy.utils import iers

    if failure == "missing":

        def unavailable(*args, **kwargs):
            raise OSError("Synthetic missing data")

        monkeypatch.setattr(iers.IERS_Auto, "open", unavailable)
    else:

        def stale(*args, **kwargs):
            raise ValueError("Synthetic stale prediction")

        monkeypatch.setattr(iers.IERS_Auto, "ut1_utc", stale)
    with pytest.raises(PlanningError) as error:
        plan_night(**args(targets="moon"))
    assert error.value.status == 503


def test_mcp_planning_does_not_block_other_tools_or_queue_unbounded_work(monkeypatch):
    import threading

    spec = importlib.util.spec_from_file_location(
        "concurrent_observing_mcp", ROOT / "mcp-server/server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    counter = []
    lock = threading.Lock()

    def slow_plan(*args, **kwargs):
        with lock:
            index = len(counter)
            counter.append(index)
        started[index].set()
        assert release.wait(3), "test must release the worker"
        return {"schema_version": 1}

    monkeypatch.setattr(server, "plan_night", slow_plan)
    db = Mock()
    db.search.return_value = []
    monkeypatch.setattr(server, "db", lambda: db)

    async def scenario():
        tasks = [
            asyncio.create_task(server.mcp.call_tool("plan_observing_night", args()))
            for _ in range(2)
        ]
        try:
            for event in started:
                assert await asyncio.to_thread(event.wait, 1)
            assert not any(task.done() for task in tasks)
            await asyncio.wait_for(
                server.mcp.call_tool("search", {"query": "Moon"}), 0.5
            )
            overload = await asyncio.wait_for(
                server.plan_observing_night(**args()), 0.5
            )
            assert overload["status"] == 503 and "busy" in overload["error"]
            assert len(counter) == 2
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert (await server.plan_observing_night(**args()))["status"] == 503
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
    assert db.search.call_count == 1
    # asyncio.run joins outstanding executor threads before returning: both
    # permits must now be reusable even though the first caller cancelled.
    assert server._planning_slots.acquire(blocking=False)
    assert server._planning_slots.acquire(blocking=False)
    server._planning_slots.release()
    server._planning_slots.release()


def test_provider_checks_staleness_before_status_and_leaves_warning_filters_unchanged(
    monkeypatch,
):
    import warnings
    from astropy.utils import iers

    original = iers.IERS_Auto.ut1_utc
    calls = []

    def record(self, *args, **kwargs):
        calls.append(kwargs.get("return_status", False))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(iers.IERS_Auto, "ut1_utc", record)
    before = list(warnings.filters)
    provider = BuiltinEphemeris(NightInput.parse("2026-10-01", "UTC", 0, 0))
    assert calls[:2] == [False, True]
    provider.positions(
        [datetime.fromisoformat("2026-10-01T22:00:00+00:00").timestamp()], ("moon",)
    )
    assert warnings.filters == before
