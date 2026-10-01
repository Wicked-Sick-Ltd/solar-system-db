"""Reproduce the reviewed subset using local upstream files; no network or writes."""

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from solar_db.starter_catalogues import NONSTELLAR_HR  # noqa: E402


def verify(directory: Path):
    snapshots = {
        s: json.loads((ROOT / f"solar_db/data/starter/{s}.json").read_text())
        for s in ("bsc5p", "openngc")
    }
    for snapshot in snapshots.values():
        for filename, digest in snapshot["upstream_sha256"].items():
            if (
                hashlib.sha256((directory / filename).read_bytes()).hexdigest()
                != digest
            ):
                raise ValueError(f"Upstream hash mismatch: {filename}")
    text = (directory / "bsc5p.tdat").read_text()
    columns = text.split("line[1] = ")[1].splitlines()[0].split()
    records = [
        dict(zip(columns, line.split("|")))
        for line in text.split("<DATA>\n")[1].split("<END>")[0].splitlines()
        if "|" in line
    ]
    bright = sorted(
        [
            r
            for r in records
            if r["vmag"] and float(r["vmag"]) <= 2 and int(r["hr"]) not in NONSTELLAR_HR
        ],
        key=lambda r: int(r["hr"]),
    )
    records = []
    for filename in ("NGC.csv", "addendum.csv"):
        with (directory / filename).open() as handle:
            records.extend(csv.DictReader(handle, delimiter=";"))
    deep = sorted(
        [r for r in records if r["M"] and r["Type"] not in ("Dup", "**")],
        key=lambda r: r["Name"],
    )
    for source, selected in [("bsc5p", bright), ("openngc", deep)]:
        if selected != snapshots[source]["rows"]:
            raise ValueError(f"Subset mismatch: {source}")
        print(f"{source}: reproduced {len(selected)} exact source rows")


if __name__ == "__main__":
    verify(Path(sys.argv[1]))
