"""Selected-hours/horizon geometry and real interface validation, offline."""

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from solar_db.observing import plan_night
from solar_db.observing.horizon import altitude_at, validate_mask
from solar_db.observing.inputs import NightInput, PlanningError

BASE = {
    "date": "2026-10-01",
    "timezone": "UTC",
    "lat": 0,
    "lon": 0,
    "targets": "moon",
    "min_altitude_deg": 0,
}
WINDOW = {
    "window_start_utc": "2026-10-01T12:00:00Z",
    "window_end_utc": "2026-10-01T12:10:00Z",
}


def mask(*pairs):
    return [{"azimuth_deg": a, "min_altitude_deg": b} for a, b in pairs]


class Synthetic:
    def __init__(self, request):
        self.start = request.start.timestamp()
        self.metadata = {"provider": "synthetic"}

    def positions(self, timestamps, bodies):
        ts = np.array(timestamps) - self.start
        result = {
            "sun_altitude_deg": np.full(len(ts), -20.0),
            "moon_altitude_deg": np.full(len(ts), -10.0),
        }
        for body in bodies:
            result[body] = {
                "altitude_deg": np.full(len(ts), 20.0),
                "azimuth_deg": ts * 0.1 % 360,
                "sun_separation_deg": np.full(len(ts), 90.0),
                "moon_separation_deg": np.full(len(ts), 0.0),
                "distance_au": np.full(len(ts), 1.0),
            }
        return result

    def lunar_phase(self, timestamp):
        return {"illumination_fraction": 0.5}


def test_selected_hours_clip_windows_and_darkness_but_preserve_full_night_samples():
    result = plan_night(**BASE, **WINDOW, provider_factory=Synthetic)
    expected = [
        {"start_utc": WINDOW["window_start_utc"], "end_utc": WINDOW["window_end_utc"]}
    ]
    assert (
        result["targets"][0]["windows"] == result["darkness"]["intervals"] == expected
    )
    assert len(result["targets"][0]["samples"]) == 289
    assert result["targets"][0]["samples"][-1]["time_utc"] == result["night"]["end_utc"]
    assert result["constraints"]["horizon_mask"] is None
    assert result["targets"][0]["samples"][0]["horizon_altitude_deg"] is None


def test_sub_grid_narrow_obstacle_splits_windows_at_refined_corners():
    result = plan_night(
        **BASE,
        **WINDOW,
        horizon_mask=mask((0, 0), (7, 0), (7.5, 40), (8, 0), (180, 0)),
        provider_factory=Synthetic,
    )
    target = result["targets"][0]
    assert target["status"] == "windows_found"
    assert len(target["windows"]) == 2
    assert target["windows"][0]["end_utc"] in (
        "2026-10-01T12:01:12Z",
        "2026-10-01T12:01:13Z",
    )
    assert target["windows"][1]["start_utc"] in (
        "2026-10-01T12:01:17Z",
        "2026-10-01T12:01:18Z",
    )


def test_horizon_seam_and_unknown_zero_are_distinct():
    value = validate_mask(mask((350, 10), (10, 30), (180, 0)))
    assert altitude_at(value, 0) == altitude_at(value, 360) == 20
    assert altitude_at(value, 355) == 15
    assert altitude_at(value, 5) == 25
    assert altitude_at(None, 90) is None
    assert altitude_at(validate_mask(mask((0, 0), (180, 0))), 90) == 0


def test_obstruction_cannot_lower_independent_baseline():
    result = plan_night(
        **{**BASE, "min_altitude_deg": 30},
        **WINDOW,
        horizon_mask=mask((0, -5), (180, -5)),
        provider_factory=Synthetic,
    )
    assert result["targets"][0]["windows"] == []
    assert result["targets"][0]["status"] == "no_matching_window"
    assert result["targets"][0]["samples"][0]["required_min_altitude_deg"] == 30


