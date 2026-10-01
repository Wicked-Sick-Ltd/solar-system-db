"""Bounded child-process isolation for Astropy's process-global ephemeris state."""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict

from .inputs import PlanningError
from .kernels import VerifiedKernel

WORKER_TIMEOUT_SECONDS = 35
MAX_OUTPUT_BYTES = 1_500_000


def configured_provider():
    name = os.environ.get("OBSERVING_EPHEMERIS", "builtin")
    if name not in ("builtin", "jpl-de440s"):
        raise PlanningError("Configured observing ephemeris is unavailable.", 503)
    return name


def run_jpl_worker(request):
    kernel = VerifiedKernel(os.environ.get("OBSERVING_JPL_KERNEL", ""))
    try:
        payload = asdict(request)
        payload.pop("start")
        payload.pop("end")
        payload["targets"] = ",".join(request.targets)
        # Coordinates travel through stdin, not command-line/process listings.
        # stdout is our bounded contract; stderr is suppressed, never relayed.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [sys.executable, "-m", "solar_db.observing.worker", kernel.path],
                input=json.dumps(payload).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=WORKER_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode or output.tell() > MAX_OUTPUT_BYTES:
                raise ValueError("Worker failed")
            output.seek(0)
            response = json.loads(output.read(MAX_OUTPUT_BYTES + 1))
        if not isinstance(response, dict) or response.get("schema_version") != 1:
            raise ValueError("Invalid worker result")
        return response
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise PlanningError(
            "Isolated JPL planner is unavailable; no fallback was used.", 503
        ) from exc
    finally:
        kernel.close()


def main():
    # Private implementation entry point. The public parent owns the snapshot
    # and concurrency slot, including cleanup after a timeout or worker crash.
    from astropy.coordinates import solar_system_ephemeris
    from .inputs import NightInput
    from .kernels import JplEphemeris
    from .planner import _plan

    raw = sys.stdin.buffer.read(2049)
    if len(raw) > 2048 or len(sys.argv) != 2:
        raise ValueError("Invalid worker input")
    request = NightInput.parse(**json.loads(raw))
    provider = JplEphemeris(request, sys.argv[1])
    try:
        with solar_system_ephemeris.set(provider.ephemeris):
            result = _plan(request, provider)
    finally:
        # ScienceState context restores the name lazily; explicitly close the
        # cached SPK descriptor before the parent unlinks its private snapshot.
        solar_system_ephemeris.get_kernel("builtin")
    serialized = json.dumps(result, allow_nan=False)
    if len(serialized.encode()) > MAX_OUTPUT_BYTES:
        raise ValueError("Worker result exceeded limit")
    sys.stdout.write(serialized)


if __name__ == "__main__":
    main()
