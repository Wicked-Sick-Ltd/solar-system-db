"""Bounded package-source identity; never a database snapshot or code signature."""

from copy import deepcopy
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import platform

import numpy as np

from .inputs import PlanningError

ALGORITHM = "observing-source-files-sha256-v1"
CATALOGUE_SCOPE = "packaged-target-snapshots; SQLite catalogue not consulted"
SOURCE_FILES = (
    "observing/__init__.py",
    "observing/catalogue.py",
    "observing/ephemeris.py",
    "observing/horizon.py",
    "observing/identity.py",
    "observing/inputs.py",
    "observing/kernels.py",
    "observing/planner.py",
    "observing/worker.py",
    "starter_astrometry.py",
    "starter_catalogues.py",
)


def hash_sources(read):
    manifest = []
    for name in SOURCE_FILES:
        content = read(name)
        if not isinstance(content, bytes) or not 0 < len(content) <= 262144:
            raise ValueError("Unavailable algorithm source")
        manifest.append({"file": name, "sha256": hashlib.sha256(content).hexdigest()})
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded + b"\n").hexdigest()


@lru_cache(maxsize=1)
def _identity():
    try:
        package = files("solar_db")

        def read(name):
            with package.joinpath(name).open("rb") as stream:
                return stream.read(262145)

        digest = hash_sources(read)
    except (OSError, ValueError, TypeError) as exc:
        raise PlanningError(
            "Observing calculation identity is unavailable.", 503
        ) from exc
    return {
        "algorithm": ALGORITHM,
        "source_sha256": digest,
        "files": list(SOURCE_FILES),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "catalogue_scope": CATALOGUE_SCOPE,
    }


def calculation_identity():
    # Never return the mutable cached object to a caller. Source trees must be
    # deployed immutably and workers restarted; this is not execution attestation.
    return deepcopy(_identity())
