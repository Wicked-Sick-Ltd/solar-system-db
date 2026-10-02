"""Bounded, public build provenance; no local paths, environment or credentials."""

import hashlib
import subprocess

from solar_db.catalogue_identity import canonical


def fingerprint(path, root):
    return {"name": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def build_provenance(root, args):
    inputs = [root / "seed/factsheets.json"]
    inputs.extend(sorted(path for path in (root / "solar_db/data/starter").rglob("*") if path.is_file()))
    if args.offline:
        names = ["jpl_sats_elem.html", "jpl_sats_phys_par.html", "sbdb_asteroids.json", "sbdb_comets.json"]
        for skipped, more in ((args.skip_mpc, ["mpc_numbered.txt"]), (args.skip_cad, ["cad.json"]),
                              (args.skip_showers, ["mdc_showers.txt"]), (args.skip_exoplanets, ["exoplanets.json", "exoplanets.meta.json"])):
            if not skipped:
                names.extend(more)
        inputs.extend(root / "tests/fixtures" / name for name in names)
    inputs = [fingerprint(path, root) for path in sorted(inputs)]
    code = [root / "schema/schema.sql"]
    for directory in ("scripts", "solar_db"):
        code.extend(sorted((root / directory).rglob("*.py")))
    code = [fingerprint(path, root) for path in sorted(code)]
    try:
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
            revision = None
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return inputs, {"revision": revision, "source_tree_sha256": hashlib.sha256(canonical(code)).hexdigest(),
                    "source_files": code,
                    "options": {key: getattr(args, key) for key in ("offline", "skip_mpc", "skip_cad", "skip_showers", "skip_exoplanets", "cad_years")},
                    "enrichment_store_requested": bool(args.enrichment_store)}
