"""Retained model checks distinguish repetition from astronomical accuracy."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from solar_db.observing import plan_night
from solar_db.observing import identity
from solar_db.observing.inputs import PlanningError
from solar_db.observing.replay import TOLERANCES, compare_retained, retained_result

FIXTURE = Path(__file__).parent / "fixtures/observing-replay.json"


def fixture():
    return json.loads(FIXTURE.read_text())


def test_source_hash_is_canonical_bounded_path_independent_and_changes_with_code():
    content = {name: name.encode() for name in identity.SOURCE_FILES}
    entries = [
        {"file": name, "sha256": hashlib.sha256(content[name]).hexdigest()}
        for name in identity.SOURCE_FILES
    ]
    expected = hashlib.sha256(
        (json.dumps(entries, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    assert identity.hash_sources(content.__getitem__) == expected
    content[identity.SOURCE_FILES[0]] += b"changed"
    assert identity.hash_sources(content.__getitem__) != expected
    for invalid in (b"", b"x" * 262145, "not bytes"):
        with pytest.raises(ValueError):
            identity.hash_sources(lambda _: invalid)


def test_source_identity_is_public_defensive_and_fails_closed_without_files(
    monkeypatch,
):
    first = identity.calculation_identity()
    first["files"].append("changed")
    assert "changed" not in identity.calculation_identity()["files"]
    assert (
        identity.calculation_identity()["catalogue_scope"] == identity.CATALOGUE_SCOPE
    )
    assert all(not path.startswith("/") and ".." not in path for path in first["files"])
    identity._identity.cache_clear()
    monkeypatch.setattr(
        identity, "files", lambda _: (_ for _ in ()).throw(OSError("/secret/path"))
    )
    try:
        with pytest.raises(PlanningError) as error:
            identity.calculation_identity()
        assert error.value.status == 503
        assert "secret" not in str(error.value)
    finally:
        identity._identity.cache_clear()


def test_retained_capture_matches_itself_and_preserves_null_distances_and_source_context():
    data = fixture()
    assert data["tolerances"] == TOLERANCES
    for case in data["cases"]:
        reference = case["reference"]
        assert (
            reference["identity"]["method"]["calculation"]["source_sha256"]
            == identity.calculation_identity()["source_sha256"]
        )
        assert compare_retained(reference, deepcopy(reference)) == {
            "status": "matched",
            "mismatches": [],
        }
        for target in reference["results"]["targets"][1:]:
            assert all(sample["distance_au"] is None for sample in target["samples"])
        assert (
            reference["identity"]["catalogues"]["openngc:NGC0224"]["appearance"][
                "major_axis_arcmin"
            ]
            > 0
        )
    assert [c["reference"]["night"]["duration_hours"] for c in data["cases"]] == [
        24,
        25,
    ]


@pytest.mark.parametrize("change", ["method", "source", "input", "timezone", "missing"])
def test_different_identity_input_or_timezone_mapping_cannot_claim_repeatability(
    change,
):
    expected = fixture()["cases"][0]["reference"]
    actual = deepcopy(expected)
    if change == "method":
        actual["identity"]["method"]["calculation"]["source_sha256"] = "a" * 64
    elif change == "source":
        actual["identity"]["catalogues"]["bsc5p:hr2491"]["snapshot_sha256"] = "a" * 64
    elif change == "input":
        actual["request"]["lat"] = 52
    elif change == "timezone":
        actual["night"]["start_utc"] = "2026-10-01T12:00:00Z"
    else:
        actual["identity"]["method"].pop("calculation")
    assert compare_retained(expected, actual)["status"] in ("not_comparable", "invalid")


def test_numerical_tolerances_are_scoped_not_a_blanket_accuracy_claim():
    expected = fixture()["cases"][0]["reference"]
    actual = deepcopy(expected)
    actual["results"]["targets"][0]["samples"][0]["altitude_deg"] += 5e-9
    assert compare_retained(expected, actual)["status"] == "matched"
    actual["results"]["targets"][0]["samples"][0]["altitude_deg"] += 1e-5
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["results"]["targets"][1]["samples"][0]["distance_au"] = 0
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["results"]["targets"][0]["samples"][0]["altitude_deg"] = False
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["results"]["moon"]["illumination_fraction"] = float("nan")
    assert compare_retained(expected, actual)["status"] == "different"


@pytest.mark.parametrize(
    "value", [{}, None, {"request": {}, "identity": {}, "night": {}, "results": {}}]
)
def test_incomplete_captures_never_match(value):
    assert compare_retained(value, value)["status"] == "invalid"


@pytest.mark.parametrize("case", fixture()["cases"], ids=lambda case: case["name"])
def test_actual_offline_plans_replay_retained_environment_or_explicitly_decline_comparison(
    monkeypatch, case
):
    if case["provider"] == "jpl-de440s":
        if not os.environ.get("JPL_TEST_KERNEL"):
            pytest.skip(
                "JPL_TEST_KERNEL is required for the actual pinned-kernel replay"
            )
        monkeypatch.setenv("OBSERVING_JPL_KERNEL", os.environ["JPL_TEST_KERNEL"])
    monkeypatch.setenv("OBSERVING_EPHEMERIS", case["provider"])
    from solar_db import SolarDB

    monkeypatch.setattr(
        SolarDB,
        "catalogue_identity",
        lambda self: pytest.fail("Planner must not attach a database identity"),
    )
    plan = plan_night(**case["reference"]["request"])
    actual = retained_result(plan)
    verdict = compare_retained(case["reference"], actual)
    if (
        verdict["status"] == "not_comparable"
        and verdict["reason"] == "identity_mismatch"
    ):
        # Moving development dependencies are allowed; they cannot be reported as
        # a replay of a retained environment. CLI returns nonzero in this case.
        pytest.skip(
            "Retained environment identity differs; replay not verified in this environment"
        )
    assert verdict == {"status": "matched", "mismatches": []}
    assert "catalogue_id" not in plan


def test_replay_cli_rejects_unbounded_or_malformed_files_without_provider_use(tmp_path):
    script = Path(__file__).resolve().parents[2] / "scripts/verify_observing_replay.py"
    for content in (
        "[]",
        '{"schema_version": true}',
        "x" * 524289,
        "[" * 2000 + "]" * 2000,
    ):
        path = tmp_path / "bad.json"
        path.write_text(content)
        result = subprocess.run(
            [sys.executable, str(script), str(path)], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert "missing, malformed" in result.stdout


def test_event_tolerance_does_not_allow_changed_sample_instants_or_window_counts():
    expected = fixture()["cases"][0]["reference"]
    actual = deepcopy(expected)
    actual["results"]["darkness"]["intervals"][0]["start_utc"] = "2026-10-01T18:50:13Z"
    assert compare_retained(expected, actual)["status"] == "matched"
    actual["results"]["darkness"]["intervals"][0]["start_utc"] = "2026-10-01T18:50:15Z"
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["results"]["moon"]["samples"][0]["time_utc"] = "2026-10-01T11:00:01Z"
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["results"]["darkness"]["intervals"].clear()
    assert compare_retained(expected, actual)["status"] == "different"


def test_malformed_huge_integers_and_naive_event_times_fail_closed():
    expected = fixture()["cases"][0]["reference"]
    actual = deepcopy(expected)
    actual["results"]["targets"][0]["samples"][0]["altitude_deg"] = 10**400
    assert compare_retained(expected, actual)["status"] == "different"
    actual = deepcopy(expected)
    actual["request"]["lat"] = 10**400
    assert compare_retained(expected, actual)["status"] == "not_comparable"
    for invalid in (
        "2026-10-01T18:50:12",
        "2026-10-01T19:50:12+01:00",
        "2026-02-30T18:50:12Z",
    ):
        actual = deepcopy(expected)
        actual["results"]["darkness"]["intervals"][0]["start_utc"] = invalid
        assert compare_retained(expected, actual)["status"] == "different"


def test_capture_nesting_and_node_counts_are_bounded_before_recursive_comparison():
    expected = fixture()["cases"][0]["reference"]
    actual = deepcopy(expected)
    nested = []
    for _ in range(40):
        nested = [nested]
    actual["results"]["nested"] = nested
    assert compare_retained(expected, actual) == {
        "status": "invalid",
        "reason": "capture_bounds",
    }
    actual["results"]["nested"] = [None] * 20001
    assert compare_retained(expected, actual)["status"] == "invalid"
