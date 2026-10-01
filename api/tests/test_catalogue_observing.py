"""Offline source-frame, angular-motion and mixed-provider contract regressions."""

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from unittest.mock import Mock

from astropy import units as u
from astropy.coordinates import AltAz, get_body, solar_system_ephemeris
from astropy.time import Time
import numpy as np
import pytest

from solar_db.observing import catalogue, plan_night
from solar_db.observing.catalogue import CatalogueDirection, resolve_targets
from solar_db.observing.ephemeris import BuiltinEphemeris
from solar_db.observing.inputs import NightInput, PlanningError

BASE = dict(date="2026-10-01", timezone="UTC", lat=51.5, lon=-0.12)
STAR = "bsc5p:hr2491"
DEEP = "openngc:NGC0224"


def test_exact_pinned_selection_accepts_eight_mixed_ids_beyond_old_string_limit():
    ids = [
        "openngc:NGC0224",
        "openngc:NGC0221",
        "openngc:NGC0205",
        "openngc:NGC0581",
        "openngc:NGC0598",
        "openngc:NGC0628",
        "openngc:NGC0650",
        STAR,
    ]
    raw = ",".join(ids)
    assert len(raw) > 100
    assert NightInput.parse(**BASE, targets=raw).targets == tuple(ids)


@pytest.mark.parametrize(
    "target",
    [
        "bsc5p:hr1",
        "BSC5P:hr2491",
        "bsc5p:HR2491",
        "bsc5p:hr02491",
        "openngc:ngc0224",
        "openngc:Mel022",
        "openngc:../secret",
        STAR + "," + STAR,
        ",".join([STAR] * 9),
        "x" * 2049,
    ],
)
def test_unknown_unsupported_and_malformed_ids_never_reach_provider(target):
    provider = Mock()
    with pytest.raises(PlanningError) as error:
        plan_night(**BASE, targets=target, provider_factory=provider)
    assert error.value.status == 422
    provider.assert_not_called()


def test_source_failure_is_unavailable_and_does_not_break_dynamic_targets(monkeypatch):
    monkeypatch.setattr(
        catalogue, "_snapshot", Mock(side_effect=ValueError("private path"))
    )
    with pytest.raises(PlanningError) as error:
        NightInput.parse(**BASE, targets=STAR)
    assert error.value.status == 503
    assert "private" not in str(error.value)
    assert NightInput.parse(**BASE, targets="moon").targets == ("moon",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("pm_ra_cosdec_arcsec_per_year", None),
        ("pm_dec_arcsec_per_year", False),
        ("pm_dec_arcsec_per_year", math.nan),
        ("frame", "ICRS"),
        ("reference_epoch_jyear", None),
    ],
)
def test_incomplete_frame_or_motion_is_not_silently_zero(monkeypatch, field, value):
    records, sources = deepcopy(catalogue._snapshot())
    records[STAR]["astrometry"][field] = value
    monkeypatch.setattr(catalogue, "_snapshot", lambda: (records, sources))
    with pytest.raises(PlanningError) as error:
        resolve_targets([STAR])
    assert error.value.status == 503


def test_fk5_orientation_matches_published_erfa_reference_without_guessing_icrs():
    # Published ERFA v2.0.1 t_fk52h J2000 position check. No motion interval,
    # so its distance/RV values do not enter this orientation-only comparison.
    reference = json.loads(
        (Path(__file__).parent / "fixtures/catalogue-erfa-reference.json").read_text()
    )
    row, source = resolve_targets([STAR])[STAR]
    row.update(
        ra_deg=math.degrees(reference["fk52h"]["ra_rad"]),
        dec_deg=math.degrees(reference["fk52h"]["dec_rad"]),
    )
    row["astrometry"].update(pm_ra_cosdec_arcsec_per_year=0, pm_dec_arcsec_per_year=0)
    direction = (
        CatalogueDirection(row, source).direction(Time("J2000", scale="tt")).icrs
    )
    assert direction.ra.rad == pytest.approx(
        reference["fk52h"]["expected_icrs_ra_rad"], abs=1e-12, rel=0
    )
    assert direction.dec.rad == pytest.approx(
        reference["fk52h"]["expected_icrs_dec_rad"], abs=1e-12, rel=0
    )
    assert abs(direction.ra.rad - reference["fk52h"]["ra_rad"]) > 1e-8