def test_zenith_ambiguity_is_unresolved_and_band_is_withheld():
    class Zenith(Synthetic):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["altitude_deg"][:] = 89.9
            return result

    result = plan_night(
        **BASE, **WINDOW, horizon_mask=mask((0, 0), (180, 0)), provider_factory=Zenith
    )
    assert result["targets"][0]["status"] == "unresolved_grazing"
    assert result["targets"][0]["windows"] == []


def test_ninety_degree_saved_baseline_accepts_zenith_tangent_honestly():
    class Zenith(Synthetic):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["altitude_deg"] = (
                    90 - ((np.array(timestamps) - self.start - 300) / 100) ** 2
                )
            return result

    result = plan_night(
        **{**BASE, "min_altitude_deg": 90}, **WINDOW, provider_factory=Zenith
    )
    assert result["targets"][0]["status"] == "unresolved_grazing"
    assert result["targets"][0]["windows"] == []


@pytest.mark.parametrize(
    "values",
    [
        {"window_start_utc": WINDOW["window_start_utc"]},
        {**WINDOW, "window_end_utc": WINDOW["window_start_utc"]},
        {**WINDOW, "window_start_utc": "2026-10-01T11:59:59Z"},
        {**WINDOW, "window_end_utc": "2026-10-03T12:00:00Z"},
        {**WINDOW, "window_start_utc": "2026-10-01T13:00:00+01:00"},
        {**WINDOW, "window_start_utc": "2026-02-30T12:00:00Z"},
        {**WINDOW, "window_start_utc": True},
        {"horizon_mask": []},
        {"horizon_mask": mask((0, 0))},
        {"horizon_mask": mask((0, 0), (360, 1))},
        {"horizon_mask": mask((0, 91), (180, 0))},
        {"horizon_mask": mask((True, 0), (180, 0))},
        {"horizon_mask": mask((0, "0"), (180, 0))},
        {"horizon_mask": mask((0, float("nan")), (180, 0))},
    ],
)
def test_invalid_constraints_fail_before_any_provider(values):
    factory = Mock()
    with pytest.raises(PlanningError):
        plan_night(**BASE, **values, provider_factory=factory)
    factory.assert_not_called()


def test_one_second_window_and_dst_local_night_boundaries():
    req = NightInput.parse(
        **BASE,
        window_start_utc="2026-10-01T12:00:00Z",
        window_end_utc="2026-10-01T12:00:01Z",
    )
    assert req.window_end_utc.endswith("01Z")
    for date, hours in (("2026-03-28", 23), ("2026-10-24", 25)):
        req = NightInput.parse(date, "Europe/London", 0, 0)
        assert (req.end - req.start).total_seconds() == hours * 3600


