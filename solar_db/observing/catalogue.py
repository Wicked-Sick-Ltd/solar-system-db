"""Pinned, source-frame catalogue directions; no invented distance or velocity.

Astropy's documented angular-only space-motion path is used without distance or
radial velocity: https://docs.astropy.org/en/stable/coordinates/apply_space_motion.html
The internal ERFA safe-distance convention is not an observed stellar distance.
Only the returned unit-sphere direction survives into apparent transforms.
"""

from copy import deepcopy
from functools import lru_cache
import math
from xml.etree.ElementTree import ParseError

from astropy import units as u
from astropy.coordinates import FK5, SkyCoord
from astropy.time import Time

from ..starter_catalogues import load_starter_catalogues
from .inputs import BODIES, PlanningError


@lru_cache(maxsize=1)
def _snapshot():
    records, sources = load_starter_catalogues()
    return {row["id"]: row for row in records}, sources


def resolve_targets(selected):
    ids = [target for target in selected if target not in BODIES]
    if not ids:
        return {}
    try:
        records, sources = _snapshot()
        result = {}
        for target in ids:
            if target not in records:
                raise PlanningError("Unknown pinned catalogue target identifier.")
            row = records[target]
            astrometry = row["astrometry"]
            if astrometry["status"] != "verified":
                raise PlanningError(
                    "This catalogue target has no verified source frame for planning."
                )
            expected = {
                "bsc5p": ("FK5", "J2000.0", "linear_angular_proper_motion"),
                "openngc": ("ICRS", None, "static_catalogue_direction"),
            }[row["source"]]
            if (
                (astrometry["frame"], astrometry["equinox"], astrometry["motion_model"])
                != expected
                or astrometry["reference_epoch_jyear"] != 2000.0
                or astrometry["coordinates_propagated"] is not False
            ):
                raise ValueError("Unexpected source interpretation")
            for field in ("pm_ra_cosdec_arcsec_per_year", "pm_dec_arcsec_per_year"):
                value = astrometry[field]
                if row["source"] == "bsc5p":
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError("Missing angular proper motion")
                elif value is not None:
                    raise ValueError("Unexpected deep-sky proper motion")
            result[target] = (deepcopy(row), deepcopy(sources[row["source"]]))
        return result
    except PlanningError:
        raise
    except (OSError, ValueError, KeyError, TypeError, ParseError) as exc:
        raise PlanningError(
            "Verified packaged catalogue data is unavailable.", 503
        ) from exc


class CatalogueDirection:
    def __init__(self, row, source):
        self.row = row
        astrometry = row["astrometry"]
        moving = astrometry["motion_model"] == "linear_angular_proper_motion"
        kwargs = {}
        if moving:
            kwargs = {
                "frame": FK5(equinox=Time("J2000")),
                "obstime": Time(2000.0, format="jyear", scale="tt"),
                "pm_ra_cosdec": astrometry["pm_ra_cosdec_arcsec_per_year"]
                * u.arcsec
                / u.yr,
                "pm_dec": astrometry["pm_dec_arcsec_per_year"] * u.arcsec / u.yr,
            }
        else:
            kwargs["frame"] = "icrs"
        self.coordinate = SkyCoord(
            ra=row["ra_deg"] * u.deg, dec=row["dec_deg"] * u.deg, **kwargs
        )
        self.moving = moving
        self.metadata = {
            **{
                key: deepcopy(source[key])
                for key in (
                    "source",
                    "snapshot_sha256",
                    "upstream_sha256",
                    "source_url",
                    "retrieved_at",
                    "license",
                    "attribution",
                    "license_url",
                    "astrometry_evidence",
                )
            },
            "input_coordinates": {
                "ra_deg": row["ra_deg"],
                "dec_deg": row["dec_deg"],
                **{
                    key: astrometry[key]
                    for key in (
                        "frame",
                        "equinox",
                        "reference_epoch_jyear",
                        "observation_epoch_jyear",
                    )
                },
            },
            "motion_model": astrometry["motion_model"],
            "proper_motion_applied": moving,
            "pm_ra_cosdec_arcsec_per_year": astrometry["pm_ra_cosdec_arcsec_per_year"],
            "pm_dec_arcsec_per_year": astrometry["pm_dec_arcsec_per_year"],
            "distance_au": None,
            "refraction": "none; zero atmospheric pressure; angular catalogue direction",
            "accuracy_note": (
                "Angular-only estimate: no measured distance, annual parallax, radial velocity, perspective acceleration or binary orbit. "
                + (
                    "Linear angular proper motion from Julian reference epoch 2000.0; missing radial velocity is treated as zero by the propagation model. "
                    if moving
                    else "Static ICRS catalogue direction; no proper motion applied. "
                )
                + "1900–2100 is a calculation bound, not an accuracy guarantee; bundled Earth-orientation coverage further restricts dates. Source rounding and catalogue systematics remain; no precision, component position, extended-object visibility or telescope-pointing guarantee."
            ),
        }

    def direction(self, times):
        coord = (
            self.coordinate.apply_space_motion(new_obstime=times)
            if self.moving
            else self.coordinate
        )
        # apply_space_motion may carry a model-induced radial differential even
        # with no input distance. Never expose/use that as a physical velocity.
        return SkyCoord(coord.frame.realize_frame(coord.data.without_differentials()))
