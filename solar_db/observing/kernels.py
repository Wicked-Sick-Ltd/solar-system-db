"""Explicit, offline provider selection; unmodified JPL DE440s only.

Kernel use: https://naif.jpl.nasa.gov/naif/rules.html
No arbitrary kernel URL, automatic download, global ephemeris switch or fallback.
"""

import hashlib
import os
from pathlib import Path
import stat
import tempfile
from importlib.metadata import version

from astropy.time import Time

from .ephemeris import BuiltinEphemeris
from .inputs import PlanningError

KERNEL_NAME = "de440s.bsp"
KERNEL_SIZE = 32726016
KERNEL_SHA256 = "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"
KERNEL_URL = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp"


class VerifiedKernel:
    """Parent-owned snapshot: finally removes it even after killing a worker."""

    def __init__(self, path):
        self._snapshot = None
        try:
            # Copy then hash the exact private file used by Astropy, so an atomic
            # replacement of the operator's source cannot change this response.
            # Reject devices/FIFOs before opening; cap the copy even if it grows.
            source = Path(path)
            if not source.is_absolute() or not stat.S_ISREG(source.stat().st_mode):
                raise ValueError("Not an absolute regular file")
            if source.stat().st_size != KERNEL_SIZE:
                raise ValueError("Unexpected size")
            self._snapshot = tempfile.NamedTemporaryFile(suffix=".bsp")
            digest = hashlib.sha256()
            with os.fdopen(
                os.open(source, os.O_RDONLY | os.O_NONBLOCK), "rb"
            ) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Not a regular file")
                remaining = KERNEL_SIZE + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    digest.update(chunk)
                    self._snapshot.write(chunk)
            if remaining != 1 or digest.hexdigest() != KERNEL_SHA256:
                raise ValueError("Checksum mismatch")
            self._snapshot.flush()
            self.path = self._snapshot.name
        except (OSError, ValueError) as exc:
            self.close()
            raise PlanningError(
                "Configured local JPL kernel is unavailable or invalid; no fallback was used.",
                503,
            ) from exc

    def close(self):
        if self._snapshot is not None:
            self._snapshot.close()
            self._snapshot = None


class JplEphemeris(BuiltinEphemeris):
    """Only construct inside an isolated process with its ephemeris context set."""

    def __init__(self, request, path):
        self.ephemeris = path
        try:
            from jplephem.spk import SPK

            with SPK.open(self.ephemeris) as kernel:
                first = max(segment.start_jd for segment in kernel.segments)
                last = min(segment.end_jd for segment in kernel.segments)
            # Cover retarded light-time calculations too; one day is conservative
            # for the supported planets. This is distinct from IERS coverage.
            times = Time([request.start, request.end]).tdb.jd
            if min(times) - 1 < first or max(times) > last:
                raise ValueError("Outside kernel coverage")
            super().__init__(request)
            self.metadata.update(
                {
                    "provider": "jpl-de440s",
                    "ephemeris": "JPL DE440s",
                    "jplephem_version": version("jplephem"),
                    "kernel": {
                        "name": KERNEL_NAME,
                        "sha256": KERNEL_SHA256,
                        "size_bytes": KERNEL_SIZE,
                        "source_url": KERNEL_URL,
                        "start_tdb": Time(first, format="jd", scale="tdb").isot,
                        "end_tdb": Time(last, format="jd", scale="tdb").isot,
                    },
                    "refraction": "none; Moon/Mercury/Venus centres, Mars through Neptune system barycentres",
                    "accuracy_note": "Checksum-pinned JPL DE440s with Astropy apparent coordinate transforms in an isolated worker. Mars through Neptune use system barycentres, not independent planet centres. Numerical crossing tolerance is not physical accuracy. No surveyed terrain or atmospheric model, and no visibility guarantee. A supplied horizon mask is user-entered.",
                }
            )
        except PlanningError:
            raise
        except (OSError, ValueError, ImportError, KeyError) as exc:
            raise PlanningError(
                "Configured local JPL kernel is unavailable, invalid or outside coverage; no fallback was used.",
                503,
            ) from exc
