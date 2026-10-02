"""Private bounded worker; no network, global ephemeris mutation stays here."""

from dataclasses import replace
import json
import sys

from .catalogue import _snapshot
from .discovery import MAX_CATALOGUE_RECORDS, _discover, parse_discovery
from .ephemeris import BuiltinEphemeris
from .inputs import BODIES
from .worker import MAX_OUTPUT_BYTES


def main():
    raw = sys.stdin.buffer.read(16385)
    if (
        len(raw) > 16384
        or len(sys.argv) != 3
        or sys.argv[1] not in ("builtin", "jpl-de440s")
    ):
        raise ValueError("Invalid discovery worker request")
    request, options = parse_discovery(json.loads(raw))
    rows, sources = _snapshot()
    if not rows or len(rows) > MAX_CATALOGUE_RECORDS:
        raise ValueError("Packaged candidate scope exceeds discovery bound")
    records = {
        key: value
        for key, value in rows.items()
        if value["astrometry"]["status"] == "verified"
    }
    # Internal catalogue-wide screen only. Public selected night plans still
    # validate their eight-target maximum before any provider is constructed.
    all_request = replace(request, targets=(*BODIES, *sorted(records)))
    if sys.argv[1] == "jpl-de440s":
        from astropy.coordinates import solar_system_ephemeris
        from .kernels import JplEphemeris

        provider = JplEphemeris(all_request, sys.argv[2])
        try:
            with solar_system_ephemeris.set(provider.ephemeris):
                result = _discover(request, options, provider, records, sources)
        finally:
            solar_system_ephemeris.get_kernel("builtin")
    else:
        provider = BuiltinEphemeris(all_request)
        result = _discover(request, options, provider, records, sources)
    encoded = json.dumps(result, allow_nan=False)
    if len(encoded.encode()) > MAX_OUTPUT_BYTES:
        raise ValueError("Discovery response exceeded limit")
    sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
