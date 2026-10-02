"""Opt-in bounded candidate discovery, not a visibility or all-sky survey."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
from importlib.resources import files
import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

from .catalogue import _snapshot, appearance_metadata
from .horizon import altitude_at, mask_json
from .inputs import BODIES, NightInput, PlanningError, number
from .kernels import VerifiedKernel
from .planner import PLANNING_CAPACITY, ROOT_TOLERANCE_SECONDS, SAMPLE_SECONDS, _plan
from .worker import MAX_OUTPUT_BYTES, WORKER_TIMEOUT_SECONDS, configured_provider

COARSE_SECONDS = 1200
MAX_CATALOGUE_RECORDS = 158
MODES = ("naked_eye", "binocular", "telescope")
PREFERENCES = ("balanced", "wide_field", "stars", "deep_sky", "solar_system")
NIGHT_FIELDS = {
    "date",
    "timezone",
    "lat",
    "lon",
    "min_altitude_deg",
    "sun_altitude_deg",
    "min_moon_separation_deg",
    "window_start_utc",
    "window_end_utc",
    "horizon_mask",
}
OPTION_FIELDS = {
    "equipment_mode",
    "preference",
    "true_field_deg",
    "max_catalogue_v_magnitude",
    "shortlist_limit",
}
DISCOVERY_FIELDS = NIGHT_FIELDS | OPTION_FIELDS


@dataclass(frozen=True)
class DiscoveryOptions:
    equipment_mode: str
    preference: str = "balanced"
    true_field_deg: float | None = None
    max_catalogue_v_magnitude: float | None = None
    shortlist_limit: int = 6

    @classmethod
    def parse(
        cls,
        equipment_mode,
        preference="balanced",
        true_field_deg=None,
        max_catalogue_v_magnitude=None,
        shortlist_limit=6,
    ):
        if not isinstance(equipment_mode, str) or equipment_mode not in MODES:
            raise PlanningError(
                "Choose naked_eye, binocular or telescope equipment_mode."
            )
        if not isinstance(preference, str) or preference not in PREFERENCES:
            raise PlanningError("Choose a supported discovery preference.")
        if type(shortlist_limit) is not int or not 1 <= shortlist_limit <= 8:
            raise PlanningError("shortlist_limit must be an integer from 1 to 8.")
        if true_field_deg is not None:
            true_field_deg = number(true_field_deg, "true_field_deg", 0.01, 180)
        if max_catalogue_v_magnitude is not None:
            max_catalogue_v_magnitude = number(
                max_catalogue_v_magnitude, "max_catalogue_v_magnitude", -30, 30
            )
        return cls(
            equipment_mode,
            preference,
            true_field_deg,
            max_catalogue_v_magnitude,
            shortlist_limit,
        )


def parse_discovery(payload):
    if (
        not isinstance(payload, dict)
        or set(payload) - DISCOVERY_FIELDS
        or not {"date", "timezone", "lat", "lon", "equipment_mode"} <= set(payload)
    ):
        raise PlanningError(
            "Use supported discovery fields including date, timezone, lat, lon and equipment_mode."
        )
    options = DiscoveryOptions.parse(
        **{k: v for k, v in payload.items() if k in OPTION_FIELDS}
    )
    request = NightInput.parse(
        **{k: v for k, v in payload.items() if k in NIGHT_FIELDS}, targets="moon"
    )
    return request, options


def normalized_request(request):
    value = {k: v for k, v in asdict(request).items() if k in NIGHT_FIELDS}
    value["horizon_mask"] = mask_json(request.horizon_mask)
    return value


def discover_targets(**payload):
    request, options = parse_discovery(payload)
    if not PLANNING_CAPACITY.acquire(blocking=False):
        raise PlanningError("Observing planner is busy; try again later.", 503)
    kernel = None
    try:
        provider = configured_provider()
        if provider == "jpl-de440s":
            kernel = VerifiedKernel(os.environ.get("OBSERVING_JPL_KERNEL", ""))
        # Both providers are isolated: one bounded child owns the complete
        # catalogue screen and selected refinement, not a queue of per-row jobs.
        normalized = normalized_request(request)
        normalized.update(asdict(options))
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "solar_db.observing.discovery_worker",
                    provider,
                    kernel.path if kernel else "",
                ],
                input=json.dumps(normalized, allow_nan=False).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=WORKER_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode or output.tell() > MAX_OUTPUT_BYTES:
                raise ValueError("Discovery worker failed")
            output.seek(0)
            response = json.loads(output.read(MAX_OUTPUT_BYTES + 1))
        if not isinstance(response, dict) or response.get("schema_version") != 1:
            raise ValueError("Invalid discovery response")
        return response
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, PlanningError):
            raise
        raise PlanningError(
            "Isolated target discovery is unavailable; no fallback was used.", 503
        ) from exc
    finally:
        try:
            if kernel is not None:
                kernel.close()
        finally:
            PLANNING_CAPACITY.release()


def source_identity():
    names = ("observing/discovery.py", "observing/discovery_worker.py")
    manifest = []
    for name in names:
        with files("solar_db").joinpath(name).open("rb") as source:
            data = source.read(262145)
        if not 0 < len(data) <= 262144:
            raise ValueError("Invalid discovery source")
        manifest.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
    digest = hashlib.sha256(
        (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    return {
        "algorithm": "observing-discovery-sources-sha256-v1",
        "source_sha256": digest,
        "files": list(names),
    }


def field_context(row, field):
    extent = (
        row.get("major_axis_arcmin") if row and "deep_sky" in row["families"] else None
    )
    if row is None or extent is None or extent <= 0:
        return "unknown_angular_extent"
    if field is None:
        return "field_not_supplied"
    return (
        "catalogue_extent_within_field"
        if extent / 60 <= field
        else "catalogue_extent_exceeds_field"
    )


def candidate_priority(target, row, context, options):
    family = (
        "solar_system"
        if row is None
        else "deep_sky"
        if "deep_sky" in row["families"]
        else "stars"
    )
    explicit = 0 if options.preference in ("balanced", "wide_field", family) else 1
    fit = 0 if context == "catalogue_extent_within_field" else 1
    if options.preference == "wide_field":
        explicit = fit
    if options.equipment_mode == "naked_eye":
        mode = (
            0
            if target == "moon"
            else 1
            if family == "stars"
            else 2
            if family == "solar_system"
            else 3
        )
        reason = "naked_eye_moon_then_bright_star_preference"
    elif options.equipment_mode == "binocular":
        mode = (
            0
            if context == "catalogue_extent_within_field"
            else 1
            if family == "deep_sky"
            else 2
            if target == "moon"
            else 3
            if family == "stars"
            else 4
        )
        reason = "binocular_known_field_extent_then_deep_sky_preference"
    else:
        mode = (
            0
            if family == "solar_system"
            else 1
            if row and "double_star" in row["families"]
            else 2
            if family == "deep_sky"
            else 3
        )
        reason = "telescope_solar_system_then_double_star_preference"
    return (explicit, mode, fit if options.preference == "wide_field" else 0), reason


def _discover(request, options, provider, records, sources):
    start = datetime.fromisoformat(
        request.window_start_utc.replace("Z", "+00:00")
    ).timestamp()
    end = datetime.fromisoformat(
        request.window_end_utc.replace("Z", "+00:00")
    ).timestamp()
    # Include a midpoint even for a sub-step selected interval. These are tests
    # of sampled instants, not an assertion of uninterrupted matching duration.
    times = np.linspace(
        start, end, max(3, math.ceil((end - start) / COARSE_SECONDS) + 1)
    ).tolist()
    targets = (*BODIES, *sorted(records))
    data = provider.positions(times, targets)
    shortlisted, brightness_excluded, unknown_brightness_excluded = [], 0, 0
    for target in targets:
        row = records.get(target)
        magnitude = row["magnitude"] if row else None
        band = row["magnitude_band"] if row else None
        v_band = isinstance(band, str) and (band == "V" or band.startswith("V ("))
        if options.max_catalogue_v_magnitude is not None:
            if magnitude is None or not v_band:
                unknown_brightness_excluded += 1
                continue
            if magnitude > options.max_catalogue_v_magnitude:
                brightness_excluded += 1
                continue
        count = 0
        peak = None
        for index in range(len(times)):
            point = {key: value[index] for key, value in data[target].items()}
            horizon = altitude_at(request.horizon_mask, point["azimuth_deg"])
            required = max(
                request.min_altitude_deg,
                horizon if horizon is not None else request.min_altitude_deg,
            )
            if (
                data["sun_altitude_deg"][index] <= request.sun_altitude_deg
                and point["altitude_deg"] >= required
                and point["sun_separation_deg"] >= 30
                and (
                    target == "moon"
                    or data["moon_altitude_deg"][index] <= 0
                    or point["moon_separation_deg"] >= request.min_moon_separation_deg
                )
            ):
                count += 1
                peak = max(
                    peak if peak is not None else -90, float(point["altitude_deg"])
                )
        if not count:
            continue
        context = field_context(row, options.true_field_deg)
        priority, reason = candidate_priority(target, row, context, options)
        candidate = {
            "id": target,
            "name": row["name"] if row else target.title(),
            "aliases": row["aliases"] if row else [],
            "preference_reasons": [
                "requested_preference_" + options.preference,
                reason,
                "more_matching_sample_instants_then_stable_id",
            ],
            "field_context": context,
            "coarse_matching_samples": count,
            "sampled_peak_altitude_deg": peak,
            "appearance": appearance_metadata(row) if row else None,
            "brightness_status": "catalogue_value"
            if magnitude is not None
            else "unknown",
        }
        shortlisted.append(((*priority, -count, target), candidate))
    shortlisted.sort(key=lambda item: item[0])
    selected = [item[1] for item in shortlisted[: options.shortlist_limit]]
    plan = (
        _plan(replace(request, targets=tuple(c["id"] for c in selected)), provider)
        if selected
        else None
    )
    useful = (
        {target["id"]: target for target in plan["targets"] if target["windows"]}
        if plan
        else {}
    )
    candidates = [
        {**candidate, "refined_status": useful[candidate["id"]]["status"]}
        for candidate in selected
        if candidate["id"] in useful
    ]
    from .identity import calculation_identity

    return {
        "schema_version": 1,
        "request": normalized_request(request),
        "discovery": {
            "options": asdict(options),
            "scope": "reviewed packaged starter sample plus Moon and seven planets; not all sky",
            "catalogue_records": len(records),
            "solar_system_targets": len(BODIES),
            "unsupported_catalogue_records": len(_snapshot()[0]) - len(records),
            "coarse_step_seconds": COARSE_SECONDS,
            "sample_count": len(times),
            "incomplete_between_samples": True,
            "coarse_matching_candidates": len(shortlisted),
            "refined_candidates": len(selected),
            "selected_without_refined_window": len(selected) - len(candidates),
            "brightness_excluded": brightness_excluded,
            "unknown_or_non_v_brightness_excluded": unknown_brightness_excluded,
            "ranking_policy": "explicit family/field preference, equipment-family preference, matching sampled instants descending, stable ID; no visibility score",
            "limitations": [
                "Twenty-minute sampled screening can miss short or grazing windows; no candidates does not establish that the night has no targets.",
                "Equipment modes are editorial family preferences, not detection, resolving-power or suitability guarantees. No aperture, sky brightness, weather or observer model is inferred.",
                "Catalogue magnitude and band are original source context. Integrated extended-object magnitude is not point-source visibility or surface brightness. Solar-system brightness and apparent size are not supplied.",
                "A known catalogue major-axis extent within a supplied true field describes angular containment only. Double-star separation is never used as an object diameter.",
                "Only returned candidate IDs with nonempty refined windows form the shortlist; uncertain grazing status remains explicit. No backfill beyond the bounded first selection.",
            ],
            "source_snapshots": [
                {
                    key: value[key]
                    for key in (
                        "source",
                        "snapshot_sha256",
                        "upstream_sha256",
                        "source_url",
                        "retrieved_at",
                        "license",
                        "attribution",
                        "license_url",
                    )
                }
                for _, value in sorted(sources.items())
            ],
            "calculation": source_identity(),
            "planner_calculation": calculation_identity(),
        },
        "candidates": candidates,
        "plan": plan,
        "method": plan["method"]
        if plan
        else {
            **provider.metadata,
            "calculation": calculation_identity(),
            "sample_minutes": SAMPLE_SECONDS / 60,
            "root_tolerance_seconds": ROOT_TOLERANCE_SECONDS,
            "window_note": "Selected-target refinement is configured for five-minute chart samples and one-second numerical root tolerance. No refinement was performed because the incomplete coarse screen found no candidates; see discovery.coarse_step_seconds for the actual screening cadence.",
        },
    }