@pytest.mark.parametrize("dec,pmra,pmdec", [(85, 1, -2), (-85, -1, 2), (0, 0, 0)])
@pytest.mark.parametrize("year", [1900, 2025, 2100])
def test_angular_motion_matches_independent_tangent_vector_without_extra_cos(
    dec, pmra, pmdec, year
):
    row, source = resolve_targets([STAR])[STAR]
    row.update(ra_deg=120, dec_deg=dec)
    row["astrometry"].update(
        pm_ra_cosdec_arcsec_per_year=pmra, pm_dec_arcsec_per_year=pmdec
    )
    model = CatalogueDirection(row, source)
    direction = model.direction(Time(year, format="jyear", scale="tt"))
    ra, de = np.deg2rad([120, dec])
    initial = np.array([np.cos(de) * np.cos(ra), np.cos(de) * np.sin(ra), np.sin(de)])
    east = np.array([-np.sin(ra), np.cos(ra), 0])
    north = np.array([-np.sin(de) * np.cos(ra), -np.sin(de) * np.sin(ra), np.cos(de)])
    expected = initial + (year - 2000) * np.deg2rad(1 / 3600) * (
        pmra * east + pmdec * north
    )
    expected /= np.linalg.norm(expected)
    assert np.allclose(direction.cartesian.xyz.value, expected, atol=2e-10, rtol=0)
    assert direction.distance.unit == u.one
    assert not direction.data.differentials
    assert model.metadata["distance_au"] is None


def test_static_icrs_direction_does_not_apply_unknown_motion_or_invent_distance():
    row, source = resolve_targets([DEEP])[DEEP]
    model = CatalogueDirection(row, source)
    direction = model.direction(Time([1900, 2100], format="jyear", scale="tt"))
    assert direction.ra.deg == pytest.approx(row["ra_deg"], abs=1e-12, rel=0)
    assert direction.dec.deg == pytest.approx(row["dec_deg"], abs=1e-12, rel=0)
    assert model.metadata["proper_motion_applied"] is False
    assert model.metadata["pm_ra_cosdec_arcsec_per_year"] is None
    assert direction.distance.unit == u.one


def test_catalogue_apparent_separations_use_same_observer_and_provider_frames():
    provider = BuiltinEphemeris(NightInput.parse(**BASE, targets=STAR + "," + DEEP))
    times = Time(["2026-10-01T22:00:00", "2026-10-02T00:00:00"])
    actual = provider.positions(times.unix, [STAR, DEEP])
    horizontal = AltAz(obstime=times, location=provider.location, pressure=0 * u.hPa)
    sun = get_body("sun", times, provider.location, ephemeris="builtin").transform_to(
        horizontal
    )
    moon = get_body("moon", times, provider.location, ephemeris="builtin").transform_to(
        horizontal
    )
    for target in [STAR, DEEP]:
        # Independent path: compare in common AltAz instead of dotting GCRS vectors.
        direction = provider.catalogue[target].direction(times).transform_to(horizontal)
        assert np.allclose(
            actual[target]["altitude_deg"], direction.alt.deg, atol=1e-7, rtol=0
        )
        assert np.allclose(
            actual[target]["sun_separation_deg"],
            direction.separation(sun).deg,
            atol=1e-7,
            rtol=0,
        )
        assert np.allclose(
            actual[target]["moon_separation_deg"],
            direction.separation(moon).deg,
            atol=1e-7,
            rtol=0,
        )
        assert actual[target]["distance_au"] == [None, None]


