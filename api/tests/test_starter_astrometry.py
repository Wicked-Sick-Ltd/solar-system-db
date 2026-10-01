"""Offline checks of the pinned frame evidence and exact source-row bindings."""

import copy
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from solar_db import starter_astrometry as evidence
from solar_db.starter_catalogues import load_starter_catalogues

ROOT = Path(__file__).resolve().parents[2] / "solar_db/data"


@pytest.fixture
def bindings(tmp_path, monkeypatch):
    records, sources = load_starter_catalogues()
    for record in records:
        record.pop("astrometry")
    for source in sources.values():
        source.pop("astrometry_evidence")
    shutil.copytree(ROOT / "starter/astrometry", tmp_path / "starter/astrometry")
    monkeypatch.setattr(evidence, "files", lambda _: tmp_path)
    return records, sources, tmp_path / "starter/astrometry"


def revise(root, source, change):
    """Re-pin a deliberately altered test fixture to exercise semantic checks."""
    manifest = json.loads((root / "manifest.json").read_text())
    path = root / manifest[source]["file"]
    xml = ET.fromstring(path.read_bytes())
    change(xml)
    data = ET.tostring(xml)
    path.write_bytes(data)
    manifest[source]["response_sha256"] = hashlib.sha256(data).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_exact_verified_coverage_and_no_propagation():
    records, sources = load_starter_catalogues()
    assert Counter(r["astrometry"]["frame"] for r in records) == {
        "FK5": 50,
        "ICRS": 107,
        None: 1,
    }
    assert all(r["astrometry"]["coordinates_propagated"] is False for r in records)
    assert all(r["astrometry"]["observation_epoch_jyear"] is None for r in records)
    sirius = next(r for r in records if r["id"] == "bsc5p:hr2491")
    assert sirius["astrometry"]["pm_ra_cosdec_arcsec_per_year"] == -0.553
    assert sirius["astrometry"]["pm_dec_arcsec_per_year"] == -1.205
    assert sirius["astrometry"]["motion_model"] == "linear_angular_proper_motion"
    assert sirius["astrometry"]["equinox"] == "J2000.0"
    assert sirius["astrometry"]["reference_epoch_jyear"] == 2000.0
    m31 = next(r for r in records if r["id"] == "openngc:NGC0224")
    assert m31["astrometry"]["motion_model"] == "static_catalogue_direction"
    assert m31["astrometry"]["equinox"] is None
    assert m31["astrometry"]["pm_ra_cosdec_arcsec_per_year"] is None
    m45 = next(r for r in records if r["id"] == "openngc:Mel022")
    assert m45["astrometry"]["status"] == "unsupported"
    assert m45["astrometry"]["reference_epoch_jyear"] is None
    assert m45["astrometry"]["motion_model"] is None
    assert sources["openngc"]["astrometry_evidence"]["unsupported_identifiers"] == [
        "Mel022"
    ]


def test_source_strings_and_coordinates_are_never_replaced(bindings):
    records, sources, _ = bindings
    original = [(r["source_data"].copy(), r["ra_deg"], r["dec_deg"]) for r in records]
    evidence.attach_astrometry(records, sources)
    assert [(r["source_data"], r["ra_deg"], r["dec_deg"]) for r in records] == original


@pytest.mark.parametrize("mutation", ["id", "coordinate", "proper_motion", "hash"])
def test_mismatches_fail_without_partial_assignment(bindings, mutation):
    records, sources, root = bindings
    if mutation == "hash":
        path = root / "gavo-openngc.xml"
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "id":
        records[-1]["source_data"]["Name"] = "not-the-same-id"
    elif mutation == "coordinate":
        records[-1]["ra_deg"] += 0.001
    else:
        records[0]["source_data"]["pmra"] = "99"
    before = copy.deepcopy((records, sources))
    with pytest.raises(ValueError):
        evidence.attach_astrometry(records, sources)
    assert (records, sources) == before


@pytest.mark.parametrize(
    "field,attribute,value",
    [
        ("RAJ2000", "ref", "missing"),
        ("RAJ2000", "xtype", "dms"),
        ("pmRA", "unit", "mas/yr"),
        ("pmDE", "ref", "missing"),
    ],
)
def test_field_frame_and_units_must_be_explicit(bindings, field, attribute, value):
    records, sources, root = bindings
    revise(
        root,
        "bsc5p",
        lambda xml: xml.find(f'.//v:FIELD[@name="{field}"]', evidence.NS).set(
            attribute, value
        ),
    )
    with pytest.raises(ValueError):
        evidence.attach_astrometry(records, sources)


@pytest.mark.parametrize(
    "source,attribute,value",
    [
        ("bsc5p", "system", "ICRS"),
        ("bsc5p", "epoch", "1950.0"),
        ("bsc5p", "equinox", "J1950"),
        ("openngc", "system", "eq_FK5"),
        ("openngc", "epoch", "J2016.0"),
    ],
)
def test_changed_frame_or_epoch_is_rejected(bindings, source, attribute, value):
    records, sources, root = bindings
    revise(
        root,
        source,
        lambda xml: xml.find(".//v:COOSYS", evidence.NS).set(attribute, value),
    )
    with pytest.raises(ValueError):
        evidence.attach_astrometry(records, sources)


def test_duplicate_identity_is_rejected(bindings):
    records, sources, root = bindings

    def duplicate(xml):
        rows = xml.findall(".//v:TR", evidence.NS)
        rows[1][0].text = rows[0][0].text

    revise(root, "openngc", duplicate)
    with pytest.raises(ValueError, match="identities"):
        evidence.attach_astrometry(records, sources)


@pytest.mark.parametrize(
    "raw,expected,model",
    [
        ("0", 0.0, "linear_angular_proper_motion"),
        ("", None, "static_catalogue_direction"),
    ],
)
def test_zero_and_missing_proper_motion_remain_distinct(bindings, raw, expected, model):
    records, sources, root = bindings
    record = next(r for r in records if r["id"] == "bsc5p:hr2491")
    record["source_data"]["pmra"] = raw

    def change(xml):
        rows = xml.findall(".//v:TR", evidence.NS)
        next(row for row in rows if row[0].text == "2491")[3].text = raw

    revise(root, "bsc5p", change)
    evidence.attach_astrometry(records, sources)
    assert record["astrometry"]["pm_ra_cosdec_arcsec_per_year"] == expected
    assert record["astrometry"]["motion_model"] == model
