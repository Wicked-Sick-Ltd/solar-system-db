import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from ingest_showers import parse_legend, parse_showers  # noqa: E402

FIX = (ROOT / "tests" / "fixtures" / "mdc_showers.txt").read_bytes().decode("utf-8", errors="replace")


def test_legend_names_established_and_working():
    legend = parse_legend([l for l in FIX.splitlines() if l.startswith(":") or l.startswith("#")])
    labels = " ".join(legend.values()).lower()
    assert "established" in labels and "working" in labels


def test_parse_geminids():
    rows = parse_showers(FIX)
    gem = [r for r in rows if r["code"] == "GEM"]
    assert gem and gem[0]["iau_no"] == 4 and gem[0]["name"].lower().startswith("geminids")
    assert 250 < gem[0]["solar_longitude_deg"] < 265 and 30 < gem[0]["vg_km_s"] < 40
    assert "Phaethon" in gem[0]["parent_body"]


def test_multiple_parameter_sets_keep_distinct_ad_no():
    rows = parse_showers(FIX)
    keys = [(r["iau_no"], r["ad_no"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert any(sum(1 for k in keys if k[0] == iau) > 1 for iau, _ in keys)


def test_empty_fields_become_none():
    rows = parse_showers(FIX)
    assert any(r["parent_body"] is None for r in rows)
    assert all(isinstance(r["status_code"], int) for r in rows)