def assert_catalogue_contract(result):
    moon, star, deep = result["targets"]
    assert "catalogue" not in moon
    assert all(sample["distance_au"] > 0 for sample in moon["samples"])
    for target in [star, deep]:
        assert len(target["samples"]) == 289
        assert all(sample["distance_au"] is None for sample in target["samples"])
        assert all(
            math.isfinite(sample["altitude_deg"]) for sample in target["samples"]
        )
        assert (
            target["catalogue"]["input_coordinates"]["observation_epoch_jyear"] is None
        )
        assert "not an accuracy guarantee" in target["catalogue"]["accuracy_note"]
        source = target["catalogue"]["source"]
        root = Path(__file__).resolve().parents[2] / "solar_db/data/starter"
        assert (
            target["catalogue"]["snapshot_sha256"]
            == hashlib.sha256((root / f"{source}.json").read_bytes()).hexdigest()
        )
        assert len(target["catalogue"]["astrometry_evidence"]["response_sha256"]) == 64
        assert target["catalogue"]["license"]
        for window in target["windows"]:
            assert window["start_utc"] >= "2026-10-01T20:00:00Z"
            assert window["end_utc"] <= "2026-10-02T04:00:00Z"
    assert star["catalogue"]["proper_motion_applied"] is True
    assert deep["catalogue"]["proper_motion_applied"] is False
    json.dumps(result, allow_nan=False)


def mixed_plan():
    return plan_night(
        **BASE,
        targets="moon," + STAR + "," + DEEP,
        window_start_utc="2026-10-01T20:00:00Z",
        window_end_utc="2026-10-02T04:00:00Z",
        horizon_mask=[
            {"azimuth_deg": 0, "min_altitude_deg": 5},
            {"azimuth_deg": 180, "min_altitude_deg": 15},
        ],
        min_moon_separation_deg=30,
    )


def test_builtin_mixed_catalogue_plan_preserves_nulls_provenance_and_constraints(
    monkeypatch,
):
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "builtin")
    assert_catalogue_contract(mixed_plan())


def test_actual_jpl_mixed_plan_isolated_and_identifies_same_pinned_catalogue(
    monkeypatch,
):
    kernel = os.environ.get("JPL_TEST_KERNEL")
    if not kernel:
        pytest.skip("Set JPL_TEST_KERNEL for actual pinned-kernel acceptance")
    before = solar_system_ephemeris.get()
    monkeypatch.setenv("OBSERVING_EPHEMERIS", "jpl-de440s")
    monkeypatch.setenv("OBSERVING_JPL_KERNEL", kernel)
    result = mixed_plan()
    assert_catalogue_contract(result)
    assert result["method"]["provider"] == "jpl-de440s"
    assert solar_system_ephemeris.get() == before


def test_rest_get_post_and_actual_mcp_dispatcher_preserve_ids_and_reject_unsupported(
    monkeypatch,
):
    import asyncio
    import importlib.util
    from fastapi.testclient import TestClient
    import api.main as api

    def validate_only(*args, **kwargs):
        request = NightInput.parse(*args, **kwargs)
        return {"schema_version": 1, "ids": list(request.targets)}

    monkeypatch.setattr(api.limiter, "enabled", False)
    monkeypatch.setattr(api, "plan_night", validate_only)
    params = {**BASE, "targets": STAR + "," + DEEP}
    with TestClient(api.app) as client:
        for response in (
            client.get("/api/v1/observing/night", params=params),
            client.post("/api/v1/observing/night", json=params),
        ):
            assert response.status_code == 200
            assert response.json()["ids"] == [STAR, DEEP]
            assert response.headers["Cache-Control"] == "no-store"
        for response in (
            client.get(
                "/api/v1/observing/night", params={**BASE, "targets": "openngc:Mel022"}
            ),
            client.post(
                "/api/v1/observing/night", json={**BASE, "targets": "openngc:Mel022"}
            ),
        ):
            assert response.status_code == 422
            assert "verified source frame" in response.json()["detail"]
    path = Path(__file__).resolve().parents[2] / "mcp-server/server.py"
    spec = importlib.util.spec_from_file_location("catalogue_planning_mcp", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    monkeypatch.setattr(server, "plan_night", validate_only)
    result = asyncio.run(server.mcp.call_tool("plan_observing_night", params))
    assert json.loads(result[0].text)["ids"] == [STAR, DEEP]
    result = asyncio.run(
        server.mcp.call_tool(
            "plan_observing_night", {**BASE, "targets": "openngc:Mel022"}
        )
    )
    assert json.loads(result[0].text)["status"] == 422
