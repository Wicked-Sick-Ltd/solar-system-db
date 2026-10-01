"""Offline Astropy builtin estimates with explicit Earth-orientation coverage.

https://docs.astropy.org/en/stable/coordinates/solarsystem.html
https://docs.astropy.org/en/stable/utils/iers.html
Provider selection and kernel verification live in kernels.py. No runtime downloads.
"""

from importlib.metadata import version
import hashlib
import json

import erfa

import astropy
from astropy import units as u
from astropy.coordinates import AltAz, EarthLocation, get_body
from astropy.time import Time
from astropy.utils import iers
import numpy as np

from .inputs import PlanningError
from .catalogue import CatalogueDirection, resolve_targets

# Process policy: requests must never download Earth orientation/leap seconds.
# Do not temporarily restore True while another request may be transforming.
iers.conf.auto_download = False


def iers_identity(table):
    """Canonical effective table columns used for UT1, polar motion and status."""
    columns = ("MJD", "UT1_UTC", "PM_x", "PM_y", "UT1Flag", "PolPMFlag")
    digest = hashlib.sha256()
    for name in columns:
        col = table[name]
        digest.update(
            json.dumps(
                [name, str(getattr(col, "unit", ""))], separators=(",", ":")
            ).encode()
        )
        if name.endswith("Flag"):
            digest.update(
                json.dumps(
                    [str(value) for value in col], separators=(",", ":")
                ).encode()
            )
        else:
            values = np.array(col, dtype="<f8")
            values[np.isnan(values)] = np.nan
            digest.update(values.tobytes())
            digest.update(np.ma.getmaskarray(col).astype("u1").tobytes())
    return {
        "sha256": digest.hexdigest(),
        "algorithm": "effective-iers-columns-le-f64-v1",
        "columns": list(columns),
    }


def iso(value):
    return value.utc.isot.split(".")[0] + "Z"


class BuiltinEphemeris:
    ephemeris = "builtin"

    def __init__(self, request):
        self.request = request
        self.catalogue = {
            key: CatalogueDirection(*value)
            for key, value in resolve_targets(request.targets).items()
        }
        self.location = EarthLocation.from_geodetic(
            request.lon * u.deg, request.lat * u.deg, 0 * u.m
        )
        try:
            self.table = iers.IERS_Auto.open()
        except (OSError, ValueError) as exc:
            raise PlanningError(
                "Bundled Earth-orientation data is unavailable.", 503
            ) from exc
        times = Time([request.start, request.end])
        first, last = self.table["MJD"][0].value, self.table["MJD"][-1].value
        if times.mjd.min() < first or times.mjd.max() > last:
            raise PlanningError(
                "Earth-orientation data does not cover this night; no extrapolated result was produced.",
                503,
            )
        try:
            # return_status alone skips Astropy's stale-prediction check.
            # Validate first; no per-request warnings filter is mutated, which
            # keeps concurrent MCP/REST calculations from racing global state.
            self.table.ut1_utc(times)
            self.table.pm_xy(times)
            _, status = self.table.ut1_utc(times, return_status=True)
            _, _, pole_status = self.table.pm_xy(times, return_status=True)
            status = np.concatenate([status, pole_status])
        except (ValueError, iers.IERSRangeError) as exc:
            raise PlanningError(
                "Bundled Earth-orientation predictions are too old for this night.", 503
            ) from exc
        if np.any(status < 0):
            raise PlanningError(
                "Earth-orientation data is unavailable for this night.", 503
            )
        self.metadata = {
            "provider": "astropy-builtin",
            "ephemeris": "ERFA builtin",
            "astropy_version": astropy.__version__,
            "erfa_version": erfa.__version__,
            "frame": "apparent topocentric AltAz; azimuth north through east",
            "refraction": "none; geometric body centre",
            "accuracy_note": "Approximate offline model, not a precision JPL ephemeris. Numerical crossing tolerance is not physical accuracy. No surveyed terrain or atmospheric model, and no visibility guarantee. A supplied horizon mask is user-entered.",
            "iers": {
                "start_utc": iso(Time(first, format="mjd")),
                "end_utc": iso(Time(last, format="mjd")),
                "data_version": version("astropy-iers-data"),
                "snapshot": iers_identity(self.table),
                "status": "predicted"
                if np.any(status == iers.FROM_IERS_A_PREDICTION)
                else "measured",
            },
        }

    def positions(self, timestamps, bodies):
        times = Time(timestamps, format="unix", scale="utc")
        try:
            frame = AltAz(obstime=times, location=self.location, pressure=0 * u.hPa)
            sun = get_body("sun", times, self.location, ephemeris=self.ephemeris)
            moon = get_body("moon", times, self.location, ephemeris=self.ephemeris)
            sun_alt = sun.transform_to(frame).alt.deg
            moon_alt = moon.transform_to(frame).alt.deg
            result = {"sun_altitude_deg": sun_alt, "moon_altitude_deg": moon_alt}
            for body in bodies:
                catalogue = self.catalogue.get(body)
                coord = (
                    catalogue.direction(times).transform_to(
                        moon.frame.replicate_without_data()
                    )
                    if catalogue is not None
                    else moon
                    if body == "moon"
                    else get_body(body, times, self.location, ephemeris=self.ephemeris)
                )
                horizontal = coord.transform_to(frame)

                # Same GCRS observer/frame: compute vector angles explicitly,
                # avoiding Astropy's different-frame separation warning.
                def separation(other):
                    left, right = (
                        coord.cartesian.xyz.value,
                        other.cartesian.xyz.value,
                    )
                    cos = np.sum(left * right, axis=0) / (
                        np.linalg.norm(left, axis=0) * np.linalg.norm(right, axis=0)
                    )
                    return np.degrees(np.arccos(np.clip(cos, -1, 1)))

                result[body] = {
                    "altitude_deg": horizontal.alt.deg,
                    "azimuth_deg": horizontal.az.deg,
                    "sun_separation_deg": separation(sun),
                    "moon_separation_deg": np.zeros(len(times))
                    if body == "moon"
                    else separation(moon),
                    "distance_au": [None] * len(times)
                    if catalogue is not None
                    else coord.distance.au,
                }
            return result
        except (ValueError, iers.IERSWarning, iers.IERSRangeError) as exc:
            raise PlanningError(
                "Offline ephemeris data could not support this night.", 503
            ) from exc

    def lunar_phase(self, timestamp):
        t = Time(timestamp, format="unix", scale="utc")
        sun = get_body("sun", t, ephemeris=self.ephemeris)
        moon = get_body("moon", t, ephemeris=self.ephemeris)
        a, b = sun.cartesian.xyz.value, moon.cartesian.xyz.value
        elongation = np.arccos(
            np.clip(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1)
        )
        phase = np.arctan2(
            sun.distance.au * np.sin(elongation),
            moon.distance.au - sun.distance.au * np.cos(elongation),
        )
        return {
            "reference_utc": iso(t),
            "definition": "geocentric geometric illuminated fraction; no surface/libration model",
            "illumination_fraction": float((1 + np.cos(phase)) / 2),
            "phase_angle_deg": float(np.degrees(phase)),
            "elongation_deg": float(np.degrees(elongation)),
        }
