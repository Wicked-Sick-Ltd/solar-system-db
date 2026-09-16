"""Fleet-board notification for the nightly builder (build/mcp_notify.py)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "build"))

import mcp_notify


def test_format_board_message():
    s = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
        "enrichment_coverage": {
            "tier1_total": 39000, "tier1_fresh_within_7d": 4680, "tier1_fresh_fraction": 0.12,
            "tier2_total": 1529429, "tier2_enriched": 0, "tier2_enriched_fraction": 0.0,
        },
        "sha256": "deadbeef",
        "url": "https://s3.wickedsick.com/solar-system-db/solar_system-20260917.sqlite.zst",
    }
    subject, body = mcp_notify.format_board_message(s)
    assert subject == "solar-system-db nightly: 1,568,429 objects, 658 MB, 2026-09-17"
    assert "tier1_fresh_fraction=12%" in body
    assert "tier1_fresh_within_7d=4,680" in body
    assert "tier2_enriched_fraction=0%" in body
    assert "solar_system-20260917.sqlite.zst" in body
    assert "asteroid" in body and "1,564,353" in body
    assert "comet" in body and "4,076" in body
    assert s["url"] in body


def test_main_prints_when_coordctl_missing(tmp_path, monkeypatch, capsys):
    summary = {
        "artefact": "solar_system-20260917.sqlite.zst",
        "size_bytes": 658_000_000,
        "built_at": "2026-09-17T03:31:00Z",
        "counts_by_type": {"asteroid": 1564353, "comet": 4076},
        "enrichment_coverage": {"tier1_fresh_fraction": 0.12},
    }
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text(json.dumps(summary))
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(tmp_path / "no-such-coordctl.py"))
    rc = mcp_notify.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "solar-system-db nightly: 1,568,429 objects, 658 MB, 2026-09-17" in out


def test_main_returns_ok_on_unreadable_summary(tmp_path, monkeypatch, capsys):
    summary_path = tmp_path / "last-publish.json"
    summary_path.write_text("{not valid json,,,")
    monkeypatch.setenv("SOLAR_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COORDCTL_PATH", str(tmp_path / "no-such-coordctl.py"))
    rc = mcp_notify.main()
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""
