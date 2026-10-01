"""Offline unit checks plus explicit opt-in acceptance with the real pinned file.

JPL_TEST_KERNEL=/absolute/de440s.bsp pytest api/tests/test_jpl_provider.py
The tests never fetch a kernel; provision the documented checksum first.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

from astropy.coordinates import solar_system_ephemeris
import pytest

from solar_db.observing import plan_night
from solar_db.observing.ephemeris import BuiltinEphemeris, iers_identity
from solar_db.observing.inputs import NightInput, PlanningError
from solar_db.observing.kernels import (
    JplEphemeris,
    VerifiedKernel,
    KERNEL_SIZE,
    KERNEL_SHA256,
)
from solar_db.observing import planner, worker

REFERENCES = json.loads(
    (Path(__file__).parent / "fixtures/observing-horizons.json").read_text()
)["cases"]


@pytest.fixture
def kernel_path():
    path = os.environ.get("JPL_TEST_KERNEL")
    if not path:
        pytest.skip(
            "Set JPL_TEST_KERNEL for actual pinned-kernel acceptance (no test downloads)"
        )
    snapshot = VerifiedKernel(path)
    try:
        yield snapshot.path
    finally:
        snapshot.close()


def request():
    return NightInput.parse("2026-10-01", "UTC", 51.5, -0.12, targets="moon")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "de440s.bsp",
        "https://naif.jpl.nasa.gov/de440s.bsp",
        "/dev/zero",
        "/no/such/kernel",
    ],
)
def test_invalid_kernel_paths_fail_without_download(path, monkeypatch):
    download = Mock(side_effect=AssertionError("No network"))
    monkeypatch.setattr("astropy.coordinates.solar_system.download_file", download)
    with pytest.raises(PlanningError) as error:
        VerifiedKernel(path)
    assert error.value.status == 503
    download.assert_not_called()


def test_wrong_hash_same_size_fails_and_deletes_private_copy(tmp_path, monkeypatch):
    source = tmp_path / "wrong.bsp"
    with source.open("wb") as stream:
        stream.truncate(KERNEL_SIZE)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with pytest.raises(PlanningError, match="invalid"):
        VerifiedKernel(str(source))
    assert list(tmp_path.iterdir()) == [source]


def test_unknown_provider_never_falls_back(monkeypatch):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "jpl")
    with pytest.raises(PlanningError, match="Configured"):
        plan_night("2026-10-01", "UTC", 0, 0)


def test_common_capacity_rejects_before_provider_and_releases_on_failure(monkeypatch):
    capacity = planner.PLANNING_CAPACITY
    assert capacity.acquire(False) and capacity.acquire(False)
    factory = Mock()
    try:
        with pytest.raises(PlanningError, match="busy"):
            plan_night("2026-10-01", "UTC", 0, 0, provider_factory=factory)
    finally:
        capacity.release()
        capacity.release()
    factory.assert_not_called()
    factory.side_effect = PlanningError("fixture failure", 503)
    with pytest.raises(PlanningError, match="fixture failure"):
        plan_night("2026-10-01", "UTC", 0, 0, provider_factory=factory)
    assert capacity.acquire(False) and capacity.acquire(False)
    capacity.release()
    capacity.release()


@pytest.mark.parametrize("failure", ["timeout", "crash", "malformed", "oversize"])
def test_failed_child_is_sanitized_and_snapshot_removed(
    failure, kernel_path, monkeypatch
):
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel_path)
    paths = []

    def run(command, **kwargs):
        paths.append(command[-1])
        assert Path(paths[-1]).exists()
        assert "51.5" not in " ".join(command)
        assert json.loads(kwargs["input"])["lat"] == 51.5
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 35, stderr="PRIVATE")
        kwargs["stdout"].write(
            b"x" * (worker.MAX_OUTPUT_BYTES + 1)
            if failure == "oversize"
            else b"PRIVATE invalid json"
        )
        return subprocess.CompletedProcess(command, 1 if failure == "crash" else 0)

    monkeypatch.setattr(worker.subprocess, "run", run)
    with pytest.raises(PlanningError) as error:
        worker.run_jpl_worker(request())
    assert "PRIVATE" not in str(error.value)
    assert error.value.status == 503
    assert not Path(paths[0]).exists()


@pytest.mark.parametrize("case", REFERENCES, ids=lambda c: c["case"])
def test_actual_pinned_kernel_against_independent_horizons(
    case, kernel_path, monkeypatch
):
    monkeypatch.setattr(
        "astropy.coordinates.solar_system.download_file",
        Mock(side_effect=AssertionError("No network")),
    )
    req = NightInput.parse(
        case["time_utc"][:10], "UTC", case["lat"] or 0, case["lon"] or 0
    )
    provider = JplEphemeris(req, kernel_path)
    t = datetime.fromisoformat(case["time_utc"].replace("Z", "+00:00")).timestamp()
    ref = case["reference"]
    with solar_system_ephemeris.set(kernel_path):
        if case["lat"] is None:
            result = provider.lunar_phase(t)
            assert (
                abs(result["illumination_fraction"] - ref["illumination_fraction"])
                < 0.00002
            )
            assert abs(result["elongation_deg"] - ref["elongation_deg"]) < 0.002
        elif case["body"] == "sun":
            assert (
                abs(
                    provider.positions([t], ("moon",))["sun_altitude_deg"][0]
                    - ref["altitude_deg"]
                )
                < 0.002
            )
        else:
            result = provider.positions([t], (case["body"],))[case["body"]]
            assert abs(result["altitude_deg"][0] - ref["altitude_deg"]) < 0.002
            assert (
                abs((result["azimuth_deg"][0] - ref["azimuth_deg"] + 180) % 360 - 180)
                < 0.002
            )
            assert result["distance_au"][0] == pytest.approx(
                ref["distance_au"], rel=0.00002
            )
    assert solar_system_ephemeris.get() == "builtin"
    assert provider.metadata["kernel"]["sha256"] == KERNEL_SHA256


def test_real_isolated_plan_preserves_parent_ephemeris_and_has_identity(
    kernel_path, monkeypatch
):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "jpl-de440s")
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel_path)
    result = plan_night("2026-10-01", "UTC", 51.5, -0.12, targets="moon")
    assert result["method"]["provider"] == "jpl-de440s"
    assert result["method"]["kernel"]["sha256"] == KERNEL_SHA256
    assert "barycentres" in result["method"]["refraction"]
    assert len(result["method"]["iers"]["snapshot"]["sha256"]) == 64
    assert solar_system_ephemeris.get() == "builtin"
    assert len(result["targets"][0]["samples"]) == 289
    data = json.loads(
        (Path(__file__).parent / "fixtures/observing-horizons.json").read_text()
    )
    window = result["targets"][0]["windows"][0]
    for case, key in zip(data["crossings"], ("start_utc", "end_utc")):
        threshold = case["threshold_deg"]
        brackets = [
            (a, b)
            for a, b in zip(case["samples"], case["samples"][1:])
            if (a["altitude_deg"] - threshold) * (b["altitude_deg"] - threshold) < 0
        ]
        assert len(brackets) == 1
        assert brackets[0][0]["time_utc"] <= window[key] <= brackets[0][1]["time_utc"]


def test_iers_identity_is_stable_sensitive_to_used_columns_only():
    provider = BuiltinEphemeris(request())
    table = provider.table.copy()
    original = iers_identity(table)
    assert original == iers_identity(table.copy())
    table["PM_x"][0] += 0.001 * table["PM_x"].unit
    assert original != iers_identity(table)


def test_worker_timeout_kills_process_and_cleans_snapshot(kernel_path, monkeypatch):
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel_path)
    monkeypatch.setattr(worker, "WORKER_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(PlanningError, match="unavailable"):
        worker.run_jpl_worker(request())


def test_kernel_snapshot_survives_source_replacement(kernel_path, tmp_path):
    import shutil

    source = tmp_path / "source.bsp"
    shutil.copyfile(kernel_path, source)
    snapshot = VerifiedKernel(str(source))
    try:
        source.write_bytes(b"replaced")
        assert Path(snapshot.path).stat().st_size == KERNEL_SIZE
        assert source.read_bytes() == b"replaced"
    finally:
        snapshot.close()
    assert not Path(snapshot.path).exists()


def test_jpl_date_outside_iers_coverage_does_not_return_builtin(
    kernel_path, monkeypatch
):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "jpl-de440s")
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel_path)
    with pytest.raises(PlanningError) as error:
        plan_night("2100-01-01", "UTC", 0, 0, targets="moon")
    assert error.value.status == 503


def test_real_worker_preserves_selected_window_and_canonical_horizon(
    kernel_path, monkeypatch
):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "jpl-de440s")
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel_path)
    result = plan_night(
        "2026-10-01",
        "UTC",
        51.5,
        -0.12,
        targets="moon",
        window_start_utc="2026-10-01T23:00:00Z",
        window_end_utc="2026-10-02T04:00:00Z",
        horizon_mask=[
            {"azimuth_deg": 180, "min_altitude_deg": 0},
            {"azimuth_deg": 360, "min_altitude_deg": 0},
        ],
    )
    constraints = result["constraints"]
    assert constraints["window_start_utc"] == "2026-10-01T23:00:00Z"
    assert constraints["horizon_mask"] == [
        {"azimuth_deg": 0.0, "min_altitude_deg": 0.0},
        {"azimuth_deg": 180.0, "min_altitude_deg": 0.0},
    ]
    assert result["targets"][0]["windows"]
    assert all(
        constraints["window_start_utc"]
        <= row["start_utc"]
        < row["end_utc"]
        <= constraints["window_end_utc"]
        for row in result["targets"][0]["windows"]
    )
    assert result["targets"][0]["samples"][0]["horizon_altitude_deg"] == 0
    assert len(result["targets"][0]["samples"]) == 289