def test_post_rejects_duplicates_unknowns_oversize_and_malformed_without_work(
    monkeypatch,
):
    import api.main as api

    monkeypatch.setattr(api.limiter, "enabled", False)
    factory = Mock(return_value={"schema_version": 1})
    monkeypatch.setattr(api, "plan_night", factory)
    with TestClient(api.app) as client:
        for raw in (
            '{"date":"2026-10-01","date":"2026-10-02"}',
            "[]",
            "{bad",
            "null",
            '{"lat":NaN}',
            "[" * 1100,
        ):
            result = client.post(
                "/api/v1/observing/night",
                content=raw,
                headers={"Content-Type": "application/json"},
            )
            assert result.status_code == 422
            assert result.headers["Cache-Control"] == "no-store"
        assert (
            client.post(
                "/api/v1/observing/night", json={**BASE, "secret": 1}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/observing/night",
                content=b" " * 16385,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert (
            client.post("/api/v1/observing/night?lat=0", json=BASE).status_code == 422
        )
        factory.assert_not_called()
        result = client.post(
            "/api/v1/observing/night",
            json={**BASE, **WINDOW, "horizon_mask": mask((0, 0), (180, 0))},
        )
        assert result.status_code == 200
        assert factory.call_args.kwargs["horizon_mask"][0]["min_altitude_deg"] == 0


def test_actual_mcp_dispatcher_rejects_boolean_mask_before_provider(monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    path = Path(__file__).resolve().parents[2] / "mcp-server/server.py"
    spec = importlib.util.spec_from_file_location("constraint_mcp", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    factory = Mock()
    monkeypatch.setattr(server, "plan_night", factory)
    with pytest.raises(ToolError):
        asyncio.run(
            server.mcp.call_tool(
                "plan_observing_night",
                {**BASE, "horizon_mask": mask((0, False), (180, 0))},
            )
        )
    factory.assert_not_called()


def test_narrow_clear_gap_is_found_between_obstructed_chart_samples():
    result = plan_night(
        **BASE,
        **WINDOW,
        horizon_mask=mask((0, 40), (7, 40), (7.5, 0), (8, 40), (180, 40)),
        provider_factory=Synthetic,
    )
    windows = result["targets"][0]["windows"]
    assert len(windows) == 1
    assert windows[0]["start_utc"] in ("2026-10-01T12:01:12Z", "2026-10-01T12:01:13Z")
    assert windows[0]["end_utc"] in ("2026-10-01T12:01:17Z", "2026-10-01T12:01:18Z")


def test_azimuth_turning_between_samples_finds_both_obstruction_crossings():
    class Turning(Synthetic):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["azimuth_deg"] = (
                    9 - ((np.array(timestamps) - self.start - 75) / 50) ** 2
                ) % 360
            return result

    result = plan_night(
        **BASE,
        **{**WINDOW, "window_end_utc": "2026-10-01T12:05:00Z"},
        horizon_mask=mask((0, 0), (7, 0), (8, 40), (10, 40), (11, 0), (180, 0)),
        provider_factory=Turning,
    )
    windows = result["targets"][0]["windows"]
    assert len(windows) == 2
    assert windows[0]["end_utc"] in ("2026-10-01T12:00:13Z", "2026-10-01T12:00:14Z")
    assert windows[1]["start_utc"] in (
        "2026-10-01T12:02:15Z",
        "2026-10-01T12:02:16Z",
        "2026-10-01T12:02:17Z",
    )


def test_north_seam_obstacle_creates_one_continuous_blocked_period():
    class North(Synthetic):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["azimuth_deg"] = (
                    350 + (np.array(timestamps) - self.start) * 0.1
                ) % 360
            return result

    result = plan_night(
        **BASE,
        **WINDOW,
        horizon_mask=mask((0, 40), (1, 0), (180, 0), (359, 0)),
        provider_factory=North,
    )
    windows = result["targets"][0]["windows"]
    assert len(windows) == 2
    assert windows[0]["end_utc"] in ("2026-10-01T12:01:34Z", "2026-10-01T12:01:35Z")
    assert windows[1]["start_utc"] in ("2026-10-01T12:01:44Z", "2026-10-01T12:01:45Z")


def test_selected_window_status_does_not_inherit_tangency_outside_selection():
    class Tangent(Synthetic):
        def positions(self, timestamps, bodies):
            result = super().positions(timestamps, bodies)
            for body in bodies:
                result[body]["altitude_deg"] = -(
                    ((np.array(timestamps) - self.start - 600) / 100) ** 2
                )
            return result

    result = plan_night(
        **BASE,
        **{**WINDOW, "window_end_utc": "2026-10-01T12:05:00Z"},
        provider_factory=Tangent,
    )
    assert result["targets"][0]["status"] == "no_matching_window"


def test_huge_json_numbers_fail_as_input_errors_instead_of_overflow(monkeypatch):
    import api.main as api

    monkeypatch.setattr(api.limiter, "enabled", False)
    with TestClient(api.app) as client:
        giant = (
            '{"date":"2026-10-01","timezone":"UTC","lat":' + "1" * 4500 + ',"lon":0}'
        )
        assert (
            client.post(
                "/api/v1/observing/night",
                content=giant,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/observing/night", json={**BASE, "lat": 10**1000}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/observing/night",
                json={**BASE, "horizon_mask": mask((0, 10**1000), (180, 0))},
            ).status_code
            == 422
        )
